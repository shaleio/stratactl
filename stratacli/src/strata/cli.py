"""StrataCLI entry point. `stratacli <command> ...`

Resolves configuration once (strata.config.provider) and dispatches to the
command modules. No tofu runs locally on the normal path — the work happens in
the ephemeral CodeBuild runner inside the target account.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from strata import __version__
from strata.config.provider import load_settings
from strata.errors import StrataError


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stratacli", description="StrataBI module installer/orchestrator.")
    p.add_argument("--version", action="version", version=f"stratacli {__version__}")

    # Global overrides (highest-precedence config layer).
    p.add_argument("--profile", help="AWS profile for the target account.")
    p.add_argument("--region", help="Target-account AWS region.")
    p.add_argument("--source", choices=["stratahq", "customer_s3"], help="Artifact source.")
    p.add_argument("--hq-endpoint", dest="hq_endpoint", help="Override StrataHQ entitlement endpoint.")
    p.add_argument("--hq-bucket", dest="hq_bucket", help="Override StrataHQ artifact bucket.")
    p.add_argument("--hq-token", dest="hq_token", help="StrataHQ license token.")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("install", help="Install a module.")
    sp.add_argument("module", help="Module id (or 'source/module', or an s3:// URI).")
    sp.add_argument("--env", default="dev", help="Target environment label (maps to profile/conventions).")
    sp.add_argument("--to", dest="version", default="latest", help="Module version (default: latest).")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation; auto-approve apply.")

    sp = sub.add_parser("update", help="Update a module to a new version.")
    sp.add_argument("module")
    sp.add_argument("--env", default="dev")
    sp.add_argument("--to", dest="version", default="latest")
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("uninstall", help="Destroy/uninstall a module.")
    sp.add_argument("module")
    sp.add_argument("--env", default="dev")
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("install-stratabi", help="Install commercial StrataBI Enterprise (licensed).")
    sp.add_argument("--env", default="dev")
    sp.add_argument("--to", dest="version", default="latest")
    sp.add_argument("--yes", action="store_true")

    sp = sub.add_parser("install-stratabi-dev", help="Install StrataBI Developer/Community edition.")
    sp.add_argument("--env", default="dev")
    sp.add_argument("--to", dest="version", default="latest")
    sp.add_argument("--yes", action="store_true")

    sub.add_parser("list", help="List installed modules.")

    sp = sub.add_parser("logs", help="Show/tail logs for a job.")
    sp.add_argument("job_id")

    ip = sub.add_parser("init", help="Installation wizard: collect config + generate Terraform vars.")
    ip.add_argument("what", nargs="?", default="stratabi", choices=["stratabi", "module"],
                    help="'stratabi' (default) or 'module'.")
    ip.add_argument("module_id", nargs="?", help="Module id when initializing a module.")
    ip.add_argument("--name", help="Installation name (Terraform name_prefix).")
    ip.add_argument("--config", help="Path to the installation config (default: ./strata.yaml).")
    ip.add_argument("--out", help="Directory to write generated tfvars (default: ./infra if present).")
    ip.add_argument("--schema", help="Explicit module install-schema JSON (module init).")
    ip.add_argument("--update", action="store_true", help="Re-prompt for all values, not just missing ones.")
    ip.add_argument("--non-interactive", dest="non_interactive", action="store_true",
                    help="Don't prompt; use saved config + defaults (fails if a required value is missing).")
    ip.add_argument("--show-config", dest="show_config", action="store_true",
                    help="Print the resolved StrataCLI settings instead of running the wizard.")

    sp = sub.add_parser("fetch-runtime", help="Fetch the account-entitled StrataBI runtime artifact for an image build.")
    sp.add_argument("--version", default="latest")
    sp.add_argument("--dir", help="Output dir (docker build context); default from config.")

    # dashboards: git <-> S3 sync for git-registered StrataBI dashboards
    dboards = sub.add_parser("dashboards", help="Sync git-registered dashboards with S3.")
    dsub = dboards.add_subparsers(dest="dash_action", required=True)
    dpull = dsub.add_parser("pull", help="Download the git-registered dashboards into the local dir.")
    dsub.add_parser("list", help="List the git-registered dashboards.")
    ddiff = dsub.add_parser("diff", help="Show which local dashboards differ from S3.")
    dpush = dsub.add_parser("push", help="Upload local dashboards to S3 (overwrite in place).")
    dpush.add_argument("files", nargs="*", help="Dashboard JSON files (default: all in the manifest).")
    dpush.add_argument("--new", action="store_true", help="Mint a fresh artifact instead of overwriting.")
    dpush.add_argument("--git-registered", action="store_true", help="Register the artifact in the git table.")
    dpush.add_argument("--pinned", action="store_true", help="Also pin the dashboard.")
    dpush.add_argument("--force", action="store_true", help="Overwrite even if S3 changed since pull.")
    for _sp in (dpull, ddiff, dpush):
        _sp.add_argument("--dir", help="Local dashboards directory (default from config).")

    return p


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Map CLI flags into the canonical config-override shape (layer 1)."""
    o: dict[str, Any] = {"hq": {}}
    if args.profile:
        o["profile"] = args.profile
    if args.region:
        o["region"] = args.region
    if args.source:
        o["source"] = args.source
    if args.hq_endpoint:
        o["hq"]["endpoint"] = args.hq_endpoint
    if args.hq_bucket:
        o["hq"]["artifact_bucket"] = args.hq_bucket
    if args.hq_token:
        o["hq"]["token"] = args.hq_token
    if not o["hq"]:
        o.pop("hq")
    return o


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        settings = load_settings(_overrides(args))
        # Import lazily so a bad command doesn't pull in boto3-heavy paths.
        from strata import commands

        dispatch = {
            "install": lambda: commands.install.run(settings, args),
            "install-stratabi": lambda: commands.platform.run_enterprise(settings, args),
            "install-stratabi-dev": lambda: commands.platform.run_dev(settings, args),
            "update": lambda: commands.update.run(settings, args),
            "uninstall": lambda: commands.uninstall.run(settings, args),
            "list": lambda: commands.list.run(settings, args),
            "logs": lambda: commands.logs.run(settings, args),
            "init": lambda: commands.init.run(settings, args),
            "dashboards": lambda: commands.dashboards.run(settings, args),
            "fetch-runtime": lambda: commands.fetch_runtime.run(settings, args),
        }
        return int(dispatch[args.command]() or 0)
    except StrataError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\naborted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
