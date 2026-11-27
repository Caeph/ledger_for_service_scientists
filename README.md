# Analysis Tracker — prototype

A working prototype of the spec in `Analysis Tracker — Specification.md`. It tracks analytical sessions (INPUT → METHOD → OUTPUT) with immutable, versioned, content-addressed file storage. The UI is **Shiny for Python**.

## Run it

Everything lives in the project venv `.venv` (Python 3.12+). This machine's Python has no `ensurepip`, so the venv is created without pip and the system pip installs into it:

```bash
python3 -m venv --without-pip .venv
python3 -m pip --python .venv/bin/python install -r requirements.txt -r requirements-dev.txt
```

(On a machine where `python3 -m venv .venv` works normally, `.venv/bin/pip install -r …` does the same.)

```bash
.venv/bin/python run.py --demo     # first run: creates ./data, the admin user and demo projects
```

Open http://localhost:8000. The `--demo` flag only does something when `./data` is empty.

- Admin: `admin` / `admin12345` (change with `ADMIN_USERNAME` / `ADMIN_PASSWORD` before the first run)
- Demo analyst: `jana` / `analyst123`

Settings are environment variables, with the same names as the spec's `.env`: `DATA_DIR`, `BLOB_DIR`, `REPOS_DIR`, `MAX_UPLOAD_MB` (500), `APP_PORT` (8000), `APP_HOST`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `LOAD_DEMO`, `TRUSTED_ORIGINS`.

A guided tour with sample files is in `walkthrough/WALKTHROUGH.md`.

### On a server in your local network (without Docker)

On the server, start it like any uvicorn app:

```bash
./deploy.sh serve
```

This creates `.venv` if it's missing, then runs `uvicorn main:app --host 0.0.0.0 --port 8000 --reload` and prints the server's network address. Open `http://<server's IP>:8000` in the browser on your own computer. `--reload` restarts the app when a `.py` file changes; `./deploy.sh serve --no-reload` turns that off. Stop it with Ctrl+C.

- Settings come from `.env` if there is one (`APP_PORT`, `ADMIN_PASSWORD`, `LOAD_DEMO`, `TRUSTED_ORIGINS`, …). Variables set on the command line win, e.g. `APP_PORT=9000 ./deploy.sh serve`.
- The data goes to `./data`, or `DATA_DIR`. That's separate from the Docker instance's `./docker-data`.
- If the Docker instance is running, it already uses port 8000; give `serve` another port.

You can also run uvicorn directly: `.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload`.

**If the login works but the page then stays empty or greys out**, the browser's live connection (websocket) was refused. The server log says why:

```
Rejected websocket: browser origin 'http://…' does not match host '…'
```

The app only accepts that connection from the address the server itself sees, to stop other web pages from using your login. When you go through a reverse proxy, or a port forward that changes the address, allow your browser's address explicitly:

```bash
TRUSTED_ORIGINS=http://localhost:9000 ./deploy.sh serve
```

Use exactly what your browser's address bar shows (scheme, host, port); separate several with commas. A plain SSH tunnel (`ssh -L 8000:localhost:8000 server`, then `http://localhost:8000`) works without this.

## Run it in Docker

```bash
./deploy.sh            # builds the image, runs the tests in it, starts it, waits until healthy
```

On the first run the script creates `.env` from `.env.example`, with a random admin password, and prints the login. Other commands: `build`, `test`, `up`, `down`, `restart`, `status`, `logs`, `backup`, `export`, `load FILE` (`./deploy.sh help`). Inside the container the app starts the same way as with `serve`, as `uvicorn main:app --host 0.0.0.0 --port 8000`, just without `--reload`. If your user isn't in the `docker` group, the script falls back to `sudo docker`.

- **One image** (`Dockerfile`): `python:3.12-slim` with the requirements, starting `uvicorn main:app --host 0.0.0.0 --port 8000 --reload`. `compose.yaml` runs it as your uid with a read-only root filesystem, and adds the health check and the data settings (`DATA_DIR=/data`). `./deploy.sh test` runs pytest inside the built image, so a failing test stops the deploy.
  - The image includes `git`, so commit SHAs are checked against the repositories in `REPOS_PATH` (mounted at `/repos`). `safe.directory '*'` lets git read repositories owned by another user.
