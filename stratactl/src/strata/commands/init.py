"""`strata init` — interactive installation & initialization wizard.

Collects the configuration a StrataBI deployment (or an official module) needs,
persists it to the YAML installation config (the source of truth), and generates
the Terraform/OpenTofu variable files. Re-running reuses saved values and prompts
only for what is missing; `--update` re-asks everything.

  strata init                      initialize / refresh the StrataBI deployment
  strata init module <id>          initialize a module's variables
  strata init --show-config        print the resolved StrataCTL settings (legacy view)

The wizard itself lives in strata.install (schema / prompt / store / tfvars /
modules); this command only orchestrates them, so `deploy`, `upgrade`, and
`module install/update` can reuse the same pieces later.
"""

from __future__ import annotations

from pathlib import Path

from strata.errors import ConfigError
from strata.install import modules as mod_discovery
from strata.install import store, tfvars
from strata.install.prompt import Wizard
from strata.install.schema import stratabi_schema


def _legacy_show(settings) -> int:
    print("Resolved StrataCTL configuration")
    print("================================")
    print(f"  source            : {settings.source}")
    print(f"  region            : {settings.region}")
    print(f"  profile           : {settings.profile or '(default credentials)'}")
    print(f"  HQ endpoint       : {settings.hq.endpoint}")
    print(f"  HQ artifact bucket: {settings.hq.artifact_bucket}")
    print(f"  HQ token set      : {'yes' if settings.hq.token else 'no'}")
    return 0


def _out_dir(args) -> Path:
    if getattr(args, "out", None):
        return Path(args.out).expanduser()
    # Default to ./infra when it looks like a Terraform module lives there.
    infra = Path.cwd() / "infra"
    return infra if infra.is_dir() else Path.cwd()


def _interactive(args) -> bool:
    return not getattr(args, "non_interactive", False)


def run(settings, args) -> int:
    if getattr(args, "show_config", False):
        return _legacy_show(settings)

    what = getattr(args, "what", "stratabi") or "stratabi"
    if what == "module":
        return _run_module(settings, args)
    return _run_stratabi(settings, args)


# --------------------------------------------------------------------------- #
# StrataBI deployment
# --------------------------------------------------------------------------- #
def _run_stratabi(settings, args) -> int:
    schema = stratabi_schema()
    path = store.config_path(getattr(args, "config", None))
    config = store.load(path)
    existed = store.exists(path)

    # Seed sensible defaults from resolved CLI settings / flags on first run.
    if not existed:
        store.set_(config, "installation", "aws_region", settings.region)
    if getattr(args, "name", None):
        store.set_(config, "installation", "name", args.name)

    if _interactive(args):
        print(f"StrataBI installation wizard  ({'updating' if existed else 'new'}: {path.name})")
        print("Answer the prompts; press Enter to accept the [default]. Ctrl-C to abort.")

    wizard = Wizard(schema, config, interactive=_interactive(args),
                    update=getattr(args, "update", False))
    config = wizard.run()

    store.save(path, config)
    out = _out_dir(args)
    tf = tfvars.write(schema, config, out)

    _summary(path, [tf])
    print("\nNext:")
    edition = store.get(config, "installation", "edition", "enterprise")
    cmd = "install-stratabi" if edition == "enterprise" else "install-stratabi-dev"
    print(f"  review {tf}")
    print(f"  stratactl {cmd}        # deploy with the generated variables")
    return 0


# --------------------------------------------------------------------------- #
# Module
# --------------------------------------------------------------------------- #
def _run_module(settings, args) -> int:
    module_id = getattr(args, "module_id", None)
    if not module_id:
        raise ConfigError("usage: strata init module <module_id>")

    schema = mod_discovery.discover(
        module_id,
        explicit=getattr(args, "schema", None),
        search_dirs=[Path.cwd(), _out_dir(args)],
    )
    if schema is None:
        print(f"Module '{module_id}' declares no installation variables — nothing to collect.")
        print("(A module ships an install.schema.json or an install_schema block in module.json.)")
        return 0

    path = store.config_path(getattr(args, "config", None))
    config = store.load(path)

    if _interactive(args):
        print(f"Module '{module_id}' installation wizard  ({path.name})")

    wizard = Wizard(schema, config, interactive=_interactive(args),
                    update=getattr(args, "update", False))
    config = wizard.run()

    store.save(path, config)

    out = _out_dir(args)
    body = tfvars.render_module(schema, config, module_id)
    written: list[Path] = []
    if body.strip():
        tf = out / f"{module_id}.auto.tfvars"
        out.mkdir(parents=True, exist_ok=True)
        tf.write_text(body, encoding="utf-8")
        written.append(tf)

    _summary(path, written)
    print("\nNext:")
    print(f"  stratactl install {module_id}        # install the module")
    return 0


def _summary(config_path: Path, generated: list[Path]) -> None:
    print("\nSaved:")
    print(f"  config : {config_path}")
    for g in generated:
        print(f"  tfvars : {g}")
