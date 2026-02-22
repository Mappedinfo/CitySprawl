# GeoAI Urban Sandbox (CityGen)

Hybrid-driven urban generation MVP with a deterministic procedural core and AI-ready enrichment interfaces.

## Monorepo Structure

- `engine/`: Python generation engine + FastAPI API
- `web/`: React + TypeScript lightweight demo
- `examples/`: sample configs
- `docs/`: notes and architecture docs

## Quick Start (Backend, uv)

```bash
uv venv --python 3.11 .venv   # or use local python version if 3.11 is unavailable
uv pip install --python .venv/bin/python -e ".[dev]"

# Option A (recommended): run inside the project env without activating it
uv run --python .venv/bin/python uvicorn engine.api.app:app --reload

# Option B: call the venv binary directly
.venv/bin/uvicorn engine.api.app:app --reload

# Option C: activate first, then use uvicorn
source .venv/bin/activate
uvicorn engine.api.app:app --reload
```

## Quick Start (Frontend)

```bash
cd web
npm install
npm run dev
```

By default the frontend expects backend API at `http://localhost:8000`.