- **Data** goes to `DATA_PATH` (default `./docker-data`), bind-mounted at `/data`. The container runs as the uid that owns that folder, so the files stay yours on the host. Git repositories go in `REPOS_PATH` (default `./repos`), mounted read-only at `/repos`.
- **Network:** by default the app listens on `0.0.0.0:8000`, so other machines on the local network reach it at `http://<this machine's IP>:8000`. `deploy.sh` prints that address. Set `BIND_ADDR=127.0.0.1` in `.env` to allow only this machine.
  - Docker opens published ports in the firewall itself, past `ufw`, so the app is reachable on **every** network the machine joins. On a laptop that also goes on public Wi-Fi, switch to `127.0.0.1` there, or stop the app with `./deploy.sh down`.
  - Plain HTTP sends passwords and files unencrypted over the network. That's acceptable on a trusted LAN. Otherwise put a reverse proxy with HTTPS in front; it must pass the `Host` header through and allow websockets, because the app rejects websockets whose `Origin` doesn't match `Host`.
- **Backup:** `./deploy.sh backup` takes a consistent SQLite snapshot from the running app and writes `backups/tracker-<time>.tar.gz` with the snapshot and all blobs.
- **No registry?** `./deploy.sh export` writes `dist/analysis-tracker-latest.tar.gz`. Copy it to the server with `deploy.sh`, `compose.yaml` and `.env.example`, then run `./deploy.sh load dist/…tar.gz && ./deploy.sh up`.
- `LOAD_DEMO=1` in `.env` loads the demo projects on the first start (same as `--demo`).

### Step by step: running it locally

**1. Docker access.** If `docker info` fails with "permission denied", either:
- add yourself to the `docker` group (one-time), then log out and back in, or run `newgrp docker`:
  ```bash
  sudo usermod -aG docker $USER
  ```
  Membership in the `docker` group is effectively root access on the machine.
- or skip that: `deploy.sh` then uses `sudo docker` and asks for your password (run it from a terminal).

**2. (Optional) Demo data.** On the first run the script creates `.env` with the demo off. To start with the demo projects, create `.env` yourself first:
```bash
cp .env.example .env && chmod 600 .env
```
Then set `ADMIN_PASSWORD=` (at least 8 characters) and `LOAD_DEMO=1`. The demo only loads into an empty data folder.

**3. Build and start.**
```bash
./deploy.sh
```
The first build downloads the Python base image and packages, so it needs internet and takes a few minutes. It ends with `Up: http://localhost:8000` and the address for other machines on your network (`On the local network: http://<IP>:8000`), plus the admin login if the script created `.env`. If port 8000 is taken, set `APP_PORT` in `.env` and run it again.

**4. Log in** at http://localhost:8000, or from another machine at the network address the script printed:
- `admin` with `ADMIN_PASSWORD` from `.env`
- `jana` / `analyst123` if you loaded the demo

The walkthrough in `walkthrough/WALKTHROUGH.md` works the same here. The Docker instance keeps its data in `./docker-data`, separate from the venv's `./data`.

**5. Day to day.**

| Command | What it does |
| --- | --- |
| `./deploy.sh status` | Shows the container and its health |
| `./deploy.sh logs` | Follows the app output (Ctrl+C to leave) |
| `./deploy.sh backup` | Writes `backups/tracker-<time>.tar.gz` while the app runs |
| `./deploy.sh down` | Stops the app; the data stays in `docker-data` |
| `./deploy.sh up` | Starts it again without rebuilding |
| `./deploy.sh` | Rebuilds after code changes and restarts (`deploy --skip-tests` skips the tests) |

To start over with no data: run `./deploy.sh down`, delete `docker-data`, then run `./deploy.sh`. This deletes all uploaded files and history.

