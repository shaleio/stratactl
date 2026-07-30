"""`stratactl dashboards <pull|push|list|diff>` — git <-> S3 sync for the
git-registered StrataBI dashboards. Direct S3/DynamoDB with operator creds (no
runner needed). See strata.dashboards.sync."""

from __future__ import annotations

from pathlib import Path

from strata.aws import session as aws_session
from strata.dashboards import sync
from strata.errors import ConfigError


def _resolve_from_install(settings, d):
    """Fill dashboards.bucket / git_registry_table from the recorded StrataBI Dev
    install's tofu outputs (system_bucket, git_registry_table), so `dashboards`
    is zero-config after `stratactl dev install`. Best-effort — any failure leaves
    the explicit config untouched."""
    if d.bucket and d.git_registry_table:
        return d
    try:
        from strata.commands.dev import _record
        _, rec = _record(settings)
    except Exception:
        rec = None
    outputs = (rec or {}).get("outputs") or {}

    def _out(key):
        v = outputs.get(key)
        return (v.get("value") if isinstance(v, dict) else v) or ""

    if not d.bucket:
        d.bucket = _out("system_bucket")
    if not d.git_registry_table:
        d.git_registry_table = _out("git_registry_table")
    return d


def _cfg(settings, *, need_registry: bool):
    d = _resolve_from_install(settings, settings.dashboards)
    if not d.bucket:
        raise ConfigError(
            "Could not resolve the dashboards bucket.\n"
            "  fix: run `stratactl dev install` first (it's read from the tofu outputs),\n"
            "       or set it explicitly (STRATA_DASH_BUCKET / [dashboards] bucket)."
        )
    if need_registry and not d.git_registry_table:
        raise ConfigError(
            "This action needs the dashboard git-registry table.\n"
            "  fix: run `stratactl dev install` (it provisions one), or set "
            "STRATA_DASH_GIT_TABLE / [dashboards] git_registry_table.\n"
            "  tip: a plain `dashboards push <file>` (no --new/--git-registered) "
            "needs only the bucket."
        )
    return d


def run(settings, args) -> int:
    action = args.dash_action
    # Only the git-sync actions need the registry table; a plain push needs just
    # the bucket, so the generic funnel works even without git-registry provisioned.
    need_registry = action in ("list", "pull", "diff") or (
        action == "push" and (getattr(args, "new", False) or getattr(args, "git_registered", False)))
    d = _cfg(settings, need_registry=need_registry)
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
