from strata.commands import _common


def run(settings, args) -> int:
    return _common.launch(
        settings,
        action="update",
        module_spec=args.module,
        version=getattr(args, "version", "latest"),
        cli_source=getattr(args, "source", None) or settings.source,
        assume_yes=getattr(args, "yes", False),
    )
