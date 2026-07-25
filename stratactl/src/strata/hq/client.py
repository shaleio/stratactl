"""StrataHQ control-plane client.

Single responsibility: ask HQ whether the customer is entitled to a module/version
and, if so, get a short-lived presigned URL + expected sha256 for the artifact.
HQ is request/response only; it never receives customer-account credentials.

The request is **SigV4-signed** with the caller's AWS credentials (the entitlement
endpoint is IAM-authorized), so HQ learns the caller's *verified* AWS account from
the signature rather than a client-asserted field — a license token can only be
redeemed from the account it was issued for. The license token rides in the
X-Strata-Token header (Authorization is reserved for the SigV4 signature).

Transport + signing both use botocore (a dependency via boto3): the signed
AWSRequest is sent as-is, so the bytes/headers on the wire match the signature.
"""

from __future__ import annotations

import json
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


def _send(session, url: str, payload: bytes, hq: HQConfig,
          region: Optional[str], timeout: int):
    """Sign (SigV4 execute-api, when a session is given) and send the POST via
    botocore's own HTTP session, so the exact bytes + headers that were signed are
    what goes on the wire.

    This matters: copying the signed headers into a fresh urllib request re-processes
    the body/headers (Content-Type, Host, header casing) in ways that can invalidate
    a SigV4 signature — API Gateway then rejects it with a 403 before the Lambda runs.
    Sending the prepared AWSRequest avoids that. Region must be the API's region (a
    custom domain still signs against the API's region). Returns (status, body_bytes).
    """
    try:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.httpsession import URLLib3Session
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ConfigError("botocore is required to call StrataHQ") from exc

    headers = {"Content-Type": "application/json"}
    if hq.token:
        headers["X-Strata-Token"] = hq.token  # included in the signature below

    aws_req = AWSRequest(method="POST", url=url, data=payload, headers=headers)
    if session is not None:
        creds = session.get_credentials()
        if creds is None:
            raise ConfigError("no AWS credentials available to sign the StrataHQ request")
        sign_region = region or hq.region or "us-east-1"
        SigV4Auth(creds.get_frozen_credentials(), SIGV4_SERVICE, sign_region).add_auth(aws_req)

    try:
        resp = URLLib3Session(timeout=timeout).send(aws_req.prepare())
    except Exception as exc:  # transport error
        raise StrataError(f"could not reach StrataHQ at {url}: {exc}") from exc
    return resp.status_code, (resp.content or b"{}")


def check_entitlement(hq: HQConfig, customer_id: str, module_id: str,
                      version: str, *, session=None, region: Optional[str] = None,
                      timeout: int = 15, action: Optional[str] = None) -> EntitlementResult:
    """POST /entitlement to StrataHQ. Raises EntitlementDenied on a hard 'no'.

    action="destroy" asks HQ for the retained artifact so a teardown can proceed
    even if the grant has lapsed or the version is retired (destroy is always
    permitted). Omit for install/update, which require an active grant."""
    if not hq.endpoint:
        raise ConfigError("StrataHQ endpoint is not configured.")
    if not hq.token:
        raise ConfigError(
            "No StrataHQ license token. Set STRATA_HQ_TOKEN, add it to your "
            "config file, or store it in SSM (/strataci/hq/token)."
        )

    url = hq.endpoint.rstrip("/") + "/entitlement"
    body = {
        "customer_id": customer_id,
        "module_id": module_id,
        "version": version,
    }
    if action:
        body["action"] = action
    payload = json.dumps(body).encode("utf-8")

    status, raw = _send(session, url, payload, hq, region, timeout)
    if status in (401, 403):
        raise EntitlementDenied(
            f"StrataHQ denied {module_id}@{version} (HTTP {status}). "
            "Check that you are signing from the licensed AWS account and that "
            "the license token is valid."
        )
    if status >= 400:
        detail = raw[:300].decode("utf-8", "replace")
        raise StrataError(f"StrataHQ error (HTTP {status}): {detail}")
    try:
        resp_body = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise StrataError("StrataHQ returned a non-JSON response.") from exc

    result = EntitlementResult(
        allow=bool(resp_body.get("allow")),
        module_id=module_id,
        version=resp_body.get("version", version),
        artifact_url=resp_body.get("artifact_url"),
        sha256=resp_body.get("sha256"),
        signature=resp_body.get("signature"),
        expires_at=resp_body.get("expires_at"),
        reason=resp_body.get("reason"),
    )
    if not result.allow:
        raise EntitlementDenied(result.reason or f"not entitled to {module_id}@{version}")
    return result
