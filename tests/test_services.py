"""Service-layer tests for the spec's invariants and acceptance criteria. Run: python3 -m unittest -v"""
import hashlib
import inspect
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
import zipfile

from tracker import services
from tracker.services import ServiceError, Tracker

SHA = "0123456789abcdef0123456789abcdef01234567"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = Tracker(os.path.join(self.dir, "db.sqlite3"), os.path.join(self.dir, "blobs"))
        self.admin = self.t.bootstrap_admin("admin", "admin12345")
        self.user = self.t.create_user(self.admin, "ana", "Ana", "password1")
        self.p = self.t.create_project(self.user, "P1")
        self.s = self.t.create_session(self.user, "S1", [self.p], notes="first look at tumour growth")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def count(self, sql, *a):
        c = sqlite3.connect(self.t.db_path)
        try:
            return c.execute(sql, a).fetchone()[0]
        finally:
            c.close()

    def ready(self, sid=None):
        sid = sid or self.s
        self.t.upload(self.user, sid, "INPUT", "in.tsv", b"a\tb\n1\t2\n")
        self.t.set_method(self.user, sid, "repo", "", SHA)
        self.t.upload(self.user, sid, "OUTPUT", "fig.png", b"\x89PNG fake")


class TestVersioning(Base):
    def test_same_name_twice_creates_v1_v2_byte_identical(self):
        v1 = self.t.upload(self.user, self.s, "INPUT", "data.xlsx", b"one")
        v2 = self.t.upload(self.user, self.s, "INPUT", "data.xlsx", b"two")
        a, b = self.t.version(v1), self.t.version(v2)
        self.assertEqual(a["artifact_id"], b["artifact_id"])
        self.assertEqual((a["version_no"], b["version_no"]), (1, 2))
        self.assertEqual(self.t.read_version(v1)[1], b"one")
        self.assertEqual(self.t.read_version(v2)[1], b"two")

    def test_new_file_mode_creates_separate_artifact(self):
        v1 = self.t.upload(self.user, self.s, "INPUT", "data.xlsx", b"one")
        v2 = self.t.upload(self.user, self.s, "INPUT", "data.xlsx", b"two", mode="new")
        self.assertNotEqual(self.t.version(v1)["artifact_id"], self.t.version(v2)["artifact_id"])

    def test_identical_content_one_blob_two_versions(self):
        self.t.upload(self.user, self.s, "INPUT", "a.tsv", b"same")
        self.t.upload(self.user, self.s, "INPUT", "b.tsv", b"same")
        self.assertEqual(self.count("SELECT COUNT(*) FROM blobs"), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM artifact_versions"), 2)
        sha = hashlib.sha256(b"same").hexdigest()
        self.assertTrue(os.path.exists(os.path.join(self.dir, "blobs", sha[:2], sha)))

    def test_versions_immutable_in_db(self):
        vid = self.t.upload(self.user, self.s, "INPUT", "a.tsv", b"x")
        c = sqlite3.connect(self.t.db_path)
        with self.assertRaises(sqlite3.DatabaseError):
            c.execute("UPDATE artifact_versions SET note='hack' WHERE id=?", (vid,))
        with self.assertRaises(sqlite3.DatabaseError):
            c.execute("DELETE FROM blobs")
        c.close()

    def test_filename_sanitized(self):
        vid = self.t.upload(self.user, self.s, "INPUT", "../../etc/passwd", b"x")
        self.assertEqual(self.t.version(vid)["original_filename"], "passwd")

    def test_upload_limit(self):
        with self.assertRaises(ServiceError):
            self.t.upload(self.user, self.s, "INPUT", "big.bin", b"x" * 2048, max_bytes=1024)


class TestNoDeletion(Base):
    def test_no_delete_functions(self):
        names = [n for n, _ in inspect.getmembers(Tracker, inspect.isfunction)]
        self.assertFalse([n for n in names if "delete" in n or "purge" in n or n.startswith("remove_project")])
        src = inspect.getsource(services)
        for table in ("projects", "sessions", "artifacts", "artifact_versions", "blobs"):
            self.assertNotIn(f"DELETE FROM {table} ", src)
            self.assertNotIn(f"DELETE FROM {table}\"", src)

    def test_unlink_on_open_session_keeps_version_and_audits(self):
        vid = self.t.upload(self.user, self.s, "INPUT", "a.tsv", b"keep me")
        before = self.count("SELECT COUNT(*) FROM audit_log WHERE action='unlink_input'")
        self.t.remove_input_link(self.user, self.s, vid)
        self.assertEqual(self.t.session(self.s)["inputs"], [])
        self.assertEqual(self.t.read_version(vid)[1], b"keep me")
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_log WHERE action='unlink_input'"), before + 1)

    def test_archive_admin_only_and_hidden(self):
        with self.assertRaises(ServiceError):
            self.t.archive(self.user, "project", self.p)
        self.t.archive(self.admin, "project", self.p)
        self.assertEqual(self.t.list_projects(), [])
        self.assertEqual(len(self.t.list_projects(include_archived=True)), 1)
        self.t.archive(self.admin, "project", self.p, archived=False)
        self.assertEqual(len(self.t.list_projects()), 1)


class TestFinalize(Base):
    def test_refused_with_named_missing_parts(self):
        with self.assertRaises(ServiceError) as e:
            self.t.finalize(self.user, self.s)
        msg = str(e.exception)
        self.assertIn("INPUT", msg)
        self.assertIn("METHOD", msg)
        self.assertIn("OUTPUT", msg)
        self.t.upload(self.user, self.s, "INPUT", "in.tsv", b"1")
        with self.assertRaises(ServiceError) as e:
            self.t.finalize(self.user, self.s)
        self.assertNotIn("INPUT", str(e.exception))

    def test_read_only_after_finalize(self):
        self.ready()
        self.t.finalize(self.user, self.s)
        other = self.t.create_session(self.user, "S2", [self.p])
        ov = self.t.upload(self.user, other, "INPUT", "x.tsv", b"x")
        for fn in (lambda: self.t.upload(self.user, self.s, "INPUT", "in.tsv", b"new"),
                   lambda: self.t.upload(self.user, self.s, "OUTPUT", "o.png", b"new"),
                   lambda: self.t.select_input(self.user, self.s, ov),
                   lambda: self.t.set_method(self.user, self.s, "repo", "", SHA),
                   lambda: self.t.remove_input_link(self.user, self.s, ov),
                   lambda: self.t.finalize(self.user, self.s)):
            with self.assertRaises(ServiceError):
                fn()
        self.t.annotate(self.user, "session", self.s, text="still editable", tags="ok")
        self.assertEqual(self.t.annotation("session", self.s)["text"], "still editable")
        s = self.t.session(self.s)
        self.assertEqual(s["status"], "finalized")
        self.assertEqual(s["finalized_by"], self.user)

    def test_method_sha_format(self):
        with self.assertRaises(ServiceError):
            self.t.set_method(self.user, self.s, "repo", "", "abc123")
        self.t.set_method(self.user, self.s, "repo", "", SHA.upper())
        self.assertEqual(self.t.session(self.s)["method"]["verified"], 0)  # unmounted → not verified


class TestScriptOnlyMethod(Base):
    def test_script_only_method_allows_finalize(self):
        self.t.upload(self.user, self.s, "INPUT", "in.tsv", b"1")
        self.t.set_method(self.user, self.s, "", "", "", snapshot=("analysis.R", b"library(survival)\n"))
        m = self.t.session(self.s)["method"]
        self.assertEqual(m["commit_sha"], "")
        self.assertEqual(m["snapshot"]["name"], "analysis.R")
        self.t.upload(self.user, self.s, "OUTPUT", "fig.png", b"png")
        self.t.finalize(self.user, self.s)

    def test_method_needs_sha_or_script(self):
        with self.assertRaises(ServiceError) as e:
            self.t.set_method(self.user, self.s, "repo", "", "")
        self.assertIn("script", str(e.exception))
        with self.assertRaises(ServiceError):
            self.t.set_method(self.user, self.s, "", "", SHA)  # SHA without a repo
        with self.assertRaises(ServiceError):
            self.t.set_method(self.user, self.s, "", "", "", snapshot=("empty.py", b""))
        self.assertIsNone(self.t.session(self.s)["method"])
        self.assertEqual(self.count("SELECT COUNT(*) FROM artifact_versions"), 0)  # nothing stored on refusal

    def test_edit_script_only_method_keeps_script_and_versions_it(self):
        self.t.set_method(self.user, self.s, "", "", "", snapshot=("run.py", b"v1"))
        self.t.set_method(self.user, self.s, "", "", "", note="python 3.12")  # no new file: keeps the script
        self.assertEqual(self.t.session(self.s)["method"]["snapshot"]["version_no"], 1)
        self.t.set_method(self.user, self.s, "", "", "", snapshot=("run.py", b"v2"))
        snap = self.t.session(self.s)["method"]["snapshot"]
        self.assertEqual(snap["version_no"], 2)
        self.assertEqual(self.t.read_version(snap["id"])[1], b"v2")

    def test_script_only_lineage_and_zip(self):
        self.t.upload(self.user, self.s, "INPUT", "in.tsv", b"1")
        self.t.set_method(self.user, self.s, "", "", "", snapshot=("run.py", b"print(1)"))
        self.t.upload(self.user, self.s, "OUTPUT", "fig.png", b"png")
        self.t.finalize(self.user, self.s)
        out = self.t.session(self.s)["outputs"][0]
        prod = self.t.artifact(out["artifact_id"])["versions"][0]["produced_by"][0]
        self.assertEqual(prod["method"]["snapshot_name"], "run.py")
        z = zipfile.ZipFile(io.BytesIO(self.t.session_zip(self.user, self.s)))
        m = json.loads(z.read("manifest.json"))["method"]
        self.assertEqual(hashlib.sha256(z.read(m["snapshot"]["path"])).hexdigest(), m["snapshot"]["sha256"])


class TestReuseAndLineage(Base):
    def test_start_from_prefills(self):
        self.ready()
        self.t.finalize(self.user, self.s)
        new = self.t.start_from(self.user, self.s)
        n, o = self.t.session(new), self.t.session(self.s)
        self.assertEqual(n["parent_session_id"], self.s)
        self.assertEqual(n["status"], "open")
        self.assertEqual([v["id"] for v in n["inputs"]], [v["id"] for v in o["inputs"]])
        self.assertEqual(n["method"]["commit_sha"], SHA)
        self.assertEqual(n["outputs"], [])

    def test_iteration_output_same_name_becomes_v2(self):
        self.ready()
        self.t.finalize(self.user, self.s)
        old = self.t.session(self.s)["outputs"][0]
        new = self.t.start_from(self.user, self.s)
        vid = self.t.upload(self.user, new, "OUTPUT", "fig.png", b"\x89PNG better")
        v = self.t.version(vid)
        self.assertEqual((v["artifact_id"], v["version_no"]), (old["artifact_id"], 2))
        self.assertEqual(self.t.session(self.s)["outputs"][0]["version_no"], 1)  # parent untouched
        self.assertIn("fig.png", self.t.existing_names(new, "OUTPUT"))

    def test_lineage_both_directions(self):
        self.ready()
        self.t.finalize(self.user, self.s)
        s2 = self.t.create_session(self.user, "S2", [self.p])
        in_vid = self.t.session(self.s)["inputs"][0]["id"]
        self.t.select_input(self.user, s2, in_vid)
        out = self.t.session(self.s)["outputs"][0]
        a = self.t.artifact(out["artifact_id"])
        prod = a["versions"][0]["produced_by"][0]
        self.assertEqual(prod["id"], self.s)
        self.assertEqual(prod["method"]["commit_sha"], SHA)
        self.assertEqual(prod["inputs"][0]["id"], in_vid)
        ia = self.t.artifact(self.t.version(in_vid)["artifact_id"])
        self.assertEqual({s["id"] for s in ia["versions"][0]["used_in"]}, {self.s, s2})


class TestZipSearchAudit(Base):
    def test_zip_manifest_hashes_match(self):
        self.ready()
        self.t.set_method(self.user, self.s, "repo", "", SHA, snapshot=("run.py", b"print(1)"))
        z = zipfile.ZipFile(io.BytesIO(self.t.session_zip(self.user, self.s)))
        m = json.loads(z.read("manifest.json"))
        entries = m["inputs"] + m["outputs"] + [m["method"]["snapshot"]]
        self.assertEqual(len(entries), 3)
        for e in entries:
            self.assertEqual(hashlib.sha256(z.read(e["path"])).hexdigest(), e["sha256"])

    def test_search(self):
        self.t.upload(self.user, self.s, "INPUT", "clinical_table.xlsx", b"x")
        self.t.annotate(self.user, "session", self.s, tags="melanoma")
        ids = lambda q: [s["id"] for s in self.t.search(q)["sessions"]]
        self.assertIn(self.s, ids("tumour"))          # word in notes
        self.assertIn(self.s, ids("melanoma"))        # tag
        self.assertIn(self.s, ids("clinical_table"))  # input filename

    def test_every_write_audited(self):
        before = self.count("SELECT COUNT(*) FROM audit_log")
        self.ready()
        self.t.finalize(self.user, self.s)
        self.t.annotate(self.user, "session", self.s, text="x")
        after = self.count("SELECT COUNT(*) FROM audit_log")
        self.assertEqual(after - before, 5)  # input, method, output, finalize, annotate
        self.assertEqual(self.count("SELECT COUNT(*) FROM audit_log WHERE user_id IS NULL OR at IS NULL"), 0)


class TestAuth(Base):
    def test_login_and_deactivate(self):
        self.assertIsNone(self.t.login("ana", "wrong"))
        tok = self.t.login("ana", "password1")
        self.assertEqual(self.t.user_for_token(tok)["username"], "ana")
        self.t.set_user_active(self.admin, self.user, False)
        self.assertIsNone(self.t.user_for_token(tok))
        self.assertIsNone(self.t.login("ana", "password1"))

    def test_argon2_and_legacy_upgrade(self):
        self.assertTrue(self.count("SELECT password_hash FROM users WHERE id=?", self.user).startswith("$argon2"))
        legacy = ("scrypt$" + __import__("base64").b64encode(b"0" * 16).decode() + "$" +
                  __import__("base64").b64encode(hashlib.scrypt(b"oldpass12", salt=b"0" * 16, n=2**14, r=8, p=1)).decode())
        c = sqlite3.connect(self.t.db_path)
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (legacy, self.user))
        c.commit()
        c.close()
        self.assertIsNotNone(self.t.login("ana", "oldpass12"))
        self.assertTrue(self.count("SELECT password_hash FROM users WHERE id=?", self.user).startswith("$argon2"))
        self.assertIsNotNone(self.t.login("ana", "oldpass12"))

    def test_non_admin_cannot_admin(self):
        with self.assertRaises(ServiceError):
            self.t.create_user(self.user, "bob", "Bob", "password1")


class TestPerformance(Base):
    def test_dashboard_50_projects_1000_sessions(self):
        c = sqlite3.connect(self.t.db_path)
        now = services.now()
        for i in range(50):
            c.execute("INSERT INTO projects(name, created_by, created_at) VALUES (?,?,?)", (f"perf{i}", self.user, now))
        pids = [r[0] for r in c.execute("SELECT id FROM projects")]
        for i in range(1000):
            sid = c.execute("INSERT INTO sessions(title, created_by, created_at) VALUES (?,?,?)",
                            (f"s{i}", self.user, now)).lastrowid
            c.execute("INSERT INTO session_projects VALUES (?,?)", (sid, pids[i % len(pids)]))
        c.commit()
        c.close()
        t0 = time.time()
        rows, _ = self.t.dashboard()
        self.assertEqual(len(rows), 51)
        self.assertLess(time.time() - t0, 2.0)


if __name__ == "__main__":
    unittest.main()
