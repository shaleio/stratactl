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


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _prepare_tfvars(sess, bucket: str, *, action: str, module_id: str, env_name: str,
                    reconfigure: bool, assume_yes: bool) -> None:
    """Decide what configuration the runner gets, BEFORE anything is launched.

    * Environment values (network + identity — envstore.ENV_KEYS) live once per env at
      config/env/<env>.auto.tfvars.json. The runner loads them last, so they win.
    * install: seed the environment from strata.yaml if it doesn't exist yet; upload the
      module's own values.
    * update: reuse what's stored — strata.yaml is NOT re-read (an upgrade must not pick up
      whatever config sits in the current directory). --reconfigure re-reads it, shows the
      diff, and asks before changing anything.
    * Before launching, the environment's network is checked against the account.
    """
    from strata.install import envstore

    s3 = sess.client("s3")
    env_cfg = envstore.load(s3, bucket, env_name)
    stored_mod = envstore.load_module(s3, bucket, module_id)
    env_label = f"environment '{env_name}' (s3://{bucket}/{envstore.key(env_name)})"

    def preflight(cfg) -> None:
        if not cfg:
            return
        try:
            problems = envstore.validate_network(sess.client("ec2"), cfg)
        except PermissionError:
            print("env: warning — no ec2:Describe* permission; skipped the network preflight.")
            return
        if problems:
            raise StrataError(f"network preflight failed for {env_label}:\n  - "
                              + "\n  - ".join(problems)
                              + "\nNothing was changed. Fix it with `stratactl env set` (or "
                                "`env import`), then re-run.")
        print(f"env: {env_label} — network preflight OK")

    if action == "install" or reconfigure:
        rendered = _resolve_tfvars(module_id)
        new_env, new_mod = envstore.split(rendered)
        candidate, write_env = env_cfg, False

        if env_cfg is None:
            if new_env:
                candidate, write_env = new_env, True
        else:
            changes = envstore.diff(env_cfg, new_env)
            if changes:
                print(f"env: strata.yaml differs from {env_label}:\n{envstore.format_diff(changes)}")
                if not reconfigure:
                    raise StrataError(
                        "refusing to change the environment's network/identity during an "
                        f"{action}. Keep the stored values (fix strata.yaml), or re-run with "
                        "--reconfigure to change them, or use `stratactl env set`.")
                if not _confirm("Apply these environment changes?", assume_yes):
                    raise StrataError("aborted — environment unchanged.")
                candidate, write_env = {**env_cfg, **new_env}, True

        preflight(candidate)  # before anything is written
        if write_env:
            envstore.save(s3, bucket, env_name, candidate)
            verb = "created" if env_cfg is None else "updated"
            print(f"env: {verb} {env_label}")
        if new_mod or stored_mod is None:
            envstore.save_module(s3, bucket, module_id, new_mod)
            print(f"tfvars: uploaded s3://{bucket}/{envstore.module_key(module_id)}")
    else:  # update without --reconfigure: reuse stored config untouched
        print(f"tfvars: reusing stored s3://{bucket}/{envstore.module_key(module_id)}"
              + ("" if stored_mod is not None else " (none stored)"))
        if env_cfg is None and stored_mod and envstore.split(stored_mod)[0]:
            raise StrataError(
                f"no {env_label} yet, and this module's stored tfvars carry network values. "
                "Create the environment first so an upgrade can't change the network:\n"
                f"  stratactl env import --env {env_name}     # read it from the deployed platform\n"
                f"  stratactl env show   --env {env_name}     # check it\n"
                "then re-run this update.")
        preflight(env_cfg)


def launch(settings: Settings, *, action: str, module_spec: str, version: str,
           cli_source: Optional[str] = None, module_id_override: Optional[str] = None,
           assume_yes: bool = False, env_name: str = "dev", reconfigure: bool = False,
           allow_replace: bool = False) -> int:
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
        # Official Modules carry a per-account perpetual license recorded via an Order
        # Form at extraction; the platform products carry the master Enterprise license
        # instead and are exempt. Show the terms + capture acceptance, then hand the
        # acceptance metadata to HQ, which writes the authoritative, account-stamped
        # record. Declining raises and aborts before anything is extracted.
        from strata.legal import order_form as _of
        order_acceptance = None
        if action in ("install", "update") and module_id not in _of.PLATFORM_IDS:
            order_acceptance = _of.prompt_acceptance(
                module_id=module_id, version=version, account_id=account,
                assume_yes=assume_yes,
            )
        ent = hq.check_entitlement(settings.hq, customer_id=account,
                                   module_id=module_id, version=version,
                                   session=sess, region=settings.hq.region or settings.region,
                                   action=action, order_acceptance=order_acceptance)
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

    # Configuration for the ephemeral runner (install/update; destroy reuses what's stored).
    if action in ("install", "update"):
        _prepare_tfvars(sess, bucket, action=action, module_id=module_id, env_name=env_name,
                        reconfigure=reconfigure, assume_yes=assume_yes)

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
        "STRATA_ENV": env_name,
        **artifact_env,
    }
    if allow_replace:
        env["STRATA_ALLOW_REPLACE"] = "1"

    # The runner must have the `strata` package to execute the job. On a standard
    # CodeBuild image it isn't present, so tell the buildspec how to install it
    # (imported as `strata`, distributed on PyPI as `stratactl`). Overridable to
    # point at an s3:// wheel or a different spec via STRATA_RUNNER_PKG. Harmless on a
    # prebaked image — the buildspec skips the install when `import strata` already
    # succeeds. Sent per-build so it always tracks the launching CLI, no re-bootstrap.
    # Default is PINNED to this CLI's version so the runner can't silently drift to a
    # newer PyPI release than the operator is running locally.
    from strata import __version__ as _strata_version
    env["STRATA_PKG"] = os.getenv("STRATA_RUNNER_PKG", f"stratactl=={_strata_version}")

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
    if status != "SUCCEEDED":
        # E11: tell the operator what to do next instead of leaving them at a bare FAILED.
        print(
            "\nNext steps:\n"
            f"  - Re-running is safe — state lives in your S3 backend, so a re-run only\n"
            f"    creates what's missing:  stratactl {action} {module_id}\n"
            f"  - Inspect the full log:    stratactl logs {job.job_id}\n"
            "  - Common causes: an AlreadyExists_ name collision (pick a different install\n"
            "    name), no VPC / <2 AZ subnets, expired SSO (`aws sso login`), or Bedrock\n"
            "    model access not enabled when enable_ai=true."
        )
    return 0 if status == "SUCCEEDED" else 1
