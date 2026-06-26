"""Load and save the YAML installation config — the source of truth.

The config is a nested mapping of ``section -> key -> value`` (plus a top-level
``modules`` map for module-specific values). It is human-readable and is what
the user keeps in version control; Terraform variable files are generated from
it and should not be edited by hand.

YAML is read/written via PyYAML. The import is lazy so the rest of the CLI keeps
working if PyYAML is absent; only the wizard requires it, and we raise a clear
message pointing at the extra.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from strata.errors import ConfigError

DEFAULT_FILENAME = "strata.yaml"


def _yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise ConfigError(
            "PyYAML is required for the installation wizard. "
            "Install it with: pip install 'strata-cli[wizard]'  (or: pip install pyyaml)"
        ) from exc


def config_path(explicit: str | None = None) -> Path:
    """Resolve the installation-config path: explicit flag, else ./strata.yaml."""
    if explicit:
        return Path(explicit).expanduser()
    return Path.cwd() / DEFAULT_FILENAME


def exists(path: Path) -> bool:
    return path.is_file()


def load(path: Path) -> dict[str, Any]:
    """Return the parsed config, or an empty dict if the file does not exist."""
    if not path.is_file():
        return {}
    yaml = _yaml()
    try:
        data = yaml.safe_load(path.read_text("utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"invalid installation config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"installation config {path} must be a mapping at the top level")
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    yaml = _yaml()
    header = (
        "# StrataBI installation config — source of truth for deployment.\n"
        "# Managed by `strata init`. Terraform variable files are generated from\n"
        "# this; edit values here and re-run `strata init` rather than editing tfvars.\n"
    )
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Small accessors so callers don't hand-walk the nested dict.
# --------------------------------------------------------------------------- #
def get(data: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    sec = data.get(section)
    if isinstance(sec, dict) and key in sec:
        return sec[key]
    return default


def set_(data: dict[str, Any], section: str, key: str, value: Any) -> None:
    data.setdefault(section, {})[key] = value


def flatten(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten section/key into a single key->value map (keys are unique across
    a schema), used for `when` conditions and reuse lookups."""
    out: dict[str, Any] = {}
    for section, body in data.items():
        if isinstance(body, dict):
            for k, v in body.items():
                out[k] = v
    return out


def module_section(module_id: str) -> str:
    """Config section name for a module's values (kept under a `modules` namespace
    in the YAML, but addressed as a flat section here for uniformity)."""
    return f"module:{module_id}"
