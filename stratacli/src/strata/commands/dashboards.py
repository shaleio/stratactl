"""`stratacli dashboards <pull|push|list|diff>` — git <-> S3 sync for the
git-registered StrataBI dashboards. Direct S3/DynamoDB with operator creds (no
runner needed). See strata.dashboards.sync."""

from __future__ import annotations

from pathlib import Path

from strata.aws import session as aws_session
from strata.dashboards import sync
from strata.errors import ConfigError


def _cfg(settings):
    d = settings.dashboards
    if not d.bucket or not d.git_registry_table:
        raise ConfigError(
            "dashboards.bucket and dashboards.git_registry_table must be set "
            "(config [dashboards] section, or STRATA_DASH_BUCKET / STRATA_DASH_GIT_TABLE)."
        )
    return d


def run(settings, args) -> int:
    d = _cfg(settings)
    sess = aws_session.make_session(settings)
    local_dir = Path(getattr(args, "dir", None) or d.local_dir)
    action = args.dash_action

    if action == "pull":
        written = sync.pull(sess, bucket=d.bucket, registry_table=d.git_registry_table,
                            local_dir=local_dir)
        print(f"pulled {len(written)} dashboard(s) -> {local_dir}/ (commit with git)")
        for slug in written:
            print(f"  {slug}.json")
        return 0

    if action == "push":
        files = [Path(p) for p in (getattr(args, "files", None) or [])]
        pushed = sync.push(
            sess, bucket=d.bucket, registry_table=d.git_registry_table,
            pinned_table=d.pinned_table or None, local_dir=local_dir, files=files,
            new=args.new, git_register=args.git_registered, pinned=args.pinned,
            prefix=d.prefix, force=args.force,
        )
        print(f"pushed {len(pushed)} dashboard(s):")
        for line in pushed:
            print(f"  {line}")
        return 0

    if action == "list":
        refs = sync.scan_registry(sess, d.git_registry_table)
        if not refs:
            print("no git-registered dashboards.")
            return 0
        for r in refs:
            print(f"  {r.slug:30}  {r.s3_key}")
        return 0

    if action == "diff":
        rows = sync.diff(sess, bucket=d.bucket, registry_table=d.git_registry_table,
                         local_dir=local_dir)
        if not rows:
            print("nothing to compare.")
            return 0
        for slug, status in rows:
            print(f"  {status:>14}  {slug}")
        return 0

    return 1
