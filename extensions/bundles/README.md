# Bundles

Curated bundle manifests group related NeuroCore ecosystem pieces into
documented capability sets without installing or loading anything at runtime.

Each bundle manifest:

- lives directly under this folder as `kebab-case.json`
- declares a display name, description, and one or more repo-relative items
- may reference curated surfaces under `extensions/`, `integrations/`,
  `recipes/`, `skills/`, `dashboards/`, `schemas/`, and `primitives/`
- may name optional Python extras from `pyproject.toml`
- may declare required boolean `NeuroCoreConfig` flags for the runtime shape

Bundles are curation metadata only in this tranche. They do not mutate the
environment, install dependencies, or auto-enable runtime features.
