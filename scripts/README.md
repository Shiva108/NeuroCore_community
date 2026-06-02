# Scripts

The public repository keeps a deliberately small script surface:

- `bootstrap.py`: create `.venv`, install dependencies, and initialize the
  operator-home env file
- `neurocore_checkout.py`: run the CLI with repo-local Python and env handling
- `validate_checkout.py`: run repository contract validation
- `generate_openapi_snapshot.py`: refresh or verify the checked-in OpenAPI schema
- `mock_openai_compatible.py`: local-only OpenAI-compatible mock for consensus
  development and demos

The scripts remain flat because there are only a few public entry points and
tests/docs reference them directly.
