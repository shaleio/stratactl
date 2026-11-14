"""Install the StrataBI platform itself (not a module) through the runner.

Both editions ship as module.zip-format bundles whose `tofu/` is the platform's
infrastructure:

  * Enterprise / commercial StrataBI  -> licensed (source=stratahq, entitlement
    required), module id "stratabi".
  * Developer / Community StrataBI-dev -> source-available. NO StrataHQ contract
    required. Install it HQ-free from either:
      - a public URL   (--source https://.../stratabi_dev.zip, e.g. a GitHub
        release) — the runner fetches it directly, no StrataHQ, no entitlement; or
      - your own S3    (default: customer_s3 — host the bundle in your bucket).
    Module id "stratabi_dev".

They run through the identical install path (source → runner → tofu), so
update/uninstall/list/logs work on them too.
"""

from strata.commands import _common

ENTERPRISE_MODULE = "stratabi"
DEV_MODULE = "stratabi_dev"


def run_enterprise(settings, args) -> int:
    return _common.launch(
        settings,
        action="install",
        module_spec=ENTERPRISE_MODULE,
        version=getattr(args, "version", "latest"),
        cli_source=getattr(args, "source", None) or "stratahq",
        env_name=getattr(args, "env", None) or "dev",
        reconfigure=getattr(args, "reconfigure", False),
        allow_replace=getattr(args, "allow_replace", False),
    )


def run_dev(settings, args) -> int:
    source = getattr(args, "source", None)
    version = getattr(args, "version", "latest")
    # An explicit artifact URI installs the dev edition HQ-free — a public https URL
    # (e.g. a GitHub release: ZERO StrataHQ contact) or your own s3:// bundle. Pass it
    # as the spec and force the module id so the artifact filename need not be exactly
    # "stratabi_dev".
    if source and source.startswith(("http://", "https://", "s3://")):
        return _common.launch(
            settings,
            action="install",
            module_spec=source,
            version=version,
            module_id_override=DEV_MODULE,
            env_name=getattr(args, "env", None) or "dev",
            reconfigure=getattr(args, "reconfigure", False),
            allow_replace=getattr(args, "allow_replace", False),
        )
    # Otherwise `source` is a source KIND: customer_s3 (default — your own bucket at
    # the conventional key), or public / stratahq.
    return _common.launch(
        settings,
        action="install",
        module_spec=DEV_MODULE,
        version=version,
        cli_source=source or "customer_s3",
        env_name=getattr(args, "env", None) or "dev",
        reconfigure=getattr(args, "reconfigure", False),
        allow_replace=getattr(args, "allow_replace", False),
    )
