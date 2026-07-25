"""`stratactl fetch-runtime` — the licensing seam.

Authenticates to StrataHQ, checks the entitlement for THIS account + version,
pulls the signed StrataBI runtime artifact, verifies it, and writes it into the
docker build context (dist/). `docker build` then installs it via
--build-arg RUNTIME_WHEEL=<file>. No valid account entitlement => no artifact.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from strata import hq
from strata.artifacts import bundle
from strata.aws import session as aws_session
from strata.errors import StrataError


def run(settings, args) -> int:
    sess = aws_session.make_session(settings)
    account = aws_session.account_id(sess)
    version = getattr(args, "version", None) or "latest"
    module_id = settings.runtime.module_id

    # account-scoped entitlement check (raises EntitlementDenied if not licensed).
    # SigV4-signed so HQ verifies the caller account from the signature.
    ent = hq.check_entitlement(settings.hq, customer_id=account,
                               module_id=module_id, version=version,
                               session=sess, region=settings.hq.region or settings.region)

    dist = Path(getattr(args, "dir", None) or settings.runtime.dist_dir)
    dist.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(ent.artifact_url or "").path).name or f"{module_id}-{ent.version}.whl"
    dest = dist / name

    bundle.download_url(ent.artifact_url, dest)
    actual = bundle.verify_sha256(dest, ent.sha256)
    if ent.signature and settings.hq.public_key:
        bundle.verify_signature(actual, ent.signature, settings.hq.public_key)
        verified = "sha256 + signature"
    elif settings.hq.require_signature:
        raise StrataError("runtime signature required but no signature/public key available "
                          "(set STRATA_HQ_PUBKEY).")
    else:
        verified = "sha256"

    print(f"runtime {module_id}@{ent.version} -> {dest}  ({verified} verified, account {account})")
    print(f"  build:  docker build --build-arg RUNTIME_WHEEL={dest.as_posix()} -t <image> .")
    print(f"  or:     RUNTIME_WHEEL={dest.as_posix()} IMAGE=<image> ./build.sh")
    return 0
