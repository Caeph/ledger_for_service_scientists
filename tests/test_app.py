"""HTTP-level tests for the Starlette shell around the Shiny app (login gate, file routes)."""
import io
import os
import shutil
import tempfile
import zipfile

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tracker.app import COOKIE, create_app, parse_route
from tracker.services import Tracker

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def env():
    d = tempfile.mkdtemp()
    t = Tracker(os.path.join(d, "db.sqlite3"), os.path.join(d, "blobs"))
    admin = t.bootstrap_admin("admin", "admin12345")
    p = t.create_project(admin, "P")
    s = t.create_session(admin, "S", [p])
    vid = t.upload(admin, s, "INPUT", "data.tsv", b"a\tb\n1\t2\n")
    html_vid = t.upload(admin, s, "OUTPUT", "report.html", b"<script>alert(1)</script>")
    client = TestClient(create_app(t), follow_redirects=False)
    yield client, t, s, vid, html_vid
    shutil.rmtree(d)


def login(client):
    r = client.post("/login", data={"username": "admin", "password": "admin12345"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "httponly" in r.headers["set-cookie"].lower()
    client.cookies.set(COOKIE, r.cookies[COOKIE])


def test_unauthenticated_redirects_to_login(env):
    client, _, s, vid, _ = env
    for path in ("/", f"/files/versions/{vid}/download", f"/files/versions/{vid}/raw", f"/files/sessions/{s}/zip"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/login", path


def test_wrong_password(env):
    client = env[0]
    r = client.post("/login", data={"username": "admin", "password": "nope"})
    assert r.headers["location"] == "/login?error=1"


def test_download_is_attachment_and_byte_identical(env):
    client, _, _, vid, _ = env
    login(client)
    r = client.get(f"/files/versions/{vid}/download")
    assert r.status_code == 200
    assert r.content == b"a\tb\n1\t2\n"
    assert r.headers["content-disposition"].startswith("attachment")


def test_html_preview_is_sandboxed(env):
    client, _, _, _, html_vid = env
    login(client)
    r = client.get(f"/files/versions/{html_vid}/raw")
    assert "sandbox" in r.headers["content-security-policy"]


def test_zip_and_page_after_login(env):
    client, _, s, _, _ = env
    login(client)
    assert client.get("/").status_code == 200
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/files/sessions/{s}/zip").content))
    assert "manifest.json" in z.namelist()


def test_logout_invalidates_token(env):
    client = env[0]
    login(client)
    client.post("/logout")
    assert client.get("/").status_code == 303


def test_cross_origin_websocket_rejected(env):
    client = env[0]
    login(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/websocket/", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_text()


def test_parse_route():
    assert parse_route("#/") == ("dashboard", None, {})
    assert parse_route("#/sessions/12") == ("session", 12, {})
    assert parse_route("#/projects/3?archived=1") == ("project", 3, {"archived": "1"})
    assert parse_route("#/search?q=a%20b") == ("search", None, {"q": "a b"})
    assert parse_route("#/sessions/x")[0] == "notfound"
