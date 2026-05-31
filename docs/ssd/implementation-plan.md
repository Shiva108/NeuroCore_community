# NeuroCore Community Implementation Plan

## Objective

Keep the public community repository focused on the stable core package, public
contract docs, and contributor-safe tooling.

## Included In This Repository

- `src/neurocore/` core package
- `tests/` contract and behavior coverage
- `docs/ssd/` public architecture and specification docs
- `scripts/` bootstrap, validation, and OpenAPI snapshot helpers
- `.github/` CI and community scaffolding

## Intentionally Excluded

- private commit history
- personal operator workflows
- operational logs and runbooks
- concrete connector packages and provider-specific deployment guides
- sensitive or proprietary planning material

## Ongoing Work

1. keep the SSD docs aligned with public behavior
2. preserve parity across CLI, HTTP, and MCP interfaces
3. expand community-safe examples and contribution guidance
4. accept public changes here first, then port approved changes back upstream

## Validation

Primary checks for the community repo:

```bash
pytest
python scripts/validate_checkout.py
python scripts/generate_openapi_snapshot.py --check
```
