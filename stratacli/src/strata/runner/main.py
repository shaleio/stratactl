"""StrataCI runner — runs INSIDE the ephemeral CodeBuild container.

Reads a job spec from the environment, fetches + verifies the artifact, runs
OpenTofu against the customer's S3 backend, and records status/outputs. Uses the
runner's own (CodeBuild service-role) credentials — no profile.

    python -m strata.runner
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3

from strata.artifacts import bundle, manifest as manifest_
from strata.registry import installs, jobs
from strata.runner import sensitive


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        print(f"runner: missing required env {name}", file=sys.stderr)
        sys.exit(2)
    return val


def _tofu(args: list[str], cwd: Path, env: dict | None = None) -> None:
    print(f"+ tofu {' '.join(args)}", flush=True)
    proc = subprocess.run(["tofu", *args], cwd=str(cwd), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"tofu {args[0]} failed (exit {proc.returncode})")


def _scoped_deploy_env(session, m, region: str, state_bucket: str, lock_table: str) -> dict | None:
    """Decision A: assume the deploy role with a session policy derived from the
    module manifest, so tofu runs with module-scoped least privilege. Returns an
    env dict (for the tofu subprocess) or None to use the runner's own creds."""
    role_arn = os.environ.get("STRATA_DEPLOY_ROLE_ARN")
    if not role_arn:
        print("runner: STRATA_DEPLOY_ROLE_ARN unset — running tofu with runner credentials (unscoped).", flush=True)
        return None

    from strata.runner import permissions

    if not m.permissions_are_scoped:
        print("runner: WARNING — module permissions are not structured; deploy scope will be broad "
              "(legacy service:* grants). Structured required_permissions are required for third-party modules.",
              flush=True)
    if permissions.policy_too_large(m, state_bucket=state_bucket, lock_table=lock_table):
        raise RuntimeError("module session policy exceeds the 2048-char inline limit; "
                           "split required_permissions into managed policies (v2).")

    creds = session.client("sts").assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"strataci-{m.module_id[:48]}",
        Policy=permissions.policy_json(m, state_bucket=state_bucket, lock_table=lock_table),
        DurationSeconds=3600,
    )["Credentials"]

    env = dict(os.environ)
    env["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    env["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    env["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    env["AWS_DEFAULT_REGION"] = region
    print(f"runner: assumed deploy role with module-scoped session policy "
          f"({len(m.required_permissions)} declared grant(s)).", flush=True)
    return env


def main() -> int:
    action = _env("STRATA_ACTION", required=True)
    module_id = _env("STRATA_MODULE", required=True)
    version = _env("STRATA_VERSION", "latest")
    region = _env("STRATA_REGION", "us-east-1")
    job_id = _env("STRATA_JOB_ID", required=True)

    state_bucket = _env("STRATA_STATE_BUCKET", required=True)
    lock_table = _env("STRATA_LOCK_TABLE", "strataci_tf_locks")
    install_table = _env("STRATA_INSTALL_TABLE", "strataci_install_registry")
    jobs_table = _env("STRATA_JOBS_TABLE", "strataci_jobs")

    artifact_url = _env("STRATA_ARTIFACT_URL")
    artifact_s3 = _env("STRATA_ARTIFACT_S3")
    sha256 = _env("STRATA_ARTIFACT_SHA256")

    session = boto3.Session(region_name=region)
    s3 = session.client("s3")

    # #2 account self-check: the runner verifies the account it is actually running
    # in against the licensed/entitled account passed by the CLI. A copied job spec
    # replayed into a different account is refused here. (The cryptographic version —
    # comparing against a signed, account-bound token — is the runtime boot-time
    # check tracked in OPEN_QUESTIONS; this is the cheap consistency gate.)
    real_account = session.client("sts").get_caller_identity()["Account"]
    entitled_account = _env("STRATA_CUSTOMER_ID")
    print(f"runner: account {real_account}", flush=True)
    if entitled_account and real_account != entitled_account:
        jobs.set_status(session, jobs_table, job_id, jobs.FAILED, exit_code=2,
                        error="account mismatch: runner is not in the licensed account")
        print(f"runner: REFUSING — running in {real_account} but entitlement is for "
              f"{entitled_account}", file=sys.stderr, flush=True)
        return 2

    jobs.set_status(session, jobs_table, job_id, jobs.RUNNING)
    workroot = Path(tempfile.mkdtemp(prefix="strataci-"))
    try:
        # 1. fetch artifact
        zip_path = workroot / "module.zip"
        if artifact_url:
            bundle.download_url(artifact_url, zip_path)
        elif artifact_s3:
            bundle.download_s3(s3, artifact_s3, zip_path)
        else:
            raise RuntimeError("no artifact source (STRATA_ARTIFACT_URL or STRATA_ARTIFACT_S3)")

        # 2. verify hash + signature, then 3. extract
        actual = bundle.verify_sha256(zip_path, sha256)
        print(f"runner: artifact sha256 {actual}", flush=True)

        signature = os.environ.get("STRATA_ARTIFACT_SIGNATURE")
        pubkey = os.environ.get("STRATA_HQ_PUBKEY")
        require_sig = os.environ.get("STRATA_REQUIRE_SIGNATURE", "").lower() in ("1", "true", "yes", "on")
        if signature and pubkey:
            bundle.verify_signature(actual, signature, pubkey)
            print("runner: bundle signature verified (Decision C)", flush=True)
        elif require_sig:
            raise RuntimeError("signature required but missing signature/public key "
                               "(STRATA_ARTIFACT_SIGNATURE / STRATA_HQ_PUBKEY)")
        else:
            print("runner: no signature verification (unsigned or keys not configured)", flush=True)

        workdir = bundle.safe_extract(zip_path, workroot / "bundle")

        # 4. validate manifest
        m = manifest_.load_and_validate(workdir / "stratabi-module.json")
        state_key = os.environ.get("STRATA_STATE_KEY") or m.state_key
        tofu_dir = workdir / "tofu"

        # surface config/defaults.json to tofu as auto-loaded vars, if present
        defaults = workdir / "config" / "defaults.json"
        if defaults.is_file():
            shutil.copyfile(defaults, tofu_dir / "defaults.auto.tfvars.json")

        # 5. assume the module-scoped deploy role (Decision A), then run tofu
        deploy_env = _scoped_deploy_env(session, m, region, state_bucket, lock_table)

        backend = [
            f"-backend-config=bucket={state_bucket}",
            f"-backend-config=key={state_key}",
            f"-backend-config=region={region}",
            f"-backend-config=dynamodb_table={lock_table}",
            "-backend-config=encrypt=true",
        ]
        _tofu(["init", "-input=false", *backend], tofu_dir, env=deploy_env)

        if action in ("install", "update"):
            _tofu(["plan", "-input=false", "-out=plan.bin"], tofu_dir, env=deploy_env)
            _tofu(["apply", "-input=false", "-auto-approve", "plan.bin"], tofu_dir, env=deploy_env)
            outputs_raw = subprocess.check_output(["tofu", "output", "-json"],
                                                  cwd=str(tofu_dir), env=deploy_env)
            outputs = json.loads(outputs_raw or b"{}")

            # cache the verified bundle so future update/uninstall work without HQ
            bundle.cache_to_customer_s3(s3, state_bucket, module_id, version, zip_path)
            installs.upsert(session, install_table, module_id=module_id, version=version,
                            state_key=state_key, outputs=outputs, job_id=job_id)

        elif action in ("uninstall", "destroy"):
            if not m.supports_destroy:
                raise RuntimeError(f"module {module_id} does not support destroy")
            _tofu(["destroy", "-input=false", "-auto-approve"], tofu_dir, env=deploy_env)
            installs.mark_removed(session, install_table, module_id, job_id=job_id)
        else:
            raise RuntimeError(f"unknown action '{action}'")

        jobs.set_status(session, jobs_table, job_id, jobs.SUCCEEDED, exit_code=0)
        print(f"runner: {action} {module_id}@{version} SUCCEEDED", flush=True)
        return 0

    except Exception as exc:  # noqa: BLE001 — top-level runner boundary
        # Scrub any sensitive value (e.g. the presigned artifact URL) from the error
        # before it reaches the runner log or the jobs table.
        safe = sensitive.redact_text(str(exc))
        jobs.set_status(session, jobs_table, job_id, jobs.FAILED, exit_code=1, error=safe)
        print(f"runner: {action} {module_id} FAILED: {safe}", file=sys.stderr, flush=True)
        return 1
    finally:
        shutil.rmtree(workroot, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
