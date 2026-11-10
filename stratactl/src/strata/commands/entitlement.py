"""`stratactl entitlement resolve` — resolve licensed artifact metadata WITHOUT CodeBuild.

For customers who deploy through their own CI/CD. Runs the exact same account-scoped,
SigV4-signed StrataHQ entitlement check the installer uses, and writes the returned
metadata — artifact_url, sha256, signature, version, expires_at — to a file.

The artifact_url is a short-lived (default 15 min) presigned bearer URL: treat it as a
credential. It is written only to the file you name, never to stdout or logs, and the file
is created with 0600 permissions. Do not commit it.

This does NOT launch CodeBuild, bootstrap StrataCI, or require any in-account install plane.
It reuses the existing StrataHQ client and configuration provider; it introduces no new
entitlement API or licensing mechanism.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from strata import hq
from strata.aws import session as aws_session
from strata.errors import ConfigError


def resolve_entitlement(settings, args):
    """Shared helper (used by `entitlement resolve` and `fetch-bundle`): make a session,
    determine the caller's verified account, and run the StrataHQ entitlement check for the
    requested module/version. Raises EntitlementDenied on a hard 'no' (→ nonzero exit)."""
    sess = aws_session.make_session(settings)
    account = aws_session.account_id(sess)
    module_id = getattr(args, "module", None) or "stratabi"
    version = getattr(args, "version", None) or "latest"
    ent = hq.check_entitlement(
        settings.hq, customer_id=account, module_id=module_id, version=version,
        session=sess, region=settings.hq.region or settings.region,
    )
    return account, module_id, ent


def write_secure_json(path: Path, payload: dict) -> None:
    """Write JSON with 0600 perms from creation (no world-readable window). Sensitive
    metadata (presigned URL, signature) must never land in a default-readable file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    try:  # best-effort on platforms without POSIX modes (Windows)
        os.chmod(path, 0o600)
    except OSError:
        pass


def run(settings, args) -> int:
    action = getattr(args, "ent_action", None)
    if action != "resolve":
        raise ConfigError("usage: stratactl entitlement resolve --module <id> --output <file>")

    out = getattr(args, "output", None)
    if not out:
        # Require an explicit destination: the metadata holds a presigned bearer URL, so we
        # never dump it to the terminal where it could land in shell history or CI logs.
        raise ConfigError(
            "entitlement resolve requires --output <file>; the metadata contains a "
            "short-lived presigned URL and is never printed to the terminal."
        )

    account, module_id, ent = resolve_entitlement(settings, args)
    write_secure_json(Path(out).expanduser(), {
        "module_id": module_id,
        "version": ent.version,
        "artifact_url": ent.artifact_url,
        "sha256": ent.sha256,
        "signature": ent.signature,
        "expires_at": ent.expires_at,
    })

    # Confirm only non-sensitive facts — never the URL or signature.
    print(f"entitled: {module_id}@{ent.version} (account {account})")
    print(
        f"  metadata written to {out} (mode 0600) — contains a presigned URL valid until "
        f"{ent.expires_at or 'its TTL (default 15 min)'}. Treat it as a secret; do not commit it."
    )
    return 0
