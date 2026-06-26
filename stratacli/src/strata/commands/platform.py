"""Install the StrataBI platform itself (not a module) through the runner.

Both editions ship as module.zip-format bundles whose `tofu/` is the platform's
infrastructure:

  * Enterprise / commercial StrataBI  -> licensed (source=stratahq, entitlement
    required), module id "stratabi".
  * Developer / Community StrataBI-dev -> source-available (source=customer_s3 by
    default; host the bundle in your own bucket), module id "stratabi_dev".

They run through the identical install path (entitlement → runner → tofu), so
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
    )


def run_dev(settings, args) -> int:
    return _common.launch(
        settings,
        action="install",
        module_spec=DEV_MODULE,
        version=getattr(args, "version", "latest"),
        cli_source=getattr(args, "source", None) or "customer_s3",
    )
