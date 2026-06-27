"""StrataHQ control-plane client.

Single responsibility: ask HQ whether the customer is entitled to a module/version
and, if so, get a short-lived presigned URL + expected sha256 for the artifact.
HQ is request/response only; it never receives customer-account credentials.

The request is **SigV4-signed** with the caller's AWS credentials (the entitlement
endpoint is IAM-authorized), so HQ learns the caller's *verified* AWS account from
the signature rather than a client-asserted field — a license token can only be
redeemed from the account it was issued for. The license token rides in the
X-Strata-Token header (Authorization is reserved for the SigV4 signature).

Transport is urllib (stdlib); signing uses botocore (already a dependency via boto3).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from strata.config.models import HQConfig
from strata.errors import ConfigError, EntitlementDenied, StrataError

SIGV4_SERVICE = "execute-api"


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


def _signed_headers(session, region: str, url: str, payload: bytes,
                    token: Optional[str]) -> dict:
    """SigV4-sign the POST and return the headers to send. Region must be the API's
    region (not necessarily the request's apparent host — a custom domain still
    signs against the API's region)."""
    try:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ConfigError("botocore is required to sign StrataHQ requests") from exc

    creds = session.get_credentials()
    if creds is None:
        raise ConfigError("no AWS credentials available to sign the StrataHQ request")

    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Strata-Token"] = token  # signed below (included in the request)

    req = AWSRequest(method="POST", url=url, data=payload, headers=headers)
    SigV4Auth(creds.get_frozen_credentials(), SIGV4_SERVICE, region).add_auth(req)
    return dict(req.headers)


def check_entitlement(hq: HQConfig, customer_id: str, module_id: str,
                      version: str, *, session=None, region: Optional[str] = None,
                      timeout: int = 15) -> EntitlementResult:
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

    if session is not None:
        sign_region = region or hq.region or "us-east-1"
        headers = _signed_headers(session, sign_region, url, payload, hq.token)
    else:
        # Unsigned fallback (only works against a non-IAM-authed endpoint).
        headers = {"Content-Type": "application/json", "X-Strata-Token": hq.token}

    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise EntitlementDenied(
                f"StrataHQ denied {module_id}@{version} (HTTP {exc.code}). "
                "Check that you are signing from the licensed AWS account and that "
                "the license token is valid."
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
