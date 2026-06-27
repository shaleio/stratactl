"""Shared install/update/uninstall flow: resolve source, (maybe) check
entitlement, launch the CodeBuild runner, stream logs."""

from __future__ import annotations

from typing import Optional

from strata import hq
from strata.artifacts.source import KIND_STRATAHQ, resolve_source
from strata.aws import session as aws_session
from strata.config.models import Settings
from strata.errors import StrataError
from strata.registry import installs, jobs
from strata.runner import launcher
from strata.runner.logs import stream_build


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def launch(settings: Settings, *, action: str, module_spec: str, version: str,
           cli_source: Optional[str] = None) -> int:
    sess = aws_session.make_session(settings)
    account = aws_session.account_id(sess)
    bucket = settings.resolved_bucket(account)

    src = resolve_source(module_spec, settings, cli_source)
    module_id = src.module_id

    # For destroy we operate on the installed version's cached bundle.
    if action in ("uninstall", "destroy"):
        rec = installs.get(sess, settings.customer.install_table, module_id)
        if not rec or rec.get("status") == installs.REMOVED:
            raise StrataError(f"{module_id} is not installed in this account.")
        version = rec.get("version", version)
        artifact_env = {
            "STRATA_ARTIFACT_S3": f"s3://{bucket}/artifacts/{module_id}/{version}/module.zip",
        }
    elif src.kind == KIND_STRATAHQ:
        ent = hq.check_entitlement(settings.hq, customer_id=account,
                                   module_id=module_id, version=version,
                                   session=sess, region=settings.hq.region or settings.region)
        artifact_env = {"STRATA_ARTIFACT_URL": ent.artifact_url or ""}
        if ent.sha256:
            artifact_env["STRATA_ARTIFACT_SHA256"] = ent.sha256
        if ent.signature:
            artifact_env["STRATA_ARTIFACT_SIGNATURE"] = ent.signature
        version = ent.version
        print(f"entitled: {module_id}@{version} (StrataHQ)")
    else:  # customer_s3
        uri = src.customer_s3_uri or f"s3://{bucket}/artifacts/{module_id}/{version}/module.zip"
        artifact_env = {"STRATA_ARTIFACT_S3": uri}
        if src.sha256:
            artifact_env["STRATA_ARTIFACT_SHA256"] = src.sha256

    job = jobs.create(sess, settings.customer.jobs_table,
                      action=action, module_id=module_id, version=version)

    env = {
        "STRATA_ACTION": action,
        "STRATA_MODULE": module_id,
        "STRATA_VERSION": version,
        "STRATA_REGION": settings.region,
        "STRATA_JOB_ID": job.job_id,
        # The licensed/verified account; the runner refuses to deploy if it finds
        # itself running in a different account (#2 account self-check).
        "STRATA_CUSTOMER_ID": account,
        "STRATA_STATE_BUCKET": bucket,
        "STRATA_LOCK_TABLE": settings.customer.lock_table,
        "STRATA_INSTALL_TABLE": settings.customer.install_table,
        "STRATA_JOBS_TABLE": settings.customer.jobs_table,
        **artifact_env,
    }

    # Bundle-signing config (Decision C). Verification is offline in the runner.
    if settings.hq.public_key:
        env["STRATA_HQ_PUBKEY"] = settings.hq.public_key
    if _truthy(settings.hq.require_signature):
        env["STRATA_REQUIRE_SIGNATURE"] = "1"

    runner = launcher.for_settings(settings)
    handle = runner.start(sess, job_id=job.job_id, env=env)
    log_location = None
    if handle.get("log_group") and handle.get("log_stream"):
        log_location = f"{handle['log_group']}:{handle['log_stream']}"
    jobs.attach_build(sess, settings.customer.jobs_table, job.job_id,
                      handle["build_id"], log_location)

    print(f"job {job.job_id} started ({action} {module_id}@{version})  build={handle['build_id']}")
    print("--- streaming runner logs ---")
    status = stream_build(sess, handle["build_id"])
    print(f"--- runner finished: {status} ---")
    return 0 if status == "SUCCEEDED" else 1
