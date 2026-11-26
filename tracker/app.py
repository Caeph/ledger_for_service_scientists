"""ASGI app: Shiny for Python UI + a few plain Starlette routes.

Starlette handles what Shiny is not built for:
  /login, /logout            -> set/clear an HttpOnly session cookie
  /files/...                 -> authenticated downloads, inline previews and session zips
Everything else (mounted at /) is the Shiny app. Pages are addressed by URL hash (#/sessions/3).
"""
from __future__ import annotations

import asyncio
import html
import re
import urllib.parse

from shiny import App, reactive, render, ui
from shiny.types import SilentException
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from .services import ServiceError, Tracker
from .ui import admin, artifact, dashboard, project, search, session as session_page
from .ui.common import Ctx, h, head, kind_of, run, topbar

COOKIE = "tracker_session"
FILE_INPUTS = ("in_files", "out_files", "m_snapshot")


# ================================================================== Shiny
def parse_route(raw: str):
    raw = (raw or "#/").lstrip("#")
    path, _, qs = raw.partition("?")
    query = {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ("dashboard", None, query)
    names = {"projects": "project", "sessions": "session", "artifacts": "artifact", "versions": "version"}
    if len(parts) == 2 and parts[0] in names and parts[1].isdigit():
        return (names[parts[0]], int(parts[1]), query)
    if parts[0] in ("search", "admin", "new-session") and len(parts) == 1:
        return (parts[0], None, query)
    return ("notfound", None, query)


def make_server(tracker: Tracker, max_bytes: int):
    def server(input, output, session):
        token = session.http_conn.cookies.get(COOKIE)
        tick = reactive.value(0)
        consumed: set[str] = set()

        def goto_login():
            asyncio.ensure_future(session.send_custom_message("goto", {"url": "/login"}))

        def me():
            u = tracker.user_for_token(token)
            if not u:
                goto_login()
            return u

        def val(id, default=None):
            if id not in input:
                return default
            try:
                v = input[id]()
            except SilentException:
                return default
            return default if v is None else v

        def files(id):
            return [f for f in (val(id) or []) if f["datapath"] not in consumed]

        def consume(id):
            for f in val(id) or []:
                consumed.add(f["datapath"])

        @reactive.calc
        def route():
            return parse_route(val("route", "#/"))

        async def goto(hash_):
            await session.send_custom_message("goto", {"hash": hash_})

        def notify(msg, kind="message"):
            ui.notification_show(msg, type=kind, duration=8 if kind == "error" else 4)

        ctx = Ctx(tracker=tracker, input=input, session=session, me=me, bump=lambda: tick.set(tick() + 1),
                  goto=goto, notify=notify, val=val, files=files, consume=consume, route=route, max_bytes=max_bytes)

        # A file chosen on one page must never be uploaded from another page.
        @reactive.effect
        @reactive.event(input.route)
        def _forget_uploads_on_navigation():
            with reactive.isolate():
                for fid in FILE_INPUTS:
                    consume(fid)

        @render.ui
        def page_out():
            tick()
            name, id_, query = route()
            with reactive.isolate():
                u = me()
            if not u:
                return ui.div("Redirecting to login…", class_="loading")
            try:
                if name == "dashboard":
                    content = dashboard.page(ctx, query)
                elif name == "project":
                    content = project.page(ctx, id_, query)
                elif name == "session":
                    content = session_page.page(ctx, id_)
                elif name == "new-session":
                    content = session_page.new_page(ctx, query)
                elif name == "artifact":
                    content = artifact.page(ctx, id_)
                elif name == "version":
                    content = artifact.preview_page(ctx, id_)
                elif name == "search":
                    content = search.page(ctx, query)
                elif name == "admin":
                    content = admin.page(ctx) if u["is_admin"] else ui.HTML("<h1>Admins only</h1>")
                else:
                    content = ui.HTML('<h1>Not found</h1><p><a href="#/">Back to the dashboard</a></p>')
            except ServiceError as e:
                content = ui.HTML(f'<h1>{h(str(e))}</h1><p><a href="#/">Back to the dashboard</a></p>')
            return ui.TagList(topbar(u), ui.div(content, class_="main"))

        # ---- main annotation form on project and session pages
        @reactive.effect
        @reactive.event(input.ann_save)
        def _save_annotation():
            name, id_, _ = route()
            u = me()
            if u and name in ("project", "session"):
                run(ctx, lambda: tracker.annotate(u["id"], name, id_, text=val("ann_text", ""), kv=val("ann_kv", ""),
                                                  tags=val("ann_tags", "")), "Annotation saved.")

        # ---- generic per-row actions (buttons with data-act=...)
        @reactive.effect
        @reactive.event(input.act)
        def _act():
            a = input.act()
            u = me()
            if not (u and a):
                return
            act, id_, fields, data = a.get("act"), a.get("id"), a.get("fields") or {}, a.get("data") or {}
            name, rid, _ = route()
            if act in ("unlink_input", "unlink_output") and name == "session":
                fn = tracker.remove_input_link if act == "unlink_input" else tracker.remove_output_link
                run(ctx, lambda: fn(u["id"], rid, id_), "Unlinked from this session. The file version is still stored.")
            elif act == "archive":
                run(ctx, lambda: tracker.archive(u["id"], data.get("kind"), id_, str(data.get("archived")) == "1"),
                    "Updated.")
            elif act == "user_active":
                run(ctx, lambda: tracker.set_user_active(u["id"], id_, str(data.get("active")) == "1"), "Updated.")
            elif act == "user_password":
                run(ctx, lambda: tracker.reset_password(u["id"], id_, fields.get("password", "")), "Password reset.")
            elif act == "annotate_version":
                run(ctx, lambda: tracker.annotate(u["id"], "artifact_version", id_, text=fields.get("text", ""),
                                                  kv=fields.get("kv", ""), tags=fields.get("tags", "")),
                    "Annotation saved.")

        for mod in (dashboard, project, session_page, search, admin):
            mod.register(ctx)

    return server


app_ui = ui.page_fluid(head(), ui.output_ui("page_out", fill=False), title="Analysis Tracker")


# ================================================================== Starlette routes
LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Log in · Analysis Tracker</title>
<style>:root{{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--accent:#2f5fd0;--warn:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161618;--card:#1f1f22;--ink:#ececef;--muted:#a0a0a8;--line:#323236;
--accent:#7aa2ff;--warn:#ff8a80}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif}}
.card{{max-width:340px;margin:80px auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:22px}}
h1{{font-size:22px;margin:0 0 12px}}label{{display:block;font-size:13px;color:var(--muted);margin:10px 0 3px}}
input{{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--line);border-radius:7px;background:var(--bg);
color:var(--ink);font:inherit}}button{{margin-top:16px;background:var(--accent);color:#fff;border:0;border-radius:7px;
padding:8px 16px;font:inherit;font-weight:600;cursor:pointer}}.err{{color:var(--warn);font-size:14px}}</style></head>
<body><form class="card" method="post" action="/login"><h1>Analysis Tracker</h1>{error}
<label for="u">Username</label><input id="u" name="username" autofocus autocomplete="username">
<label for="p">Password</label><input id="p" type="password" name="password" autocomplete="current-password">
<button>Log in</button></form></body></html>"""


def create_app(tracker: Tracker, max_mb: int = 500):
    shiny_app = App(app_ui, make_server(tracker, max_mb * 1024 * 1024))

    def current_user(request: Request):
        return tracker.user_for_token(request.cookies.get(COOKIE))

    async def login_get(request: Request):
        err = '<p class="err">Wrong username or password.</p>' if request.query_params.get("error") else ""
        return HTMLResponse(LOGIN_HTML.format(error=err))

    async def login_post(request: Request):
        form = await request.form()
        token = tracker.login(str(form.get("username", "")), str(form.get("password", "")))
        if not token:
            return RedirectResponse("/login?error=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/")
        return resp

    async def logout(request: Request):
        tracker.logout(request.cookies.get(COOKIE))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    def _auth_or_redirect(request):
        u = current_user(request)
        return u, (None if u else RedirectResponse("/login", status_code=303))

    async def version_download(request: Request):
        u, deny = _auth_or_redirect(request)
        if deny:
            return deny
        try:
            v, data = tracker.read_version(int(request.path_params["vid"]))
        except ServiceError:
            return Response("Not found", 404)
        fn = v["original_filename"]
        ascii_fn = re.sub(r"[^A-Za-z0-9._-]", "_", fn)
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_fn}\"; filename*=UTF-8''{urllib.parse.quote(fn)}",
            "X-Content-Type-Options": "nosniff"})

    async def version_raw(request: Request):
        """Inline bytes for previews. A CSP sandbox stops uploaded HTML/SVG from running with this origin."""
        u, deny = _auth_or_redirect(request)
        if deny:
            return deny
        try:
            v, data = tracker.read_version(int(request.path_params["vid"]))
        except ServiceError:
            return Response("Not found", 404)
        kind = kind_of(v["mime_type"], v["original_filename"])
        if kind not in ("image", "pdf", "html"):
            return Response("Preview not available", 415)
        headers = {"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if kind == "html":
            headers["Content-Security-Policy"] = "sandbox allow-scripts"
        elif kind == "image":
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        # PDFs: browsers refuse to render them inside a CSP sandbox
        ctype = "text/html; charset=utf-8" if kind == "html" else v["mime_type"]
        return Response(data, media_type=ctype, headers=headers)

    async def session_zip(request: Request):
        u, deny = _auth_or_redirect(request)
        if deny:
            return deny
        sid = int(request.path_params["sid"])
        try:
            data = tracker.session_zip(u["id"], sid)
        except ServiceError:
            return Response("Not found", 404)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="session_{sid}.zip"'})

    async def gated_shiny(scope, receive, send):
        """Page loads need a valid login; the websocket must come from our own origin."""
        if scope["type"] == "http" and scope["path"] in ("", "/"):
            req = Request(scope)
            if not current_user(req):
                await RedirectResponse("/login", status_code=303)(scope, receive, send)
                return
        if scope["type"] == "websocket":
            hdrs = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            origin, host = hdrs.get("origin", ""), hdrs.get("host", "")
            if origin and urllib.parse.urlparse(origin).netloc != host:
                await send({"type": "websocket.close", "code": 1008})
                return
        await shiny_app(scope, receive, send)

    return Starlette(routes=[
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/files/versions/{vid:int}/download", version_download),
        Route("/files/versions/{vid:int}/raw", version_raw),
        Route("/files/sessions/{sid:int}/zip", session_zip),
        Mount("/", app=gated_shiny),
    ])
