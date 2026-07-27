"""Shared install/update/uninstall flow: resolve source, (maybe) check
entitlement, launch the CodeBuild runner, stream logs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from strata import hq
from strata.artifacts.source import KIND_PUBLIC, KIND_STRATAHQ, resolve_source
from strata.aws import session as aws_session
from strata.config.models import Settings
from strata.errors import StrataError
from strata.registry import installs, jobs
from strata.runner import launcher
from strata.runner.logs import stream_build


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _resolve_tfvars(module_id: str) -> dict:
    """Render the customer's `strata init` values (from strata.yaml) as a JSON-able
    tfvars dict for the runner. Returns {} if nothing is configured. Never fatal —
    an install shouldn't die because tfvars couldn't be rendered."""
    try:
        from strata.install import store
        from strata.install import tfvars as tfv

        config = store.load(store.config_path(None))
        if module_id in ("stratabi", "stratabi_dev"):
            from strata.install.schema import stratabi_schema
            return tfv.render_json(stratabi_schema(), config)
        from strata.install import modules as mod_discovery
        schema = mod_discovery.discover(
            module_id, search_dirs=[Path.cwd(), Path.cwd() / "infra"]
        )
        return tfv.render_module_json(schema, config, module_id) if schema else {}
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not render tfvars for {module_id}: {exc}")
        return {}


def _upload_tfvars(sess, bucket: str, module_id: str, data: dict) -> Optional[str]:
    """Upload the customer tfvars to a deterministic key the runner fetches. The
    runner writes it as zz-customer.auto.tfvars.json so it overrides the bundle's
    baked defaults.auto.tfvars.json."""
    if not data:
        return None
    key = f"config/{module_id}.auto.tfvars.json"
    sess.client("s3").put_object(
        Bucket=bucket, Key=key,
        Body=json.dumps(data, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def launch(settings: Settings, *, action: str, module_spec: str, version: str,
           cli_source: Optional[str] = None, module_id_override: Optional[str] = None) -> int:
    sess = aws_session.make_session(settings)
    account = aws_session.account_id(sess)
    bucket = settings.resolved_bucket(account)

    src = resolve_source(module_spec, settings, cli_source)
    # A public/explicit-URL spec derives its module_id from the URL filename, which
    # need not match the real module (e.g. a GitHub release asset). Callers that know
    # the true id (the dev-edition install command) pass it explicitly.
    module_id = module_id_override or src.module_id

    # For destroy we operate on the installed version's tofu. Where that tofu lives
    # depends on the delivery mode recorded at install time:
    #   local   -> the bundle was cached into the customer bucket; use it (offline).
    #   managed -> nothing was persisted; re-fetch from HQ. Destroy is ALWAYS
    #              permitted (even if the license lapsed or the version is retired),
    #              so we pass action="destroy" to the entitlement gate.
    if action in ("uninstall", "destroy"):
        rec = installs.get(sess, settings.customer.install_table, module_id)
        if not rec or rec.get("status") == installs.REMOVED:
            raise StrataError(f"{module_id} is not installed in this account.")
        version = rec.get("version", version)
        delivery = rec.get("delivery", "local")
        if delivery == "managed":
            ent = hq.check_entitlement(settings.hq, customer_id=account,
                                       module_id=module_id, version=version,
                                       session=sess, region=settings.hq.region or settings.region,
                                       action="destroy")
            artifact_env = {"STRATA_ARTIFACT_URL": ent.artifact_url or ""}
            if ent.sha256:
                artifact_env["STRATA_ARTIFACT_SHA256"] = ent.sha256
            if ent.signature:
                artifact_env["STRATA_ARTIFACT_SIGNATURE"] = ent.signature
        else:  # local — teardown from the cached bundle, re-verified offline
            uri = rec.get("bundle_uri") or f"s3://{bucket}/artifacts/{module_id}/{version}/module.zip"
            artifact_env = {"STRATA_ARTIFACT_S3": uri}
            if rec.get("sha256"):
                artifact_env["STRATA_ARTIFACT_SHA256"] = rec["sha256"]
            if rec.get("signature"):
                artifact_env["STRATA_ARTIFACT_SIGNATURE"] = rec["signature"]
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
    elif src.kind == KIND_PUBLIC:
        # Public artifact (e.g. a GitHub release of the source-available dev edition).
        # Fetched directly over https by the runner — NO StrataHQ contact, no
        # entitlement, no token. Signature verification still applies if configured.
        if not src.public_url:
            raise StrataError("public source requires an http(s) URL")
        artifact_env = {"STRATA_ARTIFACT_URL": src.public_url}
        if src.sha256:
            artifact_env["STRATA_ARTIFACT_SHA256"] = src.sha256
        print(f"public source: {module_id}@{version} ({src.public_url})")
    else:  # customer_s3
        uri = src.customer_s3_uri or f"s3://{bucket}/artifacts/{module_id}/{version}/module.zip"
        artifact_env = {"STRATA_ARTIFACT_S3": uri}
        if src.sha256:
            artifact_env["STRATA_ARTIFACT_SHA256"] = src.sha256

    # Bridge the customer's tfvars to the ephemeral runner (install/update only;
    # destroy reuses the copy uploaded at install time).
    if action in ("install", "update"):
        _key = _upload_tfvars(sess, bucket, module_id, _resolve_tfvars(module_id))
        if _key:
            print(f"tfvars: uploaded s3://{bucket}/{_key}")

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

    # The runner must have the `strata` package to execute the job. On a standard
    # CodeBuild image it isn't present, so tell the buildspec how to install it
    # (imported as `strata`, distributed on PyPI as `stratactl`). Overridable to
    # pin a version or point at an s3:// wheel via STRATA_RUNNER_PKG. Harmless on a
    # prebaked image — the buildspec skips the install when `import strata` already
    # succeeds. Sent per-build so it always tracks the launching CLI, no re-bootstrap.
    env["STRATA_PKG"] = os.getenv("STRATA_RUNNER_PKG", "stratactl")

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
