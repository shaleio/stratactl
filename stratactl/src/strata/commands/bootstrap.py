"""`stratactl bootstrap` — stand StrataCI up inside a customer's AWS account.

This is the one command that runs OpenTofu *locally* (everything else runs in the
ephemeral CodeBuild runner). It:

  1. validates AWS credentials and shows account / profile / region;
  2. validates that OpenTofu (or Terraform) is available;
  3. extracts the version-matched, packaged bootstrap infra into a stable per-
     account working directory (platform-appropriate paths via platformdirs);
  4. runs `tofu init` + `tofu plan`;
  5. requires explicit confirmation (or --yes) before `tofu apply`.

SAFETY: by default this command is a DRY RUN — it validates and prints the plan it
*would* run and stops. It only shells out to tofu when --run is passed, and only
applies when --run AND (--yes or an interactive "yes"). It never applies silently.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from strata import bootstrap as _bootstrap_pkg
from strata.aws import session as aws_session
from strata.errors import StrataError

try:
    from platformdirs import user_state_dir
except Exception:  # pragma: no cover - platformdirs is a declared dependency
    user_state_dir = None  # type: ignore


def _tofu_bin() -> str | None:
    for exe in ("tofu", "terraform"):
        if shutil.which(exe):
            return exe
    return None


def _working_dir(account: str, region: str) -> Path:
    base = (Path(user_state_dir("stratactl")) if user_state_dir
            else Path.home() / ".local" / "state" / "stratactl")
    return base / "bootstrap" / f"{account}-{region}"


def _extract_infra(dest: Path) -> list[str]:
    """Copy the packaged, immutable bootstrap infra into the working dir."""
    dest.mkdir(parents=True, exist_ok=True)
    root = _bootstrap_pkg.bootstrap_infra_root()
    written = []
    for p in root.iterdir():
        if p.is_file():
            (dest / p.name).write_bytes(p.read_bytes())
            written.append(p.name)
    return sorted(written)


def run(settings, args) -> int:
    # 1) Credentials + identity.
    sess = aws_session.make_session(settings)
    try:
        ident = sess.client("sts").get_caller_identity()
    except Exception as exc:  # noqa: BLE001
        raise StrataError(
            f"could not validate AWS credentials: {exc}\n"
            "  fix: configure an AWS profile (--profile) or export credentials."
        ) from exc
    account = ident["Account"]
    region = settings.region
    print("StrataCI bootstrap")
    print(f"  account : {account}")
    print(f"  arn     : {ident.get('Arn', '?')}")
    print(f"  profile : {settings.profile or '(default provider chain)'}")
    print(f"  region  : {region}")

    # 2) OpenTofu / Terraform availability.
    tofu = _tofu_bin()
    print(f"  tofu    : {tofu or 'NOT FOUND — install OpenTofu 1.6+ or Terraform 1.6+'}")

    # 3) Extract version-matched infra into a stable working dir.
    wd = _working_dir(account, region)
    written = _extract_infra(wd)
    print(f"  workdir : {wd}")
    print(f"  infra   : {', '.join(written)}")

    # 4/5) Plan / apply — gated. Default is a dry run.
    if not getattr(args, "run", False):
        print("\nDry run (default). Nothing was applied. To proceed:")
        print(f"  stratactl bootstrap --profile {settings.profile or '<profile>'} "
              f"--region {region} --run           # init + plan")
        print(f"  stratactl bootstrap --profile {settings.profile or '<profile>'} "
              f"--region {region} --run --yes     # init + plan + apply")
        return 0

    if not tofu:
        raise StrataError("OpenTofu/Terraform not found on PATH; cannot --run.")

    subprocess.run([tofu, "init", "-input=false"], cwd=wd, check=True)
    subprocess.run([tofu, "plan", "-input=false", "-out=tfplan"], cwd=wd, check=True)

    if not getattr(args, "yes", False):
        resp = input("\nApply this StrataCI bootstrap plan? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("aborted — plan saved, nothing applied.")
            return 0

    subprocess.run([tofu, "apply", "-input=false", "tfplan"], cwd=wd, check=True)
    print("\nStrataCI bootstrap applied. Next: `stratactl dev install`.")
    # NOTE (follow-up): upload the installed CLI/runner wheel to the created bucket
    # and validate the CodeBuild runner. Tracked in PUBLISH_READINESS.md.
    return 0


def add_parser(sub: "argparse._SubParsersAction") -> None:
    sp = sub.add_parser("bootstrap", help="Stand StrataCI up inside a customer AWS account.")
    sp.add_argument("--run", action="store_true",
                    help="Actually run tofu init+plan (default: dry run).")
    sp.add_argument("--yes", action="store_true",
                    help="With --run, apply without an interactive confirmation.")
