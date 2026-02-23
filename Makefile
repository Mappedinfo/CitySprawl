PYTHON ?= .venv/bin/python
UVICORN ?= .venv/bin/uvicorn
UV ?= uv
BACKEND_HOST ?= 127.0.0.1
BACKEND_PORT ?= 8000
FRONTEND_HOST ?= 127.0.0.1
FRONTEND_PORT ?= 5173
DEV_PID_DIR ?= .tmp/dev
BACK_PID_FILE := $(DEV_PID_DIR)/backend.pid
WEB_PID_FILE := $(DEV_PID_DIR)/frontend.pid

.PHONY: backend-install backend-dev test web-dev dev dev-stop

backend-install:
	$(UV) venv --python 3.11 .venv || $(UV) venv --python 3.9 .venv
	$(UV) pip install --python $(PYTHON) -e ".[dev]"

backend-dev:
	$(UVICORN) engine.api.app:app --reload --host $(BACKEND_HOST) --port $(BACKEND_PORT)

test:
	$(PYTHON) -m pytest -q

web-dev:
	cd web && npm run dev -- --host $(FRONTEND_HOST) --port $(FRONTEND_PORT) --strictPort

dev-stop:
	@set -e; \
	mkdir -p $(DEV_PID_DIR); \
	for file in "$(BACK_PID_FILE)" "$(WEB_PID_FILE)"; do \
		if [ -f "$$file" ]; then \
			pid=$$(cat "$$file" 2>/dev/null || true); \
			if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then \
				echo "[dev-stop] stopping pid $$pid from $$file"; \
				kill "$$pid" 2>/dev/null || true; \
			fi; \
			rm -f "$$file"; \
		fi; \
	done; \
	for port in $(BACKEND_PORT) $(FRONTEND_PORT); do \
		pids=$$(lsof -tiTCP:$$port -sTCP:LISTEN 2>/dev/null || true); \
		if [ -n "$$pids" ]; then \
			echo "[dev-stop] freeing port $$port (pids: $$pids)"; \
			kill $$pids 2>/dev/null || true; \
			sleep 0.4; \
			still=$$(lsof -tiTCP:$$port -sTCP:LISTEN 2>/dev/null || true); \
			if [ -n "$$still" ]; then \
				echo "[dev-stop] force killing port $$port listeners (pids: $$still)"; \
				kill -9 $$still 2>/dev/null || true; \
			fi; \
		fi; \
	done

dev:
	@set -e; \
	mkdir -p $(DEV_PID_DIR); \
	$(MAKE) --no-print-directory dev-stop; \
	BACK_PID=""; WEB_PID=""; \
	trap 'if [ -n "$$BACK_PID" ]; then kill $$BACK_PID 2>/dev/null || true; fi; if [ -n "$$WEB_PID" ]; then kill $$WEB_PID 2>/dev/null || true; fi; rm -f "$(BACK_PID_FILE)" "$(WEB_PID_FILE)"' INT TERM EXIT; \
	echo "[dev] starting backend on http://$(BACKEND_HOST):$(BACKEND_PORT)"; \
	$(UV) run --python $(PYTHON) uvicorn engine.api.app:app --reload --host $(BACKEND_HOST) --port $(BACKEND_PORT) & \
	BACK_PID=$$!; \
	echo "$$BACK_PID" > "$(BACK_PID_FILE)"; \
	echo "[dev] starting frontend on http://$(FRONTEND_HOST):$(FRONTEND_PORT)"; \
	(cd web && npm run dev -- --host $(FRONTEND_HOST) --port $(FRONTEND_PORT) --strictPort) & \
	WEB_PID=$$!; \
	echo "$$WEB_PID" > "$(WEB_PID_FILE)"; \
	wait $$BACK_PID $$WEB_PID
