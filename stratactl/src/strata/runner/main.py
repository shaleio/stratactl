"""StrataCI runner — runs INSIDE the ephemeral CodeBuild container.

Reads a job spec from the environment, fetches + verifies the artifact, runs
OpenTofu against the customer's S3 backend, and records status/outputs. Uses the
runner's own (CodeBuild service-role) credentials — no profile.

    python -m strata.runner
"""

from __future__ import annotations

import json
import os
import re
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

    assume_kwargs = {
        "RoleArn": role_arn,
        "RoleSessionName": f"strataci-{m.module_id[:48]}",
        "DurationSeconds": 3600,
    }
    if m.permissions_are_scoped:
        # Third-party / structured: narrow the broad deploy role to exactly what the
        # module declared, via an inline session policy.
        if permissions.policy_too_large(m, state_bucket=state_bucket, lock_table=lock_table):
            raise RuntimeError("module session policy exceeds the inline limit; "
                               "split required_permissions into managed policies (v2).")
        assume_kwargs["Policy"] = permissions.policy_json(
            m, state_bucket=state_bucket, lock_table=lock_table)
        note = f"module-scoped session policy ({len(m.required_permissions)} declared grant(s))"
    else:
        # Simple form = Shaleio-internal / first-party per the manifest contract. Its
        # broad service:* grants don't fit an inline session policy, and scoping a
        # trusted, signed first-party bundle adds little — assume the FULL deploy role.
        note = "full deploy role (first-party simple permissions — no session-policy narrowing)"

    creds = session.client("sts").assume_role(**assume_kwargs)["Credentials"]

    env = dict(os.environ)
    env["AWS_ACCESS_KEY_ID"] = creds["AccessKeyId"]
    env["AWS_SECRET_ACCESS_KEY"] = creds["SecretAccessKey"]
    env["AWS_SESSION_TOKEN"] = creds["SessionToken"]
    env["AWS_DEFAULT_REGION"] = region
    print(f"runner: assumed deploy role — {note}.", flush=True)
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
            print("runner: WARNING - bundle signature NOT verified (no signature/public "
                  "key configured). The SHA-256 is checked, but integrity is not "
                  "cryptographically guaranteed. Set STRATA_REQUIRE_SIGNATURE=1 and "
                  "configure STRATA_HQ_PUBKEY + signed bundles to enforce signing.",
                  flush=True)

        workdir = bundle.safe_extract(zip_path, workroot / "bundle")

        # 4. validate manifest
        m = manifest_.load_and_validate(workdir / "stratabi-module.json")
        state_key = os.environ.get("STRATA_STATE_KEY") or m.state_key
        tofu_dir = workdir / "tofu"

        # surface config/defaults.json to tofu as auto-loaded vars, if present
        defaults = workdir / "config" / "defaults.json"
        if defaults.is_file():
            shutil.copyfile(defaults, tofu_dir / "defaults.auto.tfvars.json")

        # Customer tfvars uploaded by the CLI at install time (best-effort). Written as
        # zz-customer.auto.tfvars.json so tofu auto-loads it AFTER defaults.auto.tfvars.json
        # (lexical order), letting the customer's `strata init` values override the
        # bundle's baked defaults. Persisted in the customer bucket, so destroy reuses it.
        cust_key = f"config/{module_id}.auto.tfvars.json"
        try:
            obj = s3.get_object(Bucket=state_bucket, Key=cust_key)
            raw = obj["Body"].read()
            # Filter to variables this module actually declares. The install schema is
            # shared across editions, so a developer module receives enterprise-only keys
            # (enable_rbac, enable_ai, vpc_id, …) it never declares, which tofu emits as
            # "undeclared variable" warnings. Drop those here. Safety: if we can't find
            # any `variable` declarations (unexpected), write the file unfiltered rather
            # than risk dropping legitimate overrides.
            import re as _re
            declared: set[str] = set()
            for tf in tofu_dir.glob("*.tf"):
                declared |= set(_re.findall(r'(?m)^\s*variable\s+"([^"]+)"', tf.read_text(encoding="utf-8", errors="replace")))
            try:
                cust = json.loads(raw)
            except Exception:
                cust = None
            if declared and isinstance(cust, dict):
                dropped = sorted(k for k in cust if k not in declared)
                kept = {k: v for k, v in cust.items() if k in declared}
                (tofu_dir / "zz-customer.auto.tfvars.json").write_text(
                    json.dumps(kept, indent=2), encoding="utf-8")
                msg = f"runner: applied customer tfvars {cust_key} ({len(kept)} of {len(cust)} keys)"
                if dropped:
                    msg += f"; ignored keys not declared by this module: {', '.join(dropped)}"
                print(msg, flush=True)
            else:
                (tofu_dir / "zz-customer.auto.tfvars.json").write_bytes(raw)
                print(f"runner: applied customer tfvars {cust_key} (unfiltered)", flush=True)
        except Exception:  # noqa: BLE001 — missing key is normal for var-less modules
            print(f"runner: no customer tfvars at {cust_key} (bundle defaults only)", flush=True)

        # 5. assume the module-scoped deploy role (Decision A), then run tofu

        # CodeBuild is ephemeral by design — local tofu state would vanish with the
        # container, so a re-apply/update/uninstall would start from empty state and
        # collide with the resources the first run created ("already exists"). tofu's
        # S3 backend solves this natively: it pulls remote state from S3 on `init` and
        # pushes on `apply`, with the DynamoDB table as a lock. But the -backend-config
        # flags below are inert unless the config DECLARES a backend. Infra authored for
        # local use (e.g. the dev edition) doesn't, so inject one — but only when the
        # bundle declares none, since two backend blocks is a hard error.
        _tf_src = "".join(p.read_text(encoding="utf-8", errors="ignore")
                          for p in sorted(tofu_dir.glob("*.tf")))
        if not re.search(r'backend\s+"[^"]+"\s*\{', _tf_src):
            (tofu_dir / "zz_strataci_backend.tf").write_text(
                'terraform {\n  backend "s3" {}\n}\n', encoding="utf-8")
            print("runner: injected S3 backend (module declared none) — state will "
                  "persist in the customer bucket", flush=True)

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

            # Delivery mode (manifest): "local" persists the verified bundle into
            # the customer bucket so update/uninstall/destroy work offline and
            # airgapped; "managed" leaves no tofu behind — teardown re-fetches from
            # HQ (destroy is always permitted there). Either way state stays in the
            # customer's S3 backend.
            bundle_uri = None
            if m.delivery == "local":
                bundle_uri = bundle.cache_to_customer_s3(s3, state_bucket, module_id, version, zip_path)
                print(f"runner: delivery=local — bundle cached to {bundle_uri}", flush=True)
            else:
                print("runner: delivery=managed — bundle not persisted to the customer bucket", flush=True)
            installs.upsert(session, install_table, module_id=module_id, version=version,
                            state_key=state_key, outputs=outputs, job_id=job_id,
                            delivery=m.delivery, bundle_uri=bundle_uri,
                            sha256=actual, signature=signature)

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
