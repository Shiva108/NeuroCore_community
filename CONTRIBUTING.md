# Contributing to NeuroCore Community

## Workflow

NeuroCore is built contract-first. Before changing behavior, review:

- `docs/ssd/architecture.md`
- `docs/ssd/specification.md`
- `docs/ssd/implementation-plan.md`
- `docs/ssd/source-matrix.md`

Prefer small, focused changes that keep the SSD package and implementation in
sync.

The public repo accepts both core-package changes and ecosystem contributions.
The core package lives under `src/neurocore/`; reusable ecosystem work belongs
in the top-level contribution surfaces documented below.

## Local Development

1. Bootstrap the local workspace:

```bash
python scripts/bootstrap.py
```

2. Activate the virtual environment:

```bash
source .venv/bin/activate
```

3. Run the standard checks:

```bash
make test
make lint
make validate
make openapi-check
```

4. If you change architecture boundaries or dependency direction, also run:

```bash
make sentrux
```

## Reference Stack

The default local operator path is the mirror-first security profile documented
in [docs/reference-stack.md](docs/reference-stack.md). The hosted companion
path is documented in [docs/hosted-stack.md](docs/hosted-stack.md).

## Implementation Rules

- Write or update tests with every behavior change.
- Keep public contracts aligned with the SSD docs.
- Keep `docs/ssd/source-matrix.md` updated when repo guidance or named sources
  change implementation expectations.
- Preserve parity across library, CLI, HTTP, and MCP request/response
  contracts.
- Keep optional surfaces behind explicit config gates.

## Ecosystem Categories

| Category | Purpose | Review Mode |
| --- | --- | --- |
| `extensions/` | Higher-level builds that compose multiple NeuroCore capabilities | Curated |
| `primitives/` | Reusable patterns depended on by multiple ecosystem modules | Curated |
| `recipes/` | Standalone workflows or walkthroughs built on current core behavior | Open |
| `skills/` | Reusable prompt and skill packs for AI clients using NeuroCore | Open |
| `dashboards/` | UI shells or frontend add-ons that build on the reference app | Open |
| `integrations/` | External connectors and ingestion or delivery surfaces | Open |
| `schemas/` | Supplemental schema patterns and storage extensions | Open |

Every ecosystem contribution must include:

- `README.md`
- `metadata.json`
- any category-specific artifact required by the template, such as `SKILL.md`

Use each category's `_template/` folder as the starting point. `extensions/`
and `primitives/` must declare `"curation": "curated"` in `metadata.json`.

## Security and Publication

- Never commit secrets or local-only environment files.
- Use `.env.example`, `secrets.json.example`, and `preferences.json.example` as
  checked-in references for local configuration.
- Sanitize screenshots, logs, sample payloads, and provider URLs before opening
  a PR.
- Keep the community repo free of private operator data and internal-only docs.
