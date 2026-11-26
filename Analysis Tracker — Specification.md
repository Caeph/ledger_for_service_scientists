# Analysis Tracker — Specification

Nov 26, 2026 · @KBF

_Revised Nov 26, 2026: a METHOD can be recorded as an uploaded script or notebook instead of, or as well as, a git commit. Users can unlink inputs and staged outputs from an open session. In an iteration, a re-uploaded file becomes a new version of the same-named file in the parent session._

## Purpose and context

Build a small, self-hosted web app that tracks analytical work. Each piece of work is recorded as which INPUT files, processed by which METHOD (code at a git commit, or the script file itself), produced which OUTPUT files. Every uploaded file is versioned and never overwritten.

The app is a helper organizer for analysts juggling about 10 projects at once. It answers three questions: what data belongs to project X, which version of the input produced this figure, and what is the latest final output.

- **Users:** 10–20 people, all on the institute's internal network. No external access.
- **Data:** research data only, with no GDPR or patient-data constraints. Files are small: Excel/TSV tables, scripts and notebooks, figures, PDFs, HTML reports. There is no big data.
- **Access:** a browser GUI for everything, including upload and download.

## Scope

v1 is a versioned file store with provenance links, a project dashboard, and local logins. Everything else is out.

**In scope for v1**

- Local user accounts with login. Every logged-in user can see everything.
- Projects used as labels on analytical sessions.
- Analytical sessions that link INPUT → METHOD → OUTPUT.
- Immutable, versioned file storage, uploaded manually through the browser.
- METHOD recorded as a git commit reference, an uploaded script or notebook, or both.
- Flexible annotations: free text, user tags, and optional key–value fields.
- A dashboard of all projects, search, browser download, and inline preview.
- An audit log of every write.
- Deployment with Docker Compose on local disk or a NAS mount.

**Out of scope**

- Deleting anything through the UI. Users never delete; see Core concepts.
- DataLad, git-annex, or any distributed sync.
- Cloud storage, external access, and SSO/LDAP.
- Per-project permissions.
- Backups, which are handled outside the tool. The tool only documents which volumes need backing up.
- Big data or streaming uploads.
- Running analyses or pipelines from inside the tool.
- Automatic file watching or auto-commit.

## Core concepts and invariants

The unit of work is the **analytical session**: INPUT processed by METHOD produces OUTPUT. Nothing a user stores is ever overwritten or deleted.

**Concepts**

- **Project:** a label with a name, description and tags. A session can carry one or more project labels. Most sessions will have exactly one.
- **Analytical session:** a titled record with free-text notes. It has a status of `open` or `finalized`.
- **Artifact:** a logical file (for example `clinical_table.xlsx`) with a class of INPUT, METHOD or OUTPUT. It has one or more **versions**.
- **Version:** one immutable upload of an artifact. Each version has a number (v1, v2, …), the SHA-256 hash of its content, the uploader, a timestamp and a note.
- **Method reference:** how the code that ran is identified. It is a git commit (repository name or path, commit SHA, branch), an uploaded script or notebook stored as a METHOD artifact version, or both. At least one of the two is required. Optionally it also lists the script or notebook paths used and a note.
- **Annotation:** free text, user tags, and optional key–value pairs. It attaches to projects, sessions and versions. The schema is flexible; no field is required except where stated below.

**Invariants** (enforce these in the service layer and cover them with tests)

1. **No overwrite.** Uploading a file whose name matches an existing artifact in the same session, or in the session it was iterated from (following `parent_session_id` back), always creates a new version. Users can also choose to make a new artifact instead. Stored bytes are never modified.
2. **Content-addressed storage.** Each blob is stored once at `blobs/<sha256[0:2]>/<sha256>`. Identical content reuses the blob but still gets its own version record.
3. **No deletion by users.** The UI has no delete. While a session is open, a user can *unlink* an input or a staged output from it. This removes only the link; the version and its blob stay stored, and the unlink is audited. An admin can *archive* a project or session, which hides it by default. Archived items stay viewable through a filter. Blobs are never removed.
4. **Provenance is mandatory.** A session can be finalized only if it has at least one INPUT version, exactly one method reference (with a commit SHA, an uploaded script, or both), and at least one OUTPUT version.
5. **OUTPUT upload means final.** OUTPUTs uploaded to an open session are *staged*; they become final only in a finalize step that the user confirms. After that the session is read-only, including its inputs, method and outputs. Annotations and tags can still be edited.
6. **Reuse, don't copy.** A new session can select existing INPUT versions from any session or project. It can also be created with "Start new session from this one", which pre-fills the same inputs and method reference.
7. **Everything is attributed.** Every write records the user and a UTC timestamp in the audit log.

