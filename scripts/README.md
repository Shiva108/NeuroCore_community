# Scripts

The repository keeps scripts flat on purpose because they are public entry
points referenced directly by docs, tests, and CI.

## Setup and validation

- `bootstrap.py`: create `.venv`, install dependencies, and initialize the
  operator-home env file
- `validate_checkout.py`: run repository contract validation
- `generate_openapi_snapshot.py`: refresh or verify the checked-in OpenAPI schema
- `build_llms_docs.py`: regenerate `llms.txt` artifacts from the checked-in docs

## Operator workflows

- `neurocore_checkout.py`: run the CLI with repo-local Python and env handling
- `security_workflow.py`: guided capture, query, and reporting flows
- `mirror_hosted_proof.py`: mirror-mode verification and recovery proof
- `retrieval_eval.py`: local retrieval evaluation harness

## Utilities

- `fix_supabase_security_definer.py`: revoke public execution on exposed
  Supabase `SECURITY DEFINER` functions
- `mock_openai_compatible.py`: local-only OpenAI-compatible mock for consensus
  development and demos
