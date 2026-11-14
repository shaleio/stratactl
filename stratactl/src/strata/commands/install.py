from strata.commands import _common


def run(settings, args) -> int:
    return _common.launch(
        settings,
        action="install",
        module_spec=args.module,
        version=getattr(args, "version", "latest"),
        cli_source=getattr(args, "source", None) or settings.source,
        assume_yes=getattr(args, "yes", False),
        env_name=getattr(args, "env", None) or "dev",
        reconfigure=getattr(args, "reconfigure", False),
        allow_replace=getattr(args, "allow_replace", False),
    )