## Data model

The model has 14 tables in MySQL 8, managed with Alembic migrations. Timestamps are UTC. Primary keys are integer `id` unless noted.

| Table | Key fields | Notes |
| --- | --- | --- |
| `users` | username (unique), display\_name, password\_hash, is\_admin, is\_active, created\_at | Admin creates accounts; no self-registration |
| `projects` | name (unique), description, created\_by, created\_at, archived\_at | Label entity shown on the dashboard |
| `sessions` | title, notes (text), status (`open`/`finalized`), created\_by, created\_at, finalized\_at, finalized\_by, archived\_at, parent\_session\_id | `parent_session_id` set by "Start new session from this one" |
| `session_projects` | session\_id, project\_id | Many-to-many; at least one project per session |
| `blobs` | sha256 (PK), size\_bytes, mime\_type, created\_at | Physical file in the blob store |
| `artifacts` | name, class (`INPUT`/`METHOD`/`OUTPUT`), created\_by, created\_at | Logical file; versions hang off it |
| `artifact_versions` | artifact\_id, version\_no, blob\_sha256, original\_filename, uploaded\_by, uploaded\_at, note | Unique (artifact\_id, version\_no); never updated after insert |
| `session_inputs` / `session_outputs` | session\_id, artifact\_version\_id, added\_by, added\_at | One INPUT version can appear in many sessions. Rows of an open session can be removed (unlink); rows of a finalized session never change |
| `method_refs` | session\_id (unique), repo\_name, repo\_path, commit\_sha (40 hex, or empty for a script-only method), branch, paths (JSON list), note, snapshot\_version\_id (nullable) | Code snapshot stored as a METHOD artifact version. At least one of commit\_sha and snapshot\_version\_id must be set |
| `annotations` | entity\_type (`project`/`session`/`artifact_version`), entity\_id, text, kv (JSON), updated\_by, updated\_at | One row per entity; editable after finalize |
| `tags` + `entity_tags` | tag name (unique, lowercase); entity\_type, entity\_id, tag\_id | Free user tags across all entities |
| `audit_log` | user\_id, action, entity\_type, entity\_id, details (JSON), at | Append-only |

The same blob may back several versions. Lineage is derived like this: OUTPUT version → its session → that session's INPUT versions and method ref. The reverse direction is INPUT version → every session that used it → their outputs.

## Workflows

A session moves one way, from `open` to `finalized`. All saving is manual.

1. **Create session.** The user enters a title, picks one or more project labels, and optionally adds notes and tags. Status is `open`.
2. **Add INPUT.** The user uploads new files (Excel, TSV, CSV or any other type) and/or selects existing INPUT versions from other sessions.
   - Same filename as an existing artifact in this session, or in its parent sessions → the app asks "new version of X" (default) or "new file". A new version replaces the older one in this session's list.
   - While the session is open, the user can unlink an input they no longer need. The version stays stored.
   - Each upload can carry a note and tags.
3. **Record METHOD.** The user uploads the script or notebook that was run, enters a git commit, or both. A note and the script or notebook paths are optional.
   - **Script upload:** stored as a METHOD artifact version. Uploading a script with the same name again creates a new version of it. Without a commit, the method shows a "script only" badge, and the script version stands in for the commit in lineage views.
   - **Git commit:** the repo name or path, the commit SHA and the branch. If `repo_path` is visible to the server (mounted read-only), validate it with `git cat-file -e <sha>^{commit}` and auto-fill the branch and commit message. If it isn't visible, accept the SHA after format validation (40 hex) and show a "not verified" badge.
   - Saving a method with neither a commit nor a script is refused. Editing other fields keeps the existing script.
