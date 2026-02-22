PYTHON ?= .venv/bin/python
UVICORN ?= .venv/bin/uvicorn
UV ?= uv

.PHONY: backend-install backend-dev test web-dev

backend-install:
	$(UV) venv --python 3.11 .venv || $(UV) venv --python 3.9 .venv
	$(UV) pip install --python $(PYTHON) -e ".[dev]"

backend-dev:
	$(UVICORN) engine.api.app:app --reload

test:
	$(PYTHON) -m pytest -q

web-dev:
	cd web && npm run dev
