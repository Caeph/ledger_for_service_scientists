"""Service layer: every business rule and invariant lives here. Framework-free.

The UI calls only these functions; it never touches the DB or blob files directly.
There is deliberately no function that deletes a project, session, artifact, version or blob.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .db import connect, init_db
from .storage import BlobStore

SESSION_TTL = timedelta(hours=12)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ENTITY_TYPES = ("project", "session", "artifact_version")


class ServiceError(Exception):
    """A rule was violated; message is safe to show to the user."""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- passwords
# argon2 (argon2-cffi). Hashes from the earlier stdlib prototype ("scrypt$...") still verify
# and are upgraded to argon2 on the next successful login.
_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def _verify_legacy_scrypt(pw: str, stored: str) -> bool:
    try:
        _, s, d = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(s), n=2**14, r=8, p=1)
        return hmac.compare_digest(dk, base64.b64decode(d))
    except Exception:
        return False


def verify_password(pw: str, stored: str) -> bool:
    if stored.startswith("scrypt$"):
        return _verify_legacy_scrypt(pw, stored)
    try:
        return _ph.verify(stored, pw)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def needs_rehash(stored: str) -> bool:
    return stored.startswith("scrypt$") or _ph.check_needs_rehash(stored)


def clean_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name)
    return name[:200] or "unnamed"


def parse_tags(s) -> list[str]:
    if isinstance(s, (list, tuple)):
        items = s
    else:
        items = re.split(r"[,;]", s or "")
    out = []
    for t in items:
        t = t.strip().lower()
        if t and t not in out:
            out.append(t[:64])
    return out


class Tracker:
    def __init__(self, db_path: str, blob_dir: str, repos_dir: str | None = None):
        self.db_path = db_path
        self.blobs = BlobStore(blob_dir)
        self.repos_dir = repos_dir
        init_db(db_path)

    # ------------------------------------------------------------ plumbing
    @contextmanager
    def tx(self):
        conn = connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def ro(self):
        conn = connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _audit(self, c, user_id, action, entity_type, entity_id, **details):
        c.execute("INSERT INTO audit_log(user_id, action, entity_type, entity_id, details, at) VALUES (?,?,?,?,?,?)",
                  (user_id, action, entity_type, entity_id, json.dumps(details, default=str), now()))

    def _user(self, c, user_id):
        u = c.execute("SELECT * FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone()
        if not u:
            raise ServiceError("Unknown or inactive user.")
        return u

    def _require_admin(self, c, user_id):
        if not self._user(c, user_id)["is_admin"]:
            raise ServiceError("Admin rights required.")

    def _session(self, c, session_id):
        s = c.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not s:
            raise ServiceError(f"Session {session_id} not found.")
        return s

    def _require_open(self, c, session_id):
        s = self._session(c, session_id)
        if s["status"] != "open":
            raise ServiceError("Session is finalized and read-only. Use “Start new session from this one”.")
        return s

    def _set_tags(self, c, entity_type, entity_id, tags):
        c.execute("DELETE FROM entity_tags WHERE entity_type=? AND entity_id=?", (entity_type, entity_id))
        for t in parse_tags(tags):
            c.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (t,))
            tid = c.execute("SELECT id FROM tags WHERE name=?", (t,)).fetchone()[0]
            c.execute("INSERT OR IGNORE INTO entity_tags VALUES (?,?,?)", (entity_type, entity_id, tid))

    def _add_tags(self, c, entity_type, entity_id, tags):
        existing = self._tags(c, entity_type, entity_id)
        self._set_tags(c, entity_type, entity_id, existing + parse_tags(tags))

    def _tags(self, c, entity_type, entity_id) -> list[str]:
        return [r[0] for r in c.execute(
            "SELECT t.name FROM entity_tags e JOIN tags t ON t.id=e.tag_id WHERE e.entity_type=? AND e.entity_id=? "
            "ORDER BY t.name", (entity_type, entity_id))]

    def _upsert_annotation(self, c, user_id, entity_type, entity_id, text=None, kv=None):
        row = c.execute("SELECT * FROM annotations WHERE entity_type=? AND entity_id=?",
                        (entity_type, entity_id)).fetchone()
        text = text if text is not None else (row["text"] if row else "")
        kv = kv if kv is not None else (json.loads(row["kv"]) if row else {})
        c.execute("INSERT INTO annotations(entity_type, entity_id, text, kv, updated_by, updated_at) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT(entity_type, entity_id) DO UPDATE SET text=excluded.text, kv=excluded.kv, "
                  "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                  (entity_type, entity_id, text, json.dumps(kv), user_id, now()))

    # ------------------------------------------------------------ users & auth
    def bootstrap_admin(self, username: str, password: str):
        with self.tx() as c:
            if c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return None
            cur = c.execute("INSERT INTO users(username, display_name, password_hash, is_admin, created_at) "
                            "VALUES (?,?,?,1,?)", (username, username, hash_password(password), now()))
            self._audit(c, cur.lastrowid, "bootstrap_admin", "user", cur.lastrowid, username=username)
            return cur.lastrowid

    def create_user(self, actor, username, display_name, password, is_admin=False):
        username = (username or "").strip().lower()
        if not re.match(r"^[a-z0-9._-]{2,40}$", username):
            raise ServiceError("Username must be 2–40 chars of a-z, 0-9, . _ -")
        if len(password or "") < 8:
            raise ServiceError("Password must be at least 8 characters.")
        with self.tx() as c:
            self._require_admin(c, actor)
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ServiceError("Username already exists.")
            cur = c.execute("INSERT INTO users(username, display_name, password_hash, is_admin, created_at) "
                            "VALUES (?,?,?,?,?)", (username, display_name or username, hash_password(password),
                                                   1 if is_admin else 0, now()))
            self._audit(c, actor, "create_user", "user", cur.lastrowid, username=username, is_admin=bool(is_admin))
            return cur.lastrowid

    def set_user_active(self, actor, user_id, active: bool):
        with self.tx() as c:
            self._require_admin(c, actor)
            if user_id == actor and not active:
                raise ServiceError("You cannot deactivate yourself.")
            c.execute("UPDATE users SET is_active=? WHERE id=?", (1 if active else 0, user_id))
            if not active:
                c.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            self._audit(c, actor, "activate_user" if active else "deactivate_user", "user", user_id)

    def reset_password(self, actor, user_id, password):
        if len(password or "") < 8:
            raise ServiceError("Password must be at least 8 characters.")
        with self.tx() as c:
            self._require_admin(c, actor)
            c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
            c.execute("DELETE FROM auth_sessions WHERE user_id=?", (user_id,))
            self._audit(c, actor, "reset_password", "user", user_id)  # never log the password

    def login(self, username, password) -> str | None:
        with self.tx() as c:
            u = c.execute("SELECT * FROM users WHERE username=? AND is_active=1",
                          ((username or "").strip().lower(),)).fetchone()
            if not u or not verify_password(password or "", u["password_hash"]):
                return None
            if needs_rehash(u["password_hash"]):
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), u["id"]))
            token = secrets.token_urlsafe(32)
            c.execute("INSERT INTO auth_sessions VALUES (?,?,?)", (token, u["id"], now()))
            self._audit(c, u["id"], "login", "user", u["id"])
            return token

    def logout(self, token):
        with self.tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE token=?", (token,))

    def user_for_token(self, token):
        if not token:
            return None
        with self.tx() as c:
            row = c.execute("SELECT a.last_seen, u.* FROM auth_sessions a JOIN users u ON u.id=a.user_id "
                            "WHERE a.token=? AND u.is_active=1", (token,)).fetchone()
            if not row:
                return None
            last = datetime.strptime(row["last_seen"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last > SESSION_TTL:
                c.execute("DELETE FROM auth_sessions WHERE token=?", (token,))
                return None
            c.execute("UPDATE auth_sessions SET last_seen=? WHERE token=?", (now(), token))
            return dict(row)

    def list_users(self):
        with self.ro() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, username, display_name, is_admin, is_active, created_at FROM users ORDER BY username")]

    # ------------------------------------------------------------ projects
    def create_project(self, actor, name, description="", tags=""):
        name = (name or "").strip()
        if not name:
            raise ServiceError("Project name is required.")
        with self.tx() as c:
            self._user(c, actor)
            if c.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
                raise ServiceError("A project with this name already exists.")
            cur = c.execute("INSERT INTO projects(name, description, created_by, created_at) VALUES (?,?,?,?)",
                            (name, description or "", actor, now()))
            self._set_tags(c, "project", cur.lastrowid, tags)
            self._audit(c, actor, "create_project", "project", cur.lastrowid, name=name)
            return cur.lastrowid

    def update_project_description(self, actor, project_id, description):
        with self.tx() as c:
            self._user(c, actor)
            c.execute("UPDATE projects SET description=? WHERE id=?", (description or "", project_id))
            self._audit(c, actor, "update_project", "project", project_id)

    def list_projects(self, include_archived=False):
        with self.ro() as c:
            q = "SELECT * FROM projects" + ("" if include_archived else " WHERE archived_at IS NULL") + " ORDER BY name"
            return [dict(r) for r in c.execute(q)]

    def archive(self, actor, entity_type, entity_id, archived=True):
        if entity_type not in ("project", "session"):
            raise ServiceError("Only projects and sessions can be archived.")
        table = "projects" if entity_type == "project" else "sessions"
        with self.tx() as c:
            self._require_admin(c, actor)
            c.execute(f"UPDATE {table} SET archived_at=? WHERE id=?", (now() if archived else None, entity_id))
            self._audit(c, actor, "archive" if archived else "unarchive", entity_type, entity_id)

    # ------------------------------------------------------------ sessions
    def create_session(self, actor, title, project_ids, notes="", tags="", parent_session_id=None):
        title = (title or "").strip()
        project_ids = sorted({int(p) for p in project_ids or []})
        if not title:
            raise ServiceError("Session title is required.")
        if not project_ids:
            raise ServiceError("Pick at least one project.")
        with self.tx() as c:
            self._user(c, actor)
            for pid in project_ids:
                if not c.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone():
                    raise ServiceError(f"Project {pid} not found.")
            cur = c.execute("INSERT INTO sessions(title, notes, created_by, created_at, parent_session_id) "
                            "VALUES (?,?,?,?,?)", (title, notes or "", actor, now(), parent_session_id))
            sid = cur.lastrowid
            for pid in project_ids:
                c.execute("INSERT INTO session_projects VALUES (?,?)", (sid, pid))
            self._set_tags(c, "session", sid, tags)
            self._audit(c, actor, "create_session", "session", sid, title=title, projects=project_ids,
                        parent_session_id=parent_session_id)
            return sid

    def update_session_notes(self, actor, session_id, notes):
        """Notes are free text and treated like annotations: editable after finalize."""
        with self.tx() as c:
            self._user(c, actor)
            self._session(c, session_id)
            c.execute("UPDATE sessions SET notes=? WHERE id=?", (notes or "", session_id))
            self._audit(c, actor, "update_session_notes", "session", session_id)

    def start_from(self, actor, session_id):
        """Invariant 6: new open session pre-filled with the same inputs and method ref."""
        with self.ro() as c:
            s = self._session(c, session_id)
            pids = [r[0] for r in c.execute("SELECT project_id FROM session_projects WHERE session_id=?", (session_id,))]
            tags = self._tags(c, "session", session_id)
        new_id = self.create_session(actor, s["title"] + " (iteration)", pids, notes="", tags=tags,
                                     parent_session_id=session_id)
        with self.tx() as c:
            for r in c.execute("SELECT artifact_version_id FROM session_inputs WHERE session_id=?", (session_id,)).fetchall():
                c.execute("INSERT INTO session_inputs VALUES (?,?,?,?)", (new_id, r[0], actor, now()))
            m = c.execute("SELECT * FROM method_refs WHERE session_id=?", (session_id,)).fetchone()
            if m:
                c.execute("INSERT INTO method_refs(session_id, repo_name, repo_path, commit_sha, branch, paths, note, "
                          "verified, commit_message, snapshot_version_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (new_id, m["repo_name"], m["repo_path"], m["commit_sha"], m["branch"], m["paths"], m["note"],
                           m["verified"], m["commit_message"], m["snapshot_version_id"]))
            self._audit(c, actor, "prefill_from_parent", "session", new_id, parent_session_id=session_id)
        return new_id

    # ------------------------------------------------------------ uploads
    def _store_version(self, c, actor, artifact_id, data, filename, note):
        sha, size = self.blobs.put(data)  # blob lands before the row commits; orphan is harmless
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        c.execute("INSERT OR IGNORE INTO blobs VALUES (?,?,?,?)", (sha, size, mime, now()))
        n = c.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM artifact_versions WHERE artifact_id=?",
                      (artifact_id,)).fetchone()[0]
        cur = c.execute("INSERT INTO artifact_versions(artifact_id, version_no, blob_sha256, original_filename, "
                        "uploaded_by, uploaded_at, note) VALUES (?,?,?,?,?,?,?)",
                        (artifact_id, n, sha, filename, actor, now(), note or ""))
        return cur.lastrowid, n, sha

    def _artifact_in_session(self, c, session_id, cls, name):
        link = {"INPUT": "session_inputs", "OUTPUT": "session_outputs"}.get(cls)
        if link:
            q = (f"SELECT DISTINCT a.id FROM {link} l JOIN artifact_versions v ON v.id=l.artifact_version_id "
                 f"JOIN artifacts a ON a.id=v.artifact_id WHERE l.session_id=? AND a.class=? AND a.name=?")
        else:
            q = ("SELECT DISTINCT a.id FROM method_refs m JOIN artifact_versions v ON v.id=m.snapshot_version_id "
                 "JOIN artifacts a ON a.id=v.artifact_id WHERE m.session_id=? AND a.class=? AND a.name=?")
        r = c.execute(q, (session_id, cls, name)).fetchone()
        return r[0] if r else None

    def _artifact_in_lineage(self, c, session_id, cls, name):
        """Same name in this session, else in its parent chain (so an iteration's figure becomes v2, not a new file)."""
        seen = set()
        while session_id and session_id not in seen:
            seen.add(session_id)
            art = self._artifact_in_session(c, session_id, cls, name)
            if art is not None:
                return art
            session_id = c.execute("SELECT parent_session_id FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
        return None

    def existing_names(self, session_id, cls):
        """Names that an upload would version: this session's plus its parent chain's."""
        link = "session_inputs" if cls == "INPUT" else "session_outputs"
        names, seen = [], set()
        with self.ro() as c:
            while session_id and session_id not in seen:
                seen.add(session_id)
                for r in c.execute(
                        f"SELECT DISTINCT a.name FROM {link} l JOIN artifact_versions v ON v.id=l.artifact_version_id "
                        f"JOIN artifacts a ON a.id=v.artifact_id WHERE l.session_id=?", (session_id,)):
                    if r[0] not in names:
                        names.append(r[0])
                session_id = c.execute("SELECT parent_session_id FROM sessions WHERE id=?",
                                       (session_id,)).fetchone()[0]
        return names

    def upload(self, actor, session_id, cls, filename, data: bytes, note="", tags="", mode="version",
               max_bytes=None):
        """Invariant 1: a matching name in this session becomes a new version unless mode='new'."""
        if cls not in ("INPUT", "OUTPUT"):
            raise ServiceError("Upload class must be INPUT or OUTPUT.")
        if max_bytes is not None and len(data) > max_bytes:
            raise ServiceError(f"File exceeds the upload limit of {max_bytes // (1024*1024)} MB.")
        filename = clean_filename(filename)
        with self.tx() as c:
            self._user(c, actor)
            self._require_open(c, session_id)
            art = self._artifact_in_lineage(c, session_id, cls, filename) if mode == "version" else None
            if art is None:
                art = c.execute("INSERT INTO artifacts(name, class, created_by, created_at) VALUES (?,?,?,?)",
                                (filename, cls, actor, now())).lastrowid
            else:  # replace older version of this artifact in the session's list with the new one
                link = "session_inputs" if cls == "INPUT" else "session_outputs"
                c.execute(f"DELETE FROM {link} WHERE session_id=? AND artifact_version_id IN "
                          f"(SELECT id FROM artifact_versions WHERE artifact_id=?)", (session_id, art))
            vid, n, sha = self._store_version(c, actor, art, data, filename, note)
            link = "session_inputs" if cls == "INPUT" else "session_outputs"
            c.execute(f"INSERT INTO {link} VALUES (?,?,?,?)", (session_id, vid, actor, now()))
            if tags:
                self._set_tags(c, "artifact_version", vid, tags)
            self._audit(c, actor, f"upload_{cls.lower()}", "artifact_version", vid, session_id=session_id,
                        artifact_id=art, version_no=n, sha256=sha, filename=filename)
            return vid

    def select_input(self, actor, session_id, version_id):
        with self.tx() as c:
            self._user(c, actor)
            self._require_open(c, session_id)
            v = c.execute("SELECT v.id, a.class FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id "
                          "WHERE v.id=?", (version_id,)).fetchone()
            if not v or v["class"] != "INPUT":
                raise ServiceError("Only existing INPUT versions can be selected.")
            c.execute("INSERT OR IGNORE INTO session_inputs VALUES (?,?,?,?)", (session_id, version_id, actor, now()))
            self._audit(c, actor, "select_input", "session", session_id, artifact_version_id=version_id)

    def remove_input_link(self, actor, session_id, version_id):
        """Unlinks an input from an OPEN session (draft editing). The version itself is untouched."""
        with self.tx() as c:
            self._user(c, actor)
            self._require_open(c, session_id)
            c.execute("DELETE FROM session_inputs WHERE session_id=? AND artifact_version_id=?", (session_id, version_id))
            self._audit(c, actor, "unlink_input", "session", session_id, artifact_version_id=version_id)

    def remove_output_link(self, actor, session_id, version_id):
        with self.tx() as c:
            self._user(c, actor)
            self._require_open(c, session_id)
            c.execute("DELETE FROM session_outputs WHERE session_id=? AND artifact_version_id=?", (session_id, version_id))
            self._audit(c, actor, "unlink_output", "session", session_id, artifact_version_id=version_id)

    # ------------------------------------------------------------ method
    def _verify_git(self, repo_path, sha):
        """Returns (verified, branch, message) or raises ServiceError if repo visible but SHA missing."""
        if not self.repos_dir or not repo_path or not shutil.which("git"):
            return False, "", ""
        root = os.path.realpath(self.repos_dir)
        full = os.path.realpath(repo_path if os.path.isabs(repo_path) else os.path.join(root, repo_path))
        if not full.startswith(root + os.sep) or not os.path.isdir(full):
            return False, "", ""
        run = lambda *a: subprocess.run(["git", "-C", full, *a], capture_output=True, text=True, timeout=10)
        if run("rev-parse", "--git-dir").returncode != 0:
            return False, "", ""
        if run("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            raise ServiceError(f"Commit {sha} does not exist in repository {repo_path}.")
        msg = run("log", "-1", "--format=%s", sha).stdout.strip()
        br = run("branch", "--contains", sha, "--format=%(refname:short)").stdout.split()
        return True, (br[0] if br else ""), msg

    def set_method(self, actor, session_id, repo_name, repo_path, commit_sha, branch="", paths="", note="",
                   snapshot: tuple[str, bytes] | None = None):
        """A method is a git commit, an uploaded script/notebook snapshot, or both. At least one is required."""
        sha = (commit_sha or "").strip().lower()
        if sha and not SHA_RE.match(sha):
            raise ServiceError("Commit SHA must be the full 40-character hex hash.")
        if sha and not (repo_name or repo_path):
            raise ServiceError("Repository name or path is required with a commit SHA.")
        if snapshot and not snapshot[1]:
            raise ServiceError("The uploaded script is empty.")
        path_list = [p.strip() for p in re.split(r"[\n,]", paths) if p.strip()] if isinstance(paths, str) else list(paths)
        verified, git_branch, msg = self._verify_git(repo_path, sha) if sha else (False, "", "")
        with self.tx() as c:
            self._user(c, actor)
            self._require_open(c, session_id)
            old = c.execute("SELECT snapshot_version_id FROM method_refs WHERE session_id=?", (session_id,)).fetchone()
            snap_id = old["snapshot_version_id"] if old else None
            if not sha and not snapshot and not snap_id:
                raise ServiceError("Record the method as a git commit SHA, or upload the script/notebook you ran "
                                   "(or both).")
            if snapshot:
                fname = clean_filename(snapshot[0])
                art = self._artifact_in_session(c, session_id, "METHOD", fname)
                if art is None:
                    art = c.execute("INSERT INTO artifacts(name, class, created_by, created_at) VALUES (?,?,?,?)",
                                    (fname, "METHOD", actor, now())).lastrowid
                snap_id, _, _ = self._store_version(c, actor, art, snapshot[1], fname,
                                                    f"snapshot @ {sha[:10]}" if sha else "script snapshot")
            c.execute("INSERT INTO method_refs(session_id, repo_name, repo_path, commit_sha, branch, paths, note, verified, "
                      "commit_message, snapshot_version_id) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE "
                      "SET repo_name=excluded.repo_name, repo_path=excluded.repo_path, commit_sha=excluded.commit_sha, "
                      "branch=excluded.branch, paths=excluded.paths, note=excluded.note, verified=excluded.verified, "
                      "commit_message=excluded.commit_message, snapshot_version_id=excluded.snapshot_version_id",
                      (session_id, repo_name or "", repo_path or "", sha, branch or git_branch, json.dumps(path_list),
                       note or "", 1 if verified else 0, msg, snap_id))
            self._audit(c, actor, "set_method", "session", session_id, commit_sha=sha, verified=verified,
                        snapshot_version_id=snap_id)

    # ------------------------------------------------------------ finalize
    def finalize_problems(self, session_id) -> list[str]:
        with self.ro() as c:
            return self._finalize_problems(c, session_id)

    def _finalize_problems(self, c, session_id):
        p = []
        if not c.execute("SELECT 1 FROM session_inputs WHERE session_id=?", (session_id,)).fetchone():
            p.append("at least one INPUT version")
        if c.execute("SELECT COUNT(*) FROM method_refs WHERE session_id=?", (session_id,)).fetchone()[0] != 1:
            p.append("exactly one METHOD reference")
        if not c.execute("SELECT 1 FROM session_outputs WHERE session_id=?", (session_id,)).fetchone():
            p.append("at least one OUTPUT version")
        return p

    def finalize(self, actor, session_id):
        """Invariant 4 + 5: checks provenance, then sets finalized fields in one transaction."""
        with self.tx() as c:
            self._user(c, actor)
            self._require_open(c, session_id)
            missing = self._finalize_problems(c, session_id)
            if missing:
                raise ServiceError("Cannot finalize — the session is missing: " + "; ".join(missing) + ".")
            t = now()
            c.execute("UPDATE sessions SET status='finalized', finalized_at=?, finalized_by=? WHERE id=?",
                      (t, actor, session_id))
            self._audit(c, actor, "finalize", "session", session_id)

    # ------------------------------------------------------------ annotations
    def annotate(self, actor, entity_type, entity_id, text=None, kv=None, tags=None):
        """Annotations and tags stay editable after finalize."""
        if entity_type not in ENTITY_TYPES:
            raise ServiceError("Bad entity type.")
        if isinstance(kv, str):
            kv = parse_kv(kv)
        with self.tx() as c:
            self._user(c, actor)
            if text is not None or kv is not None:
                self._upsert_annotation(c, actor, entity_type, entity_id, text, kv)
            if tags is not None:
                self._set_tags(c, entity_type, entity_id, tags)
            self._audit(c, actor, "annotate", entity_type, entity_id)

    def annotation(self, entity_type, entity_id):
        with self.ro() as c:
            r = c.execute("SELECT a.*, u.display_name AS by_name FROM annotations a LEFT JOIN users u ON u.id=a.updated_by "
                          "WHERE entity_type=? AND entity_id=?", (entity_type, entity_id)).fetchone()
            d = dict(r) if r else {"text": "", "kv": "{}"}
            d["kv"] = json.loads(d["kv"])
            d["tags"] = self._tags(c, entity_type, entity_id)
            return d

    # ------------------------------------------------------------ reads
    def dashboard(self, include_archived=False, tag=None):
        with self.ro() as c:
            where = [] if include_archived else ["p.archived_at IS NULL"]
            args = []
            if tag:
                where.append("p.id IN (SELECT entity_id FROM entity_tags e JOIN tags t ON t.id=e.tag_id "
                             "WHERE e.entity_type='project' AND t.name=?)")
                args.append(tag.lower())
            q = ("SELECT p.*, "
                 "SUM(CASE WHEN s.status='open' AND s.archived_at IS NULL THEN 1 ELSE 0 END) AS n_open, "
                 "SUM(CASE WHEN s.status='finalized' AND s.archived_at IS NULL THEN 1 ELSE 0 END) AS n_final, "
                 "MAX(COALESCE(s.finalized_at, s.created_at)) AS last_activity "
                 "FROM projects p LEFT JOIN session_projects sp ON sp.project_id=p.id "
                 "LEFT JOIN sessions s ON s.id=sp.session_id "
                 + ("WHERE " + " AND ".join(where) if where else "") +
                 " GROUP BY p.id ORDER BY last_activity IS NULL, last_activity DESC, p.name")
            rows = [dict(r) for r in c.execute(q, args)]
            for r in rows:
                r["tags"] = self._tags(c, "project", r["id"])
                lo = c.execute(
                    "SELECT v.id, v.original_filename, v.version_no, b.mime_type, a.id AS artifact_id FROM sessions s "
                    "JOIN session_projects sp ON sp.session_id=s.id JOIN session_outputs o ON o.session_id=s.id "
                    "JOIN artifact_versions v ON v.id=o.artifact_version_id JOIN artifacts a ON a.id=v.artifact_id "
                    "JOIN blobs b ON b.sha256=v.blob_sha256 WHERE sp.project_id=? AND s.status='finalized' "
                    "AND s.archived_at IS NULL ORDER BY s.finalized_at DESC, v.id DESC LIMIT 1", (r["id"],)).fetchone()
                r["latest_output"] = dict(lo) if lo else None
                last_v = c.execute(
                    "SELECT MAX(v.uploaded_at) FROM session_projects sp JOIN session_inputs i ON i.session_id=sp.session_id "
                    "JOIN artifact_versions v ON v.id=i.artifact_version_id WHERE sp.project_id=?", (r["id"],)).fetchone()[0]
                if last_v and (not r["last_activity"] or last_v > r["last_activity"]):
                    r["last_activity"] = last_v
            all_tags = [x[0] for x in c.execute(
                "SELECT DISTINCT t.name FROM tags t JOIN entity_tags e ON e.tag_id=t.id WHERE e.entity_type='project' "
                "ORDER BY t.name")]
            return rows, all_tags

    def recent_activity(self, limit=20):
        with self.ro() as c:
            return [dict(r) for r in c.execute(
                "SELECT l.*, u.display_name FROM audit_log l LEFT JOIN users u ON u.id=l.user_id "
                "WHERE l.action != 'login' ORDER BY l.id DESC LIMIT ?", (limit,))]

    def audit(self, limit=200):
        with self.ro() as c:
            return [dict(r) for r in c.execute(
                "SELECT l.*, u.display_name FROM audit_log l LEFT JOIN users u ON u.id=l.user_id "
                "ORDER BY l.id DESC LIMIT ?", (limit,))]

    def project(self, project_id, include_archived=False):
        with self.ro() as c:
            p = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not p:
                raise ServiceError("Project not found.")
            p = dict(p)
            p["tags"] = self._tags(c, "project", project_id)
            sessions = [dict(r) for r in c.execute(
                "SELECT s.*, u.display_name AS author FROM sessions s JOIN session_projects sp ON sp.session_id=s.id "
                "LEFT JOIN users u ON u.id=s.created_by WHERE sp.project_id=? "
                + ("" if include_archived else "AND s.archived_at IS NULL ") +
                "ORDER BY s.created_at DESC, s.id DESC", (project_id,))]
            for s in sessions:
                s["tags"] = self._tags(c, "session", s["id"])
            return p, sessions

    def _versions(self, c, link_table, session_id):
        rows = [dict(r) for r in c.execute(
            f"SELECT v.*, a.name, a.class, a.id AS artifact_id, b.size_bytes, b.mime_type, u.display_name AS uploader, "
            f"(SELECT MAX(version_no) FROM artifact_versions WHERE artifact_id=a.id) AS latest_no "
            f"FROM {link_table} l JOIN artifact_versions v ON v.id=l.artifact_version_id "
            f"JOIN artifacts a ON a.id=v.artifact_id JOIN blobs b ON b.sha256=v.blob_sha256 "
            f"LEFT JOIN users u ON u.id=v.uploaded_by WHERE l.session_id=? ORDER BY a.name, v.version_no",
            (session_id,))]
        for r in rows:
            r["tags"] = self._tags(c, "artifact_version", r["id"])
        return rows

    def session(self, session_id):
        with self.ro() as c:
            s = dict(self._session(c, session_id))
            s["author"] = (c.execute("SELECT display_name FROM users WHERE id=?", (s["created_by"],)).fetchone() or [""])[0]
            if s["finalized_by"]:
                s["finalizer"] = c.execute("SELECT display_name FROM users WHERE id=?", (s["finalized_by"],)).fetchone()[0]
            s["projects"] = [dict(r) for r in c.execute(
                "SELECT p.id, p.name FROM projects p JOIN session_projects sp ON sp.project_id=p.id WHERE sp.session_id=?",
                (session_id,))]
            s["tags"] = self._tags(c, "session", session_id)
            s["inputs"] = self._versions(c, "session_inputs", session_id)
            s["outputs"] = self._versions(c, "session_outputs", session_id)
            m = c.execute("SELECT * FROM method_refs WHERE session_id=?", (session_id,)).fetchone()
            s["method"] = dict(m) if m else None
            if m:
                s["method"]["paths"] = json.loads(m["paths"])
                if m["snapshot_version_id"]:
                    s["method"]["snapshot"] = dict(c.execute(
                        "SELECT v.*, a.name, a.id AS artifact_id FROM artifact_versions v JOIN artifacts a "
                        "ON a.id=v.artifact_id WHERE v.id=?", (m["snapshot_version_id"],)).fetchone())
            if s["parent_session_id"]:
                s["parent"] = dict(c.execute("SELECT id, title FROM sessions WHERE id=?",
                                             (s["parent_session_id"],)).fetchone())
            s["children"] = [dict(r) for r in c.execute(
                "SELECT id, title, status FROM sessions WHERE parent_session_id=?", (session_id,))]
            return s

    def artifact(self, artifact_id):
        with self.ro() as c:
            a = c.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not a:
                raise ServiceError("Artifact not found.")
            a = dict(a)
            a["versions"] = []
            for v in c.execute("SELECT v.*, b.size_bytes, b.mime_type, u.display_name AS uploader FROM artifact_versions v "
                               "JOIN blobs b ON b.sha256=v.blob_sha256 LEFT JOIN users u ON u.id=v.uploaded_by "
                               "WHERE v.artifact_id=? ORDER BY v.version_no DESC", (artifact_id,)):
                v = dict(v)
                v["tags"] = self._tags(c, "artifact_version", v["id"])
                ann = c.execute("SELECT text, kv FROM annotations WHERE entity_type='artifact_version' AND entity_id=?",
                                (v["id"],)).fetchone()
                v["annotation"] = {"text": ann["text"], "kv": json.loads(ann["kv"])} if ann else {"text": "", "kv": {}}
                v["used_in"] = [dict(r) for r in c.execute(
                    "SELECT s.id, s.title, s.status FROM session_inputs i JOIN sessions s ON s.id=i.session_id "
                    "WHERE i.artifact_version_id=? ORDER BY s.id", (v["id"],))]
                v["produced_by"] = []
                for s in c.execute("SELECT s.id, s.title, s.status FROM session_outputs o JOIN sessions s "
                                   "ON s.id=o.session_id WHERE o.artifact_version_id=?", (v["id"],)):
                    s = dict(s)
                    s["inputs"] = [dict(r) for r in c.execute(
                        "SELECT v2.id, v2.version_no, v2.original_filename, v2.artifact_id, v2.blob_sha256 "
                        "FROM session_inputs i JOIN artifact_versions v2 ON v2.id=i.artifact_version_id WHERE i.session_id=?",
                        (s["id"],))]
                    m = c.execute("SELECT m.*, sv.original_filename AS snapshot_name, sv.version_no AS snapshot_no, "
                                  "sv.artifact_id AS snapshot_artifact_id FROM method_refs m LEFT JOIN artifact_versions sv "
                                  "ON sv.id=m.snapshot_version_id WHERE m.session_id=?", (s["id"],)).fetchone()
                    s["method"] = dict(m) if m else None
                    v["produced_by"].append(s)
                v["snapshot_for"] = [dict(r) for r in c.execute(
                    "SELECT s.id, s.title, s.status FROM method_refs m JOIN sessions s ON s.id=m.session_id "
                    "WHERE m.snapshot_version_id=?", (v["id"],))]
                a["versions"].append(v)
            return a

    def version(self, version_id):
        with self.ro() as c:
            v = c.execute("SELECT v.*, a.name, a.class, b.mime_type, b.size_bytes FROM artifact_versions v "
                          "JOIN artifacts a ON a.id=v.artifact_id JOIN blobs b ON b.sha256=v.blob_sha256 WHERE v.id=?",
                          (version_id,)).fetchone()
            if not v:
                raise ServiceError("Version not found.")
            return dict(v)

    def read_version(self, version_id) -> tuple[dict, bytes]:
        v = self.version(version_id)
        return v, self.blobs.read(v["blob_sha256"])

    def selectable_inputs(self, exclude_session_id=None, q=""):
        with self.ro() as c:
            like = f"%{q.lower()}%"
            return [dict(r) for r in c.execute(
                "SELECT v.id, v.version_no, v.original_filename, v.uploaded_at, a.name, "
                "(SELECT GROUP_CONCAT(DISTINCT p.name) FROM session_inputs i JOIN session_projects sp ON sp.session_id=i.session_id "
                " JOIN projects p ON p.id=sp.project_id WHERE i.artifact_version_id=v.id) AS projects "
                "FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id WHERE a.class='INPUT' "
                "AND v.id NOT IN (SELECT artifact_version_id FROM session_inputs WHERE session_id=?) "
                "AND LOWER(a.name) LIKE ? ORDER BY a.name, v.version_no DESC LIMIT 300",
                (exclude_session_id or -1, like))]

    def search(self, q):
        q = (q or "").strip().lower()
        if not q:
            return {"projects": [], "sessions": [], "versions": []}
        like = f"%{q}%"
        with self.ro() as c:
            projects = [dict(r) for r in c.execute(
                "SELECT DISTINCT p.id, p.name, p.description FROM projects p "
                "LEFT JOIN annotations an ON an.entity_type='project' AND an.entity_id=p.id "
                "LEFT JOIN entity_tags e ON e.entity_type='project' AND e.entity_id=p.id LEFT JOIN tags t ON t.id=e.tag_id "
                "WHERE LOWER(p.name) LIKE ? OR LOWER(p.description) LIKE ? OR LOWER(an.text) LIKE ? OR LOWER(an.kv) LIKE ? "
                "OR t.name LIKE ?", (like,) * 5)]
            sessions = [dict(r) for r in c.execute(
                "SELECT DISTINCT s.id, s.title, s.status, s.notes FROM sessions s "
                "LEFT JOIN annotations an ON an.entity_type='session' AND an.entity_id=s.id "
                "LEFT JOIN entity_tags e ON e.entity_type='session' AND e.entity_id=s.id LEFT JOIN tags t ON t.id=e.tag_id "
                "LEFT JOIN session_inputs i ON i.session_id=s.id LEFT JOIN artifact_versions vi ON vi.id=i.artifact_version_id "
                "LEFT JOIN session_outputs o ON o.session_id=s.id LEFT JOIN artifact_versions vo ON vo.id=o.artifact_version_id "
                "LEFT JOIN method_refs m ON m.session_id=s.id "
                "WHERE LOWER(s.title) LIKE ? OR LOWER(s.notes) LIKE ? OR LOWER(an.text) LIKE ? OR t.name LIKE ? "
                "OR LOWER(vi.original_filename) LIKE ? OR LOWER(vo.original_filename) LIKE ? OR m.commit_sha LIKE ? "
                "OR LOWER(m.repo_name) LIKE ? ORDER BY s.id DESC", (like,) * 8)]
            versions = [dict(r) for r in c.execute(
                "SELECT DISTINCT v.id, v.version_no, v.original_filename, v.note, a.id AS artifact_id, a.class "
                "FROM artifact_versions v JOIN artifacts a ON a.id=v.artifact_id "
                "LEFT JOIN annotations an ON an.entity_type='artifact_version' AND an.entity_id=v.id "
                "LEFT JOIN entity_tags e ON e.entity_type='artifact_version' AND e.entity_id=v.id "
                "LEFT JOIN tags t ON t.id=e.tag_id "
                "WHERE LOWER(v.original_filename) LIKE ? OR LOWER(v.note) LIKE ? OR LOWER(an.text) LIKE ? "
                "OR t.name LIKE ? OR v.blob_sha256 LIKE ? ORDER BY v.id DESC", (like,) * 5)]
            return {"projects": projects, "sessions": sessions, "versions": versions}

    def archived_items(self):
        with self.ro() as c:
            return {
                "projects": [dict(r) for r in c.execute("SELECT id, name, archived_at FROM projects WHERE archived_at IS NOT NULL")],
                "sessions": [dict(r) for r in c.execute("SELECT id, title, archived_at FROM sessions WHERE archived_at IS NOT NULL")],
            }

    # ------------------------------------------------------------ zip
    def session_zip(self, actor, session_id) -> bytes:
        s = self.session(session_id)
        manifest = {
            "session": {k: s[k] for k in ("id", "title", "notes", "status", "created_at", "finalized_at",
                                          "parent_session_id")},
            "projects": [p["name"] for p in s["projects"]], "tags": s["tags"],
            "annotation": self.annotation("session", session_id),
            "method": None, "inputs": [], "outputs": [], "generated_at": now(),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for kind, items in (("inputs", s["inputs"]), ("outputs", s["outputs"])):
                for v in items:
                    path = f"{kind}/v{v['version_no']}_{v['original_filename']}"
                    z.writestr(path, self.blobs.read(v["blob_sha256"]))
                    manifest[kind].append({"path": path, "artifact": v["name"], "version": v["version_no"],
                                           "sha256": v["blob_sha256"], "size": v["size_bytes"], "note": v["note"],
                                           "uploaded_at": v["uploaded_at"], "tags": v["tags"],
                                           "annotation": self.annotation("artifact_version", v["id"])})
            if s["method"]:
                m = dict(s["method"])
                snap = m.pop("snapshot", None)
                if snap:
                    path = f"method/v{snap['version_no']}_{snap['original_filename']}"
                    z.writestr(path, self.blobs.read(snap["blob_sha256"]))
                    m["snapshot"] = {"path": path, "sha256": snap["blob_sha256"]}
                manifest["method"] = m
            z.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        with self.tx() as c:
            self._audit(c, actor, "download_zip", "session", session_id)
        return buf.getvalue()


def parse_kv(s: str) -> dict:
    """'key=value' per line → dict."""
    out = {}
    for line in (s or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out