4. **Finalize with OUTPUT.** The user uploads the outputs (figures, PDFs, HTML reports) and their notes. They are staged and can still be unlinked or replaced while the session is open. The app checks invariant 4, then shows a confirmation that this marks the session final and read-only. On confirm, the app sets `finalized`, `finalized_at` and `finalized_by` in one transaction.
5. **Iterate.** To change anything after finalizing, the user clicks "Start new session from this one". This creates a new `open` session that is pre-filled with the same inputs and method ref and linked through `parent_session_id`. Outputs are not copied. Uploading a file with the same name as a parent's output (for example a corrected figure) creates v2 of that artifact, so its whole history stays on one artifact page.
6. **Retrieve.** The user downloads any version, or downloads a whole session as a zip. The zip holds the inputs, outputs, method snapshot if present, and `manifest.json` with the hashes, versions, method ref and annotations.

Uploads are written to a temp file, hashed, then moved atomically into the blob store before the DB row is committed. If the DB commit fails, the orphaned blob is harmless because blobs are content-addressed.

## UI pages

The landing page after login is a dashboard of all projects. Every other page is one click from it.

| Page | Shows | Actions |
| --- | --- | --- |
| Login | Username and password | Log in |
| Dashboard (home) | One card or row per project: name, tags, number of sessions (open/finalized), last activity, latest OUTPUT thumbnail or file name. Recent activity feed (last 20 audit events). | New project, new session, filter by tag, show archived |
| Project | Description and annotations; sessions list (title, status, date, author, tags), newest first | New session, edit annotation and tags |
| Session | Three columns, INPUT · METHOD · OUTPUT, each listing versions with note, uploader and date; notes and tags; parent session link | Upload, select existing input, unlink input or staged output, record method, finalize (when open); start new session from this one; download zip |
| Artifact | Version history table; per version: hash, size, uploader, note; "used in sessions" (inputs) or "produced by session" (outputs) | Download any version, preview, annotate |
| Search | Results across projects, sessions and versions | Full-text search over titles, notes, annotations, tags and filenames |
| Admin | Users list; archived items | Create or deactivate a user, reset a password, archive or unarchive |

**Preview inline:** images (PNG, JPG, SVG), PDF (browser viewer), HTML reports (sandboxed `iframe`, no same-origin), and text, TSV or CSV (first 200 rows as a table). Excel files are download-only in v1.

**UX rules:** a status badge on every session; a "not verified" badge on unverified method refs; a "script only" badge on methods without a commit; no delete buttons anywhere; and a confirmation dialog before finalize.

## Architecture and tech stack

The app is a Shiny for Python frontend over a thin Python service layer, with MySQL for metadata and a content-addressed blob store on disk.

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Frontend | Shiny for Python (served by uvicorn) |
| Service layer | Plain Python package (`tracker/services/`) holding all business rules and invariants; the UI never touches the DB or files directly |
| ORM and migrations | SQLAlchemy 2.x + Alembic |
| Database | MySQL 8 (own container) |
| Blob store | Local directory or NAS mount at `/data/blobs`, content-addressed by SHA-256 |
| Git access | `git` CLI via subprocess, read-only, against repos mounted at `/repos` (optional) |
| Auth | Shiny session plus server-side session table; passwords hashed with argon2 (`argon2-cffi`) |
| Tests | pytest, using a MySQL test container or SQLite for unit tests of the service layer |

**Suggested layout:** `tracker/models.py`, `tracker/services/` (projects, sessions, uploads, methods, search, audit), `tracker/storage.py` (blob store), `tracker/ui/` (one module per page), `migrations/`, `tests/`, `docker-compose.yml`, `.env.example`.

Keep the service layer framework-free so a CLI or REST API can be added later without touching the business rules.

## Deployment, configuration and security

The tool deploys with one `docker compose up -d` on a Linux host inside the internal network.

**Containers and volumes**

- `app`: the Shiny app on port 8000 (configurable).
- `db`: MySQL 8, not exposed outside the compose network.
- Volumes: `blob_store` → `/data/blobs` (local disk or NAS bind mount), `db_data` → MySQL data dir, optional read-only `/repos`.
- The README must list `blob_store` and `db_data` as the two things to back up. Also include a `make dump` target for a consistent MySQL dump.

