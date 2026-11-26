"""SQLite schema for the prototype (mirrors the 14 MySQL tables in the spec + auth_sessions)."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT NOT NULL DEFAULT '',
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, archived_at TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','finalized')),
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
  finalized_at TEXT, finalized_by INTEGER REFERENCES users(id), archived_at TEXT,
  parent_session_id INTEGER REFERENCES sessions(id));
CREATE TABLE IF NOT EXISTS session_projects (
  session_id INTEGER NOT NULL REFERENCES sessions(id), project_id INTEGER NOT NULL REFERENCES projects(id),
  PRIMARY KEY (session_id, project_id));
CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, mime_type TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, class TEXT NOT NULL CHECK (class IN ('INPUT','METHOD','OUTPUT')),
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifact_versions (
  id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL REFERENCES artifacts(id), version_no INTEGER NOT NULL,
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256), original_filename TEXT NOT NULL,
  uploaded_by INTEGER REFERENCES users(id), uploaded_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
  UNIQUE (artifact_id, version_no));
CREATE TABLE IF NOT EXISTS session_inputs (
  session_id INTEGER NOT NULL REFERENCES sessions(id), artifact_version_id INTEGER NOT NULL REFERENCES artifact_versions(id),
  added_by INTEGER REFERENCES users(id), added_at TEXT NOT NULL, PRIMARY KEY (session_id, artifact_version_id));
CREATE TABLE IF NOT EXISTS session_outputs (
  session_id INTEGER NOT NULL REFERENCES sessions(id), artifact_version_id INTEGER NOT NULL REFERENCES artifact_versions(id),
  added_by INTEGER REFERENCES users(id), added_at TEXT NOT NULL, PRIMARY KEY (session_id, artifact_version_id));
CREATE TABLE IF NOT EXISTS method_refs (
  id INTEGER PRIMARY KEY, session_id INTEGER UNIQUE NOT NULL REFERENCES sessions(id),
  repo_name TEXT NOT NULL DEFAULT '', repo_path TEXT NOT NULL DEFAULT '', commit_sha TEXT NOT NULL,
  branch TEXT NOT NULL DEFAULT '', paths TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
  verified INTEGER NOT NULL DEFAULT 0, commit_message TEXT NOT NULL DEFAULT '',
  snapshot_version_id INTEGER REFERENCES artifact_versions(id));
CREATE TABLE IF NOT EXISTS annotations (
  id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '',
  kv TEXT NOT NULL DEFAULT '{}', updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL,
  UNIQUE (entity_type, entity_id));
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS entity_tags (
  entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, tag_id INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY (entity_type, entity_id, tag_id));
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id INTEGER, details TEXT NOT NULL DEFAULT '{}', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth_sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), last_seen TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sp_project ON session_projects(project_id);
CREATE INDEX IF NOT EXISTS ix_si_version ON session_inputs(artifact_version_id);
CREATE INDEX IF NOT EXISTS ix_so_version ON session_outputs(artifact_version_id);
CREATE INDEX IF NOT EXISTS ix_av_artifact ON artifact_versions(artifact_id);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_log(at);
CREATE TRIGGER IF NOT EXISTS no_update_versions BEFORE UPDATE ON artifact_versions
  BEGIN SELECT RAISE(ABORT, 'artifact_versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_versions BEFORE DELETE ON artifact_versions
  BEGIN SELECT RAISE(ABORT, 'artifact_versions cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_blobs BEFORE DELETE ON blobs
  BEGIN SELECT RAISE(ABORT, 'blobs cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS no_update_audit BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_audit BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.close()
