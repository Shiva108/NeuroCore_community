# Configuration Guide

NeuroCore Community reads runtime configuration from environment variables.
Start with `.env.example`, but keep real values in the operator home outside the
repository.

## Sensitive Values

Treat these as local-only:

- API keys
- bearer tokens
- hosted database URLs
- provider-specific secrets

Use `secrets.json.example` and `preferences.json.example` as templates only.