**Configuration (`.env`)**

- `DB_URL`, `BLOB_DIR`, `REPOS_DIR`, `MAX_UPLOAD_MB` (default 500), `APP_PORT`, `SECRET_KEY`.
- `ADMIN_USERNAME` and `ADMIN_PASSWORD` are used once to bootstrap the first admin.

**Security (internal-network baseline)**

- Every page except login requires authentication. Sessions expire after 12 h of inactivity.
- Passwords are hashed with argon2. No password is ever logged.
- Uploaded HTML renders only in a sandboxed `iframe`. Downloads are served with `Content-Disposition: attachment` unless previewed.
- Filenames are sanitized and never used as storage paths, because the store uses hashes only.
- The git integration is read-only and passes the SHA as an argument, never through a shell string.
- HTTPS is optional via a reverse proxy (Caddy or nginx) and is documented but not required for v1.

## Acceptance criteria

v1 is done when every item below passes as an automated test or as a scripted manual check.

- [ ] Uploading `data.xlsx` twice to the same session creates v1 and v2. Both are downloadable and byte-identical to what was uploaded.
- [ ] Uploading identical content under two names creates one blob and two version records.
- [ ] No UI path deletes a project, session, artifact, version or blob, and no service function does either.
- [ ] On an open session, unlinking an input or staged output removes only the link: the version stays downloadable and an `audit_log` row is written. Unlinking is refused on a finalized session.
- [ ] Finalize is refused unless the session has ≥1 INPUT, exactly 1 method ref and ≥1 OUTPUT. The error message names what is missing.
- [ ] After finalize, uploads, input selection and method edits are rejected by the service layer, not only hidden in the UI. Annotations and tags remain editable.
- [ ] From any OUTPUT version, the artifact page shows its session, input versions and method (commit SHA, script version, or both). From any INPUT version, it lists every session that used it.
- [ ] "Start new session from this one" pre-fills the inputs and method and sets `parent_session_id`.
- [ ] In an iteration, uploading an OUTPUT with the same name as one in the parent session creates the next version of that artifact. The parent session still shows its original version.
- [ ] A method ref against a mounted repo is validated, and an invalid SHA is rejected. An unmounted repo shows "not verified".
- [ ] A method can be saved with only an uploaded script and the session can then be finalized. A method with neither a commit nor a script is refused, and nothing is stored. Re-uploading a script with the same name creates a new version.
- [ ] The session zip contains every file plus a `manifest.json` whose SHA-256 values match the files.
- [ ] The dashboard lists all non-archived projects with session counts and last activity, and loads in under 2 s with 50 projects and 1,000 sessions.
- [ ] Search finds a session by a word in its notes, a tag, or an input filename.
- [ ] Every write produces an `audit_log` row with the user and timestamp.
- [ ] Unauthenticated requests to any page redirect to login. A non-admin user can't reach Admin.
- [ ] `docker compose up -d` on a clean host plus the `.env` values gives a working app with the bootstrap admin.

## Build order, assumptions and open questions

Build the service layer and its tests first, then the UI. Each step should be shippable on its own.

1. Docker Compose skeleton, models, migrations, and bootstrap admin.
2. Blob store, upload service with versioning, and audit log, with tests for invariants 1–3 and 7.
3. Sessions, inputs, method refs and finalize, with tests for invariants 4–6.
4. Login, dashboard, and project and session pages.
5. Artifact page, lineage views, previews, and zip download.
6. Search, admin page, and README (including backup notes).

**Assumptions made in this spec** (confirm or change before building)

- Provenance is recorded at session level. Every OUTPUT of a session derives from all of its INPUTs and its method. Per-output input links are not tracked.
- Exactly one method ref per session. If several repos are used, record the main one and mention the others in notes.
- Admins can archive but never hard-delete.
- Excel files are download-only, with no inline preview.

**Open questions**

- Will the git repos live on the server, so they can be mounted at `/repos` and verified, or on each analyst's own machine (SHA only, unverified)? Analysts without a usable repo can upload the script instead.
- Is there a target date for v1?
- Should the app later integrate with the eio app's database, or stay fully standalone?
