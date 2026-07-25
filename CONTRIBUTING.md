# Contributing to StrataCLI

StrataCLI is licensed **Apache-2.0** (see `LICENSE` and `NOTICE`). Contributions are
accepted under Apache-2.0.

## Ground rules

- By submitting a contribution you certify you have the right to license it under
  Apache-2.0 (DCO-style intent).
- Keep the licensing boundary: this repo covers the **CLI + StrataCI bootstrap runner
  only**. Do not add StrataBI Enterprise, StrataHQ, or commercial-module source here.
- Developer Edition paths must remain **HQ-free** (no StrataHQ entitlement call).

## Development

```bash
cd stratacli
pip install -e '.[dev]'
python -m pytest
python -m compileall src/strata
python -m build && python -m twine check dist/*
```

## Pull requests

- Discuss large changes in an issue first.
- Include tests for new behavior (e.g. source resolution, flag semantics).
- No secrets in code or fixtures; never commit `.env`/`terraform.tfvars`/state.

## Security

See [`SECURITY.md`](./SECURITY.md) — report vulnerabilities privately.
