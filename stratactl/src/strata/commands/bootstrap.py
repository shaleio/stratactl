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
import os
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


def _tofu_env(settings) -> dict:
    """Environment for the tofu subprocess so it targets the SAME account the CLI
    validated. The CLI's --profile only drives boto3; tofu reads AWS creds from its
    own environment, so without this it silently falls back to the default provider
    chain — deploying StrataCI into the wrong account while the CLI prints the right
    one. When a profile is given we also drop any static keys inherited from the
    environment, since those take precedence over AWS_PROFILE."""
    env = dict(os.environ)
    if settings.profile:
        env["AWS_PROFILE"] = settings.profile
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            env.pop(k, None)
    if settings.region:
        env["AWS_REGION"] = settings.region
        env["AWS_DEFAULT_REGION"] = settings.region
    return env


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

    if getattr(args, "destroy", False):
        return _destroy(settings, sess, account, region, tofu, wd)

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

    tenv = _tofu_env(settings)
    subprocess.run([tofu, "init", "-input=false"], cwd=wd, check=True, env=tenv)
    subprocess.run([tofu, "plan", "-input=false", "-out=tfplan"], cwd=wd, check=True, env=tenv)

    if not getattr(args, "yes", False):
        resp = input("\nApply this StrataCI bootstrap plan? [y/N] ").strip().lower()
        if resp not in ("y", "yes"):
            print("aborted — plan saved, nothing applied.")
            return 0

    subprocess.run([tofu, "apply", "-input=false", "tfplan"], cwd=wd, check=True, env=tenv)

    # Durability: StrataCI is applied with LOCAL state (you can't sit an S3 backend in
    # the very bucket you're about to destroy). Copy the state into the created bucket so
    # any machine can pull it back for `stratactl bootstrap destroy` later — even if this
    # local workdir is gone. Non-fatal: local state still works if this fails.
    try:
        bucket = subprocess.check_output(
            [tofu, "output", "-raw", "bucket"], cwd=wd, env=tenv, text=True).strip()
        state_file = wd / "terraform.tfstate"
        if bucket and state_file.is_file():
            sess.client("s3").put_object(
                Bucket=bucket, Key="bootstrap/terraform.tfstate",
                Body=state_file.read_bytes(), ContentType="application/json")
            print(f"  bootstrap state backed up → s3://{bucket}/bootstrap/terraform.tfstate")
    except Exception as exc:  # noqa: BLE001
        print(f"  (warning: could not back up bootstrap state to S3: {exc})")

    print("\nStrataCI bootstrap applied. Next: `stratactl dev install`.")
    # NOTE (follow-up): upload the installed CLI/runner wheel to the created bucket
    # and validate the CodeBuild runner. Tracked in PUBLISH_READINESS.md.
    return 0


_DESTROY_OVERRIDE = '''\
# Teardown-only override (see stratactl bootstrap --destroy). Disables prevent_destroy
# on the protected resources and force-empties the bucket so `tofu destroy` completes.
# Affects only this local teardown workdir; the packaged source infra keeps its safety.
resource "aws_s3_bucket" "strataci" {
  force_destroy = true
  lifecycle { prevent_destroy = false }
}
resource "aws_dynamodb_table" "install_registry" {
  lifecycle { prevent_destroy = false }
}
resource "aws_dynamodb_table" "jobs" {
  lifecycle { prevent_destroy = false }
}
resource "aws_dynamodb_table" "tf_locks" {
  lifecycle { prevent_destroy = false }
}
'''


def _destroy(settings, sess, account, region, tofu, wd) -> int:
    """Tear StrataCI down. Pull the durable state from S3 into the local workdir, then
    `tofu destroy` LOCALLY — state is local during destroy, so the state bucket can
    self-delete cleanly. A prevent_destroy/force_destroy override makes it completable."""
    if not tofu:
        raise StrataError("OpenTofu/Terraform not found on PATH; cannot destroy.")
    bucket = settings.resolved_bucket(account)   # strataci-<account>-<region>
    state_file = wd / "terraform.tfstate"

    # Pull the state `bootstrap` uploaded after install (durable, machine-independent).
    try:
        obj = sess.client("s3").get_object(Bucket=bucket, Key="bootstrap/terraform.tfstate")
        state_file.write_bytes(obj["Body"].read())
        print(f"  pulled state ← s3://{bucket}/bootstrap/terraform.tfstate")
    except Exception:  # noqa: BLE001
        if not state_file.is_file():
            raise StrataError(
                f"no StrataCI state found (s3://{bucket}/bootstrap/terraform.tfstate, "
                f"nor a local copy in {wd}). Can't destroy from state — remove the "
                "resources by hand, or re-run bootstrap first to regenerate state."
            ) from None
        print("  (S3 state copy not found; using the local workdir state)")

    (wd / "zz_destroy_override.tf").write_text(_DESTROY_OVERRIDE, encoding="utf-8")

    print(f"\n‼  This DESTROYS StrataCI in account {account} ({region}): the {bucket} "
          "bucket, its DynamoDB tables, the CodeBuild runner, and IAM roles.")
    print("   Your StrataBI data plane is separate — run `stratactl dev uninstall` first "
          "if you haven't already.")
    if input("   Type the account id to confirm: ").strip() != account:
        print("   mismatch — aborted.")
        return 0

    tenv = _tofu_env(settings)
    subprocess.run([tofu, "init", "-input=false"], cwd=wd, check=True, env=tenv)
    subprocess.run([tofu, "destroy", "-input=false", "-auto-approve"], cwd=wd, check=True, env=tenv)
    print("\nStrataCI destroyed. The account is clean (aside from anything outside StrataBI).")
    return 0


def add_parser(sub: "argparse._SubParsersAction") -> None:
    sp = sub.add_parser("bootstrap", help="Stand StrataCI up inside a customer AWS account.")
    sp.add_argument("--run", action="store_true",
                    help="Actually run tofu init+plan (default: dry run).")
    sp.add_argument("--yes", action="store_true",
                    help="With --run, apply without an interactive confirmation.")
    sp.add_argument("--destroy", action="store_true",
                    help="Tear StrataCI down: pull the durable state from S3, then tofu destroy.")
