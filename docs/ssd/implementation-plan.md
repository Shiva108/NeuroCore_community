# NeuroCore Community Implementation Plan

## Objective

Keep the public repository focused on the package, tests, documentation, and
community-safe tooling.

## Included

- `src/neurocore/`
- `tests/`
- `docs/ssd/`
- `scripts/`
- `.github/`

## Validation

```bash
pytest
python scripts/validate_checkout.py
python scripts/generate_openapi_snapshot.py --check
```
