"""ASGI entry point for the uvicorn CLI, e.g. on a server in the local network:

    .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Settings are the same environment variables as for run.py (DATA_DIR, LOAD_DEMO, TRUSTED_ORIGINS, ...).
Note: --port here sets the port; APP_HOST/APP_PORT only apply to run.py.
"""
from run import build

app = build()
