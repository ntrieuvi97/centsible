# Contributing to Centsible

Thanks for helping. Small, focused pull requests are easiest to review.

## Set up

```bash
git clone https://github.com/ntrieuvi97/centsible.git && cd centsible
python3 -m pip install -e . pytest
python3 -m pytest -q
```

The project uses only the Python standard library at runtime. Please do not add runtime dependencies.

## Before you open a PR

1. Add or update a test for the behavior you changed (area: fact injection, loop guard, telemetry or hooks).
2. Run `python3 -m pytest -q` and confirm it passes.
3. If you change user-visible behavior, update the README and any example it quotes. README output must come from a real run.
4. Keep claims measurable. Avoid superlatives and unverified numbers.

## Good first issues

Look for the `good first issue` label. If you are unsure whether an idea fits, open an issue first.

## Reporting security problems

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.
