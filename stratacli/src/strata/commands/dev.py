"""`stratacli dev ...` — Developer Edition lifecycle (no StrataHQ contract).

Thin, coherent surface over the shared install path. Developer Edition never
contacts StrataHQ: `dev install` resolves its artifact from a public URL, your own
S3, or the conventional customer_s3 key (see commands/platform.run_dev).

    stratacli dev install [--source <url|s3://…|customer_s3>] [--to <version>]
    stratacli dev status
    stratacli dev outputs
    stratacli dev logs <job_id>
    stratacli dev uninstall [--yes]
    stratacli dev configure-local [--out .env]   # write non-secret local config
"""

from __future__ import annotations

import argparse

from strata.aws import session as aws_session
from strata.commands import _common, logs as _logs, platform
from strata.errors import StrataError
from strata.registry import installs

DEV_MODULE = platform.DEV_MODULE  # "stratabi_dev"

# Non-secret STRATABI_* keys that a local `stratabi-dev` runtime needs. Values come
# from the install record's recorded tofu outputs. AWS *secrets* are never written.
_LOCAL_KEYS = [
    "STRATABI_SYSTEM_BUCKET", "STRATABI_DASHBOARD_PREFIX", "STRATABI_MODULE_PREFIX",
    "STRATABI_THEME_PREFIX", "STRATABI_RESULT_PREFIX", "STRATABI_ATHENA_OUTPUT",
    "STRATABI_CATALOG_DATABASE",
]


def _record(settings):
    sess = aws_session.make_session(settings)
    return sess, installs.get(sess, settings.customer.install_table, DEV_MODULE)


def run(settings, args) -> int:
    action = getattr(args, "dev_action", None)

    if action == "install":
        return platform.run_dev(settings, args)

    if action == "uninstall":
        return _common.launch(settings, action="uninstall", module_spec=DEV_MODULE,
                              version="installed", cli_source="customer_s3")

    if action == "demo":
        return _demo(settings, args)

    if action == "logs":
        return _logs.run(settings, args)

    if action in ("status", "outputs"):
        _, rec = _record(settings)
        if not rec or rec.get("status") == installs.REMOVED:
            print("StrataBI Dev is not installed in this account.")
            return 0
        if action == "status":
            print(f"module   : {DEV_MODULE}")
            print(f"version  : {rec.get('version', '-')}")
            print(f"status   : {rec.get('status', '-')}")
            print(f"delivery : {rec.get('delivery', '-')}")
            print(f"last job : {rec.get('last_job', '-')}")
            return 0
        outputs = rec.get("outputs") or {}
        if not outputs:
            print("(no tofu outputs recorded for this install)")
            return 0
        for k in sorted(outputs):
            print(f"{k} = {outputs[k]}")
        return 0

    if action == "configure-local":
        _, rec = _record(settings)
        if not rec or rec.get("status") == installs.REMOVED:
            raise StrataError("StrataBI Dev is not installed; run `stratacli dev install` first.")
        outputs = rec.get("outputs") or {}
        lines = [f"AWS_REGION={settings.region}"]
        if settings.profile:
            lines.append(f"AWS_PROFILE={settings.profile}")
        for k in _LOCAL_KEYS:
            if k in outputs:
                lines.append(f"{k}={outputs[k]}")
        body = "# StrataBI Dev — non-secret local config (written by `stratacli dev " \
               "configure-local`).\n# AWS credentials are NOT written here; use your " \
               "profile / provider chain.\n" + "\n".join(lines) + "\n"
        out = getattr(args, "out", None) or ".env"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"wrote {len([l for l in lines])} settings to {out} (no secrets).")
        return 0

    raise StrataError(f"unknown dev action: {action}")


def _demo(settings, args) -> int:
    """ForgeWorks AWS demo lifecycle. Delegates to the `stratabi-dev` CLI (which owns
    the demo logic) so StrataCLI does not import the SGCL-licensed Dev Edition."""
    import shutil
    import subprocess
    if not shutil.which("stratabi-dev"):
        raise StrataError(
            "ForgeWorks demo requires the Developer Edition. Install it:\n"
            "    pipx install stratabi-dev\n"
            "then: stratacli dev demo install")
    sub = {"install": "install", "remove": "remove", "status": "status"}[args.demo_action]
    cmd = ["stratabi-dev", "demo", "aws", sub]
    if getattr(args, "profile", None) or settings.profile:
        cmd += ["--profile", args.profile or settings.profile]
    region = getattr(args, "region", None) or settings.region
    if region:
        cmd += ["--region", region]
    if getattr(args, "yes", False):
        cmd += ["--yes"]
    return subprocess.run(cmd).returncode


def add_parser(sub: "argparse._SubParsersAction") -> None:
    dev = sub.add_parser("dev", help="StrataBI Developer Edition lifecycle (no StrataHQ contract).")
    dsub = dev.add_subparsers(dest="dev_action", required=True)

    demo = dsub.add_parser("demo", help="ForgeWorks AWS demo (Athena) lifecycle.")
    demo_sub = demo.add_subparsers(dest="demo_action", required=True)
    for _act in ("install", "remove", "status"):
        _dp = demo_sub.add_parser(_act)
        _dp.add_argument("--profile", default=None)
        _dp.add_argument("--region", default=None)
        _dp.add_argument("--yes", action="store_true")

    di = dsub.add_parser("install", help="Deploy the Dev data plane (HQ-free).")
    di.add_argument("--to", dest="version", default="latest")
    di.add_argument("--source", default=None,
                    help="Public URL (https://…/stratabi_dev.zip), s3:// URI, or 'customer_s3'.")
    di.add_argument("--yes", action="store_true")

    dsub.add_parser("status", help="Show the installed Dev module status.")
    dsub.add_parser("outputs", help="Print recorded tofu outputs for the Dev install.")

    dl = dsub.add_parser("logs", help="Show/tail logs for a Dev job.")
    dl.add_argument("job_id")

    du = dsub.add_parser("uninstall", help="Destroy the Dev data plane.")
    du.add_argument("--yes", action="store_true")

    dc = dsub.add_parser("configure-local",
                         help="Write non-secret local config (.env) for the stratabi-dev app.")
    dc.add_argument("--out", default=".env", help="Output path (default: .env).")
