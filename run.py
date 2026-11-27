"""Start the Analysis Tracker (Shiny for Python, served by uvicorn).

    .venv/bin/python run.py           # uses ./data; bootstraps admin/admin12345 on first run
    .venv/bin/python run.py --demo    # also loads demo projects and sessions on first run

Settings come from environment variables (same names as the spec's .env):
  DATA_DIR (default ./data), BLOB_DIR (default $DATA_DIR/blobs), REPOS_DIR (optional),
  MAX_UPLOAD_MB (500), APP_PORT (8000), APP_HOST (0.0.0.0), ADMIN_USERNAME, ADMIN_PASSWORD,
  LOAD_DEMO=1 (same as --demo), TRUSTED_ORIGINS (comma-separated, e.g. http://myserver:8000)

The same app can be started with the uvicorn CLI instead (see main.py):
    .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
import os
import sys

import uvicorn

from tracker.app import create_app
from tracker.services import Tracker


def build():
    data = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
    os.makedirs(data, exist_ok=True)
    t = Tracker(os.path.join(data, "tracker.sqlite3"), os.environ.get("BLOB_DIR", os.path.join(data, "blobs")),
                os.environ.get("REPOS_DIR"))
    admin_user = os.environ.get("ADMIN_USERNAME", "admin")
    created = t.bootstrap_admin(admin_user, os.environ.get("ADMIN_PASSWORD", "admin12345"))
    if created:
        print(f"Bootstrapped admin user '{admin_user}'.")
        if "--demo" in sys.argv or os.environ.get("LOAD_DEMO", "").lower() in ("1", "true", "yes"):
            from demo import load_demo
            load_demo(t, created)
            print("Loaded demo data.")
    return create_app(t, int(os.environ.get("MAX_UPLOAD_MB", "500")),
                      os.environ.get("TRUSTED_ORIGINS", "").split(","))


if __name__ == "__main__":
    host, port = os.environ.get("APP_HOST", "0.0.0.0"), int(os.environ.get("APP_PORT", "8000"))
    print(f"Analysis Tracker on http://localhost:{port}")
    uvicorn.run(build(), host=host, port=port, log_level="warning")