**Troubleshooting**
- **`exec /opt/venv/bin/uvicorn: operation not permitted` (or another `exec …: operation not permitted`), app never healthy.** Docker is the snap package (`snap list docker`), and its AppArmor profile blocks containers started with `no-new-privileges`. `deploy.sh` detects the snap and writes `NO_NEW_PRIVILEGES=false` to `.env`; if it didn't, set it yourself and run `./deploy.sh up`. Docker's own apt packages (docker-ce) don't have this problem.
- **`apparmor="DENIED" … docker_cli … bind` in the kernel log.** Harmless messages from the snap; they don't affect the app.
- **Permission errors under `docker-data`.** The container runs as the uid in `APP_UID`/`APP_GID` in `.env`, which should own `docker-data`. Fix the folder's owner, or clear both values so the script detects them again.
- **The app fails to start.** The script stops the container, shows its logs and Docker's error, and exits. `./deploy.sh logs` shows more.

## Tests

```bash
.venv/bin/python -m pytest -q
```

- `tests/test_services.py` (26 tests): invariants 1–7 and most acceptance criteria. Covers versioning, blob dedup, no-delete, finalize rules, read-only after finalize, script-only methods, lineage, iteration versioning, zip manifest hashes, search, audit log, argon2 with legacy-hash upgrade, and dashboard speed with 50 projects and 1,000 sessions.
- `tests/test_app.py` (9 tests): the HTTP layer. Covers the login gate on every page and file route, the HttpOnly cookie, logout, attachment downloads, sandboxed HTML previews, and rejection of cross-origin websockets.

The Shiny pages themselves were checked by clicking through the full walkthrough in a browser. There are no automated UI tests yet (that would need Playwright).

## Layout

```
tracker/services.py    all business rules and invariants (framework-free)
tracker/storage.py     content-addressed blob store
tracker/db.py          schema (SQLite in the prototype)
tracker/app.py         ASGI app: Shiny server + Starlette routes for /login, /logout, /files/…
tracker/ui/            one module per page: dashboard, project, session, artifact, search, admin
run.py                 starts uvicorn (python run.py)
main.py                ASGI entry for the uvicorn CLI (uvicorn main:app)
demo.py                demo data
```

**How the UI is wired:**
- **Routing.** Pages are addressed by URL hash (`#/sessions/3`), so links, Back/Forward and bookmarks work. The Shiny server re-renders the page whenever the hash changes or something is written.
- **Forms.** Main forms use native Shiny inputs. Per-row buttons (unlink, archive, per-version annotations, user actions) use a small `data-act` bridge that sends one Shiny input event.
- **Login.** Login and logout are plain Starlette routes, because Shiny can't set an HttpOnly cookie. The Shiny session reads that cookie and checks it on every action, so a login expires after 12 h of inactivity.
- **Files.** Downloads, previews and zips are authenticated Starlette routes, served with `Content-Disposition: attachment` or a sandboxing CSP.

## Where it still differs from the spec

| Spec | Prototype |
| --- | --- |
| MySQL 8 + SQLAlchemy/Alembic | SQLite with the same tables plus `auth_sessions`, so Docker runs one app container and no database container |
| Docker Compose, `make dump` | `compose.yaml` + `deploy.sh` (`./deploy.sh backup` replaces `make dump`) |
| Git validation against `/repos` | Implemented and checked by hand against a real repository (a known commit is verified with its message, an unknown SHA is rejected); no automated test yet. Without a mounted repo the method shows "not verified", as the spec describes. |

**Changed from the spec on request:**
- **A method can be just the script.** A method record needs a git commit SHA, an uploaded script or notebook, or both. With a script only, the session shows a "script only" badge, and lineage shows the script and its version instead of a commit. Uploading a script with the same name again makes a new version of it. Finalize still requires exactly one method record.

- **Unlinking on open sessions.** On an open session you can *unlink* an input or a staged output. The file version stays stored, and the unlink is audited. Outputs are staged until you finalize.
- **Iteration versioning.** In an iteration, uploading a file with the same name as one in the **parent** session makes a new version of it.

All three changes are now part of the specification (see its revision note).

## Backups (for the real deployment)

Back up the blob directory (`data/blobs`) and the database (`data/tracker.sqlite3` here; the MySQL `db_data` volume in the target design).
