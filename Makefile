PYTHON ?= .venv/bin/python
UVICORN ?= .venv/bin/uvicorn
UV ?= uv

.PHONY: backend-install backend-dev test web-dev dev

backend-install:
	$(UV) venv --python 3.11 .venv || $(UV) venv --python 3.9 .venv
	$(UV) pip install --python $(PYTHON) -e ".[dev]"

backend-dev:
	$(UVICORN) engine.api.app:app --reload

test:
	$(PYTHON) -m pytest -q

web-dev:
	cd web && npm run dev

dev:
	@set -e; \
	BACK_PID=""; WEB_PID=""; \
	trap 'if [ -n "$$BACK_PID" ]; then kill $$BACK_PID 2>/dev/null || true; fi; if [ -n "$$WEB_PID" ]; then kill $$WEB_PID 2>/dev/null || true; fi' INT TERM EXIT; \
	echo "[dev] starting backend on http://localhost:8000"; \
	$(UV) run --python $(PYTHON) uvicorn engine.api.app:app --reload & \
	BACK_PID=$$!; \
	echo "[dev] starting frontend on http://localhost:5173"; \
	(cd web && npm run dev) & \
	WEB_PID=$$!; \
	wait $$BACK_PID $$WEB_PID
