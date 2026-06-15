setup:
	python scripts/bootstrap.py

format:
	black src tests scripts

test:
	pytest

lint:
	black --check src tests scripts
	flake8 src tests scripts

validate:
	python scripts/validate_checkout.py

openapi-check:
	python scripts/generate_openapi_snapshot.py --check

sentrux:
	sentrux check .
	sentrux gate .
