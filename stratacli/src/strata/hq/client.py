"""StrataHQ control-plane client.

Single responsibility: ask HQ whether the customer is entitled to a module/version
and, if so, get a short-lived presigned URL + expected sha256 for the artifact.
HQ is request/response only; it never receives customer-account credentials.

Uses urllib (stdlib) to avoid a `requests` dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from strata.config.models import HQConfig
from strata.errors import ConfigError, EntitlementDenied, StrataError


@dataclass
class EntitlementResult:
    allow: bool
    module_id: str
    version: str
    artifact_url: Optional[str] = None   # short-lived presigned GET URL
    sha256: Optional[str] = None         # expected bundle hash
    signature: Optional[str] = None      # KMS signature over the sha256 (base64)
    expires_at: Optional[str] = None
    reason: Optional[str] = None


def check_entitlement(hq: HQConfig, customer_id: str, module_id: str,
                      version: str, timeout: int = 15) -> EntitlementResult:
    """POST /entitlement to StrataHQ. Raises EntitlementDenied on a hard 'no'."""
    if not hq.endpoint:
        raise ConfigError("StrataHQ endpoint is not configured.")
    if not hq.token:
        raise ConfigError(
            "No StrataHQ license token. Set STRATA_HQ_TOKEN, add it to your "
            "config file, or store it in SSM (/strataci/hq/token)."
        )

    url = hq.endpoint.rstrip("/") + "/entitlement"
    payload = json.dumps({
        "customer_id": customer_id,
        "module_id": module_id,
        "version": version,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {hq.token}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise EntitlementDenied(
                f"StrataHQ denied {module_id}@{version} (HTTP {exc.code})."
            ) from exc
        raise StrataError(f"StrataHQ error (HTTP {exc.code}): {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise StrataError(f"could not reach StrataHQ at {url}: {exc.reason}") from exc

    result = EntitlementResult(
        allow=bool(body.get("allow")),
        module_id=module_id,
        version=body.get("version", version),
        artifact_url=body.get("artifact_url"),
        sha256=body.get("sha256"),
        signature=body.get("signature"),
        expires_at=body.get("expires_at"),
        reason=body.get("reason"),
    )
    if not result.allow:
        raise EntitlementDenied(result.reason or f"not entitled to {module_id}@{version}")
    return result
