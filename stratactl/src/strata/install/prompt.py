"""Interactive (and non-interactive) prompting engine.

The engine walks a schema and fills a config dict, asking only for values that
are missing (unless ``update=True``). IO is injected via an ``IO`` object so the
wizard is fully unit-testable without a TTY, and so non-interactive automation
can drive it from existing config + defaults.

This module does no validation logic of its own — it calls the named validators
from ``validators.py`` — and no persistence — the caller saves the returned
config via ``store``. Each concern stays in its own module.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

from strata.errors import ConfigError
from strata.install import store, validators
from strata.install.schema import Field, InstallSchema


class IO:
    """Console IO. Replace in tests with a scripted fake."""

    def ask(self, prompt: str, default: Optional[str], secret: bool) -> str:
        suffix = f" [{default}]" if default not in (None, "") else ""
        if secret:
            import getpass
            raw = getpass.getpass(f"{prompt}{suffix}: ")
        else:
            raw = input(f"{prompt}{suffix}: ")
        raw = raw.strip()
        return raw if raw else ("" if default is None else str(default))

    def say(self, message: str = "") -> None:
        print(message)


@dataclass
class ScriptedIO(IO):
    """Deterministic IO for tests: pops scripted answers in order."""

    answers: list[str]
    log: list[str] = None  # type: ignore

    def __post_init__(self):
        self.log = []

    def ask(self, prompt: str, default: Optional[str], secret: bool) -> str:
        self.log.append(prompt)
        if not self.answers:
            return "" if default is None else str(default)
        raw = self.answers.pop(0).strip()
        return raw if raw else ("" if default is None else str(default))

    def say(self, message: str = "") -> None:
        self.log.append(f"SAY:{message}")


def _coerce(field: Field, raw: Any) -> Any:
    """Turn a raw string (or existing config value) into the field's type."""
    if field.type == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("y", "yes", "true", "1", "on")
    if field.type == "list":
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [p.strip() for p in str(raw).split(",") if p.strip()]
    if field.type == "int":
        return int(raw)
    return str(raw).strip()


def _validate(field: Field, value: Any) -> Optional[str]:
    """Return an error string, or None if valid."""
    fn = validators.get(field.validator)
    if field.required:
        if (field.type == "list" and not value) or (field.type != "list" and value in (None, "")):
            return "a value is required"
    if fn is None:
        return None
    try:
        if field.type == "list":
            for item in value:
                fn(item)
        elif value not in (None, ""):
            fn(value)
    except ValueError as exc:
        return str(exc)
    return None


def _default_display(field: Field, current: Any) -> Optional[str]:
    val = current if current is not None else field.default
    if val is None:
        return None
    if field.type == "list":
        return ",".join(str(x) for x in val) if val else ""
    if field.type == "bool":
        return "yes" if val else "no"
    return str(val)


class Wizard:
    def __init__(self, schema: InstallSchema, config: dict[str, Any],
                 io: Optional[IO] = None, interactive: bool = True,
                 update: bool = False, section_for: Optional[Callable[[Field], str]] = None):
        self.schema = schema
        self.config = config
        self.io = io or IO()
        self.interactive = interactive
        self.update = update
        # Allows module flows to redirect a field's storage section.
        self.section_for = section_for or (lambda f: f.section)

    def _gate_ok(self, field: Field) -> bool:
        if not field.when:
            return True
        other_key, expected = field.when
        flat = store.flatten(self.config)
        return flat.get(other_key) == expected

    def run(self) -> dict[str, Any]:
        missing: list[str] = []
        current_section = None
        for field in self.schema.fields:
            if not self._gate_ok(field):
                continue
            section = self.section_for(field)
            existing = store.get(self.config, section, field.key, None)

            # Reuse existing values unless we're explicitly updating.
            if existing is not None and not self.update:
                continue

            if self.interactive and field.section != current_section:
                current_section = field.section
                self.io.say()
                self.io.say(f"# {field.section}")

            if not self.interactive:
                value = existing if existing is not None else field.default
                value = _coerce(field, value) if value is not None else value
                err = _validate(field, value)
                if err:
                    missing.append(f"{field.key} ({err})")
                    continue
                if value is not None:
                    store.set_(self.config, section, field.key, value)
                continue

            # interactive: prompt with validation loop
            value = self._prompt_field(field, existing)
            store.set_(self.config, section, field.key, value)

        if missing:
            raise ConfigError(
                "non-interactive init is missing required values: " + ", ".join(missing)
                + "\nProvide them in the config file or run `strata init` interactively."
            )
        return self.config

    def _prompt_field(self, field: Field, existing: Any) -> Any:
        prompt = field.prompt
        if field.help:
            self.io.say(f"  ({field.help})")
        if field.choices:
            prompt += f" ({'/'.join(str(c) for c in field.choices)})"
        default = _default_display(field, existing)
        while True:
            raw = self.io.ask(prompt, default, field.secret)
            value = _coerce(field, raw)
            if field.choices and str(value) not in [str(c) for c in field.choices]:
                self.io.say(f"  ! choose one of: {', '.join(str(c) for c in field.choices)}")
                continue
            err = _validate(field, value)
            if err:
                self.io.say(f"  ! {err}")
                if not self.interactive:
                    raise ConfigError(f"{field.key}: {err}")
                continue
            return value
