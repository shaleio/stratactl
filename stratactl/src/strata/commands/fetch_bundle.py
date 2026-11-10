"""`stratactl fetch-bundle` — download + verify the licensed platform bundle WITHOUT CodeBuild.

For customers who deploy the OpenTofu bundle through their own CI/CD. Runs the account-scoped
StrataHQ entitlement check, downloads the signed `module.zip` over the short-lived presigned
URL, verifies its SHA-256 and — fail closed — its KMS signature against the pinned public key,
then writes the verified bundle to --output.

It reuses the exact verification the in-account runner uses (strata.artifacts.bundle); no
cryptographic logic is duplicated here. It does not launch CodeBuild or require StrataCI.

Verification policy (never a silent downgrade to hash-only):
  * signature returned + pinned key configured  -> verify signature (hard fail on mismatch)
  * require_signature set but can't verify       -> refuse, delete partial file
  * signature returned but no pinned key         -> refuse (configure STRATA_HQ_PUBKEY)
  * no signature + not required                  -> hash-only, stated explicitly
"""

from __future__ import annotations

from pathlib import Path

from strata.artifacts import bundle
from strata.commands.entitlement import resolve_entitlement, write_secure_json
from strata.errors import ConfigError, StrataError


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def run(settings, args) -> int:
    out = getattr(args, "output", None)
    if not out:
        raise ConfigError("fetch-bundle requires --output <file> (path for the verified module.zip).")
    dest = Path(out).expanduser()

    account, module_id, ent = resolve_entitlement(settings, args)
    if not ent.artifact_url:
        raise StrataError("StrataHQ returned no artifact URL for this entitlement.")

    dest.parent.mkdir(parents=True, exist_ok=True)
    bundle.download_url(ent.artifact_url, dest)
    actual = bundle.verify_sha256(dest, ent.sha256)

    if ent.signature and settings.hq.public_key:
        bundle.verify_signature(actual, ent.signature, settings.hq.public_key)
        verified = "sha256 + KMS signature"
    elif settings.hq.require_signature:
        _safe_unlink(dest)
        raise StrataError(
            "bundle signature verification required but unavailable (need both a server "
            "signature and a pinned public key via STRATA_HQ_PUBKEY). Refusing to write an "
            "unverified bundle."
        )
    elif ent.signature and not settings.hq.public_key:
        _safe_unlink(dest)
        raise StrataError(
            "StrataHQ returned a signature but no pinned public key is configured, so the "
            "signature cannot be verified. Set STRATA_HQ_PUBKEY to the pinned key; refusing to "
            "fall back to hash-only verification for the deployment bundle."
        )
    else:
        verified = "sha256 only (no signature available)"

    print(f"bundle {module_id}@{ent.version} -> {dest}  ({verified} verified; account {account})")
    print("  extract + deploy with your own OpenTofu pipeline (see docs: Bring your own CI/CD).")

    meta = getattr(args, "metadata", None)
    if meta:
        # Verification receipt — deliberately WITHOUT the presigned URL.
        write_secure_json(Path(meta).expanduser(), {
            "module_id": module_id,
            "version": ent.version,
            "sha256": actual,
            "signature": ent.signature,
            "verified": verified,
            "artifact_path": str(dest),
        })
        print(f"  verification metadata -> {meta}")
    return 0
