"""Packaged StrataCI bootstrap infrastructure (immutable, version-matched).

The OpenTofu that stands StrataCI up inside a customer account ships as package
data under ``strata/bootstrap/infra/`` so a pipx-installed ``stratacli`` is fully
self-contained (no repo checkout needed). ``bootstrap_infra_files()`` returns the
packaged ``.tf`` files; the ``bootstrap`` command copies them into a per-account
working directory before running tofu — the packaged copy is never mutated.
"""

from __future__ import annotations

from importlib import resources
from importlib.abc import Traversable

__all__ = ["bootstrap_infra_root", "bootstrap_infra_files"]


def bootstrap_infra_root() -> Traversable:
    """The packaged bootstrap infra directory as an importlib.resources Traversable."""
    return resources.files(__package__).joinpath("infra")


def bootstrap_infra_files() -> list[str]:
    """Names of the packaged infra files (Terraform/OpenTofu sources + lock)."""
    root = bootstrap_infra_root()
    return sorted(
        p.name for p in root.iterdir()
        if p.name.endswith((".tf", ".tftpl", ".hcl")) or p.name.endswith(".example")
    )
