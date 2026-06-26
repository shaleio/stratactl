from strata.commands import _common


def run(settings, args) -> int:
    return _common.launch(
        settings,
        action="uninstall",
        module_spec=args.module,
        version="installed",
        cli_source="customer_s3",
    )
