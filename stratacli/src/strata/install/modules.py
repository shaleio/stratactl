"""Discover a module's installation schema — without hardcoding module names.

A module declares the variables it needs in a small JSON document, so the CLI
can prompt for them generically. The CLI looks for that declaration in, in order:

  1. ``--schema <file>`` passed explicitly, or
  2. ``<dir>/install.schema.json`` for each search dir (cwd, ./modules/<id>/), or
  3. an ``install_schema`` block inside the module's ``module.json`` manifest.

Schema document format (all keys optional except fields[].key/prompt)::

    {
      "title": "FinOps module",
      "fields": [
        {"key": "ce_granularity", "prompt": "Cost Explorer granularity",
         "tfvar": "ce_granularity", "type": "enum",
         "choices": ["DAILY", "MONTHLY"], "default": "DAILY"},
        {"key": "budget_threshold", "prompt": "Monthly budget alert ($)",
         "tfvar": "budget_threshold", "type": "int", "default": 1000}
      ]
    }

This keeps module installation data-driven: a new module ships its own
declaration and the CLI prompts for it with no code change here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from strata.install.schema import Field, InstallSchema
from strata.install import store


def schema_from_dict(module_id: str, doc: dict[str, Any]) -> InstallSchema:
    section = store.module_section(module_id)
    fields: list[Field] = []
    for raw in doc.get("fields", []):
        if not raw.get("key") or not raw.get("prompt"):
            continue
        fields.append(Field(
            section=section,
            key=str(raw["key"]),
            prompt=str(raw["prompt"]),
            tfvar=raw.get("tfvar"),
            type=raw.get("type", "string"),
            default=raw.get("default"),
            required=bool(raw.get("required", False)),
            secret=bool(raw.get("secret", False)),
            choices=raw.get("choices"),
            validator=raw.get("validator"),
            help=raw.get("help", ""),
            when=tuple(raw["when"]) if isinstance(raw.get("when"), (list, tuple)) else None,
        ))
    return InstallSchema(
        name=module_id,
        title=doc.get("title", f"{module_id} module"),
        fields=fields,
    )


def _candidate_paths(module_id: str, search_dirs: list[Path]) -> list[Path]:
    names = ["install.schema.json"]
    paths: list[Path] = []
    for d in search_dirs:
        for n in names:
            paths.append(d / n)
            paths.append(d / "modules" / module_id / n)
            paths.append(d / module_id / n)
    return paths


def discover(module_id: str, explicit: Optional[str] = None,
             search_dirs: Optional[list[Path]] = None) -> Optional[InstallSchema]:
    """Return the module's InstallSchema, or None if it declares no variables."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return schema_from_dict(module_id, json.loads(p.read_text("utf-8")))
        return None

    search_dirs = search_dirs or [Path.cwd()]
    for p in _candidate_paths(module_id, search_dirs):
        if p.is_file():
            try:
                return schema_from_dict(module_id, json.loads(p.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                continue

    # Fallback: an install_schema block inside a module.json manifest.
    for d in search_dirs:
        for manifest in (d / "module.json", d / "modules" / module_id / "module.json",
                         d / module_id / "module.json"):
            if manifest.is_file():
                try:
                    doc = json.loads(manifest.read_text("utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(doc.get("install_schema"), dict):
                    return schema_from_dict(module_id, doc["install_schema"])
    return None
