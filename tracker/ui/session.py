"""Session page (INPUT · METHOD · OUTPUT) and the new-session form."""
from htmltools import HTML, TagList, tags
from shiny import reactive, render, ui

from ..services import clean_filename
from .common import (Ctx, act_html, annotation_inputs, annotation_view, archived_badge, area, badge, btn, collapsible,
                     fmt_size, fmt_time, h, method_label, run, tag_chips, txt)


# ------------------------------------------------------------------ new session
def new_page(ctx: Ctx, query):
    projects = ctx.tracker.list_projects()
    if not projects:
        return HTML('<h1>New analytical session</h1><p>Create a project on the <a href="#/">dashboard</a> first.</p>')
    pre = [query["project"]] if query.get("project") else []
    return TagList(
        HTML("<h1>New analytical session</h1>"),
        tags.div(
            txt("ns_title", "Title"),
            ui.input_checkbox_group("ns_projects", "Projects (pick one or more)",
                                    {str(p["id"]): p["name"] for p in projects}, selected=pre, width="100%"),
            area("ns_notes", "Notes", rows=4), txt("ns_tags", "Tags (comma separated)"),
            btn("ns_create", "Create session"), class_="box", style="max-width:640px"),
    )


# ------------------------------------------------------------------ session page
def _version_item(v, is_open, kind):
    newer = (f' <span class="badge-st newer" title="A newer version exists">newer: v{v["latest_no"]}</span>'
             if v.get("latest_no") and v["latest_no"] > v["version_no"] else "")
    unlink = (" · " + act_html("unlink", f"unlink_{kind}", v["id"], cls="btn-link-act",
                               title="Remove from this draft session (the file stays stored)")) if is_open else ""
    return f"""<div class="item"><a href="#/artifacts/{v['artifact_id']}"><b>{h(v['name'])}</b></a> v{v['version_no']}{newer}
    <div class="small muted">{h(v['uploader'] or '')} · {fmt_time(v['uploaded_at'])} · {fmt_size(v['size_bytes'])}</div>
    {f'<div class="small">{h(v["note"])}</div>' if v['note'] else ''}<div>{tag_chips(v['tags'])}</div>
    <div class="small"><a href="#/versions/{v['id']}">preview</a> · <a href="/files/versions/{v['id']}/download">download</a>
    {unlink}</div></div>"""


def _upload_block(prefix, label, open_):
    return collapsible(f"Upload {label} file(s)",
                       ui.input_file(f"{prefix}_files", None, multiple=True, width="100%"),
                       ui.output_ui(f"{prefix}_clash"),
                       txt(f"{prefix}_note", "Note"), txt(f"{prefix}_tags", "Tags"),
                       btn(f"{prefix}_upload", "Upload"), open=open_)


def _method_html(m):
    if not m:
        return '<p class="muted small">No method recorded yet.</p>'
    sn = m.get("snapshot")
    snap = ""
    if sn:
        snap = (f'<div class="small">Script: <a href="#/artifacts/{sn["artifact_id"]}">{h(sn["name"])}</a> '
                f'v{sn["version_no"]} · <a href="#/versions/{sn["id"]}">preview</a> · '
                f'<a href="/files/versions/{sn["id"]}/download">download</a></div>')
    paths = "".join(f'<div class="mono">{h(p)}</div>' for p in m["paths"])
    note = f'<div class="small">{h(m["note"])}</div>' if m["note"] else ""
    if not m["commit_sha"]:  # script-only method
        title = h(m["repo_name"] or m["repo_path"] or (sn["name"] if sn else "script"))
        return f"""<div class="item"><b>{title}</b> <span class="badge-st open">script only</span>
        <div class="small muted">No git commit recorded; the uploaded script is the method.</div>
        {snap}{paths}{note}</div>"""
    vb = ('<span class="badge-st verified">verified</span>' if m["verified"]
          else '<span class="badge-st unverified">not verified</span>')
    return f"""<div class="item"><b>{h(m['repo_name'] or m['repo_path'])}</b> {vb}
    {f'<div class="small muted mono">{h(m["repo_path"])}</div>' if m['repo_path'] and m['repo_name'] else ''}
    <div class="mono" style="word-break:break-all">{h(m['commit_sha'])}</div>
    {f'<div class="small">branch <b>{h(m["branch"])}</b></div>' if m['branch'] else ''}
    {f'<div class="small muted">“{h(m["commit_message"])}”</div>' if m['commit_message'] else ''}
    {paths}{note}{snap}</div>"""


def page(ctx: Ctx, sid):
    t = ctx.tracker
    user = ctx.me()
    s = t.session(sid)
    is_open = s["status"] == "open"
    ann = t.annotation("session", sid)

    ins = "".join(_version_item(v, is_open, "input") for v in s["inputs"]) or '<p class="muted small">No inputs yet.</p>'
    outs = "".join(_version_item(v, is_open, "output") for v in s["outputs"]) or '<p class="muted small">No outputs yet.</p>'
    m = s["method"]

    in_actions, me_actions, out_actions = [], [], []
    if is_open:
        in_actions.append(_upload_block("in", "input", not s["inputs"]))
        sel = t.selectable_inputs(sid)
        if sel:
            in_actions.append(collapsible(
                "Select existing input",
                ui.input_selectize("sel_input", None, {str(v["id"]): f'{v["name"]} v{v["version_no"]} — '
                                                       f'{v["projects"] or "no project"} · {v["uploaded_at"][:10]}'
                                                       for v in sel}, multiple=True, width="100%"),
                btn("sel_add", "Add to session")))
        mv = m or {}
        has_snap = bool(mv.get("snapshot"))
        me_actions.append(collapsible(
            ("Change" if m else "Record") + " method",
            HTML('<p class="small muted">Upload the script or notebook you ran, give a git commit, or both.</p>'),
            ui.input_file("m_snapshot", "Script / notebook file" + (" (upload to replace; same name → new version)"
                                                                    if has_snap else ""), width="100%"),
            tags.div(HTML('<div class="small" style="font-weight:600;margin:4px 0">Git commit '
                          '<span class="muted" style="font-weight:400">(optional if you upload the script)</span></div>'),
                     txt("m_repo_name", "Repository name", mv.get("repo_name", "")),
                     txt("m_repo_path", "Repository path under /repos (optional)", mv.get("repo_path", "")),
                     txt("m_sha", "Commit SHA (40 hex)", mv.get("commit_sha", "")),
                     txt("m_branch", "Branch", mv.get("branch", "")),
                     style="border-left:3px solid var(--line);padding-left:10px;margin-bottom:8px"),
            area("m_paths", "Script / notebook paths within the repo (one per line, optional)",
                 "\n".join(mv.get("paths", []))),
            txt("m_note", "Note (e.g. R/Python version, parameters)", mv.get("note", "")),
            btn("m_save", "Save method"), open=bool(not m and s["inputs"])))
        out_actions.append(_upload_block("out", "output", False))
        out_actions.append(HTML('<p class="small muted">Outputs stay staged until you finalize.</p>'))

    if is_open:
        problems = t.finalize_problems(sid)
        fin = (HTML(f'<div class="warnbox small">To finalize, the session still needs: <b>{h("; ".join(problems))}</b>.</div>')
               if problems else btn("fin_open", "Finalize session…"))
    else:
        fin = HTML(f'<span class="small muted">Finalized {fmt_time(s["finalized_at"])} by {h(s.get("finalizer", ""))}. '
                   f'Read-only — notes, annotations and tags remain editable.</span>')

    lineage = ""
    if s.get("parent"):
        lineage += f'<div class="small">Iteration of <a href="#/sessions/{s["parent"]["id"]}">{h(s["parent"]["title"])}</a></div>'
    if s["children"]:
        lineage += '<div class="small">Iterations: ' + ", ".join(
            f'<a href="#/sessions/{c["id"]}">{h(c["title"])}</a> {badge(c["status"])}' for c in s["children"]) + "</div>"
    admin = ""
    if user["is_admin"]:
        arch = bool(s["archived_at"])
        admin = act_html("Unarchive" if arch else "Archive", "archive", sid, kind="session", archived="0" if arch else "1")
    projects = ", ".join(f'<a href="#/projects/{p["id"]}">{h(p["name"])}</a>' for p in s["projects"])

    return TagList(
        HTML(f'<p class="small">{projects}</p>'),
        tags.div(
            HTML(f"<h1>{h(s['title'])}</h1>{badge(s['status'])}{archived_badge(s)}"),
            tags.div(btn("s_start", "Start new session from this one", "btn-outline-primary"),
                     HTML(f'<a class="btn btn-sm btn-outline-secondary" href="/files/sessions/{sid}/zip">Download zip</a>'
                          f'{admin}'), class_="right row-flex"),
            class_="row-flex"),
        HTML(f"""<div class="small muted">Created {fmt_time(s['created_at'])} by {h(s['author'])}</div>{lineage}
        <div>{tag_chips(s['tags'])}</div>"""),
        tags.div(
            HTML(f'<p style="white-space:pre-wrap">{h(s["notes"])}</p>' if s["notes"]
                 else '<p class="muted small">No notes.</p>'),
            HTML(annotation_view(ann)),
            collapsible("Edit notes", area("s_notes", "Notes", s["notes"], rows=5), btn("s_notes_save", "Save notes")),
            annotation_inputs(ann, s["tags"]),
            class_="box", style="margin:12px 0"),
        tags.div(fin, style="margin-bottom:12px"),
        tags.div(
            tags.div(HTML(f"<h3>INPUT</h3>{ins}"), *in_actions, class_="box col-INPUT"),
            tags.div(HTML(f"<h3>METHOD</h3>{_method_html(m)}"), *me_actions, class_="box col-METHOD"),
            tags.div(HTML(f"<h3>OUTPUT</h3>{outs}"), *out_actions, class_="box col-OUTPUT"),
            class_="cols"),
    )


# ------------------------------------------------------------------ server side
def register(ctx: Ctx):
    t = ctx.tracker
    inp = ctx.input

    def current_session():
        name, sid, _ = ctx.route()
        return sid if name == "session" else None

    # --- name-clash prompt: "new version of X" (default) or "new file"
    def clash_ui(prefix, cls):
        sid = current_session()
        files = ctx.files(f"{prefix}_files")
        if not sid or not files:
            return None
        existing = set(t.existing_names(sid, cls))
        hits = [f["name"] for f in files if clean_filename(f["name"]) in existing]
        if not hits:
            return None
        return tags.div(
            HTML(f"<b>{h(', '.join(hits))}</b> already exist{'s' if len(hits) == 1 else ''} in this session "
                 f"or its parent. Save as:"),
            ui.input_radio_buttons(f"{prefix}_mode", None, {"version": "new version of the existing file (default)",
                                                            "new": "a separate new file"}, selected="version"),
            class_="warnbox small", style="margin:4px 0 8px")

    @render.ui
    def in_clash():
        return clash_ui("in", "INPUT")

    @render.ui
    def out_clash():
        return clash_ui("out", "OUTPUT")

    def do_upload(prefix, cls):
        sid, u = current_session(), ctx.me()
        if not (sid and u):
            return
        files = ctx.files(f"{prefix}_files")
        if not files:
            ctx.notify("Choose a file first.", "error")
            return
        mode = ctx.val(f"{prefix}_mode", "version") or "version"

        def go():
            for f in files:
                with open(f["datapath"], "rb") as fh:
                    t.upload(u["id"], sid, cls, f["name"], fh.read(), ctx.val(f"{prefix}_note", ""),
                             ctx.val(f"{prefix}_tags", ""), mode, max_bytes=ctx.max_bytes)
        if run(ctx, go, f"Uploaded {len(files)} {cls.lower()} file(s)."):
            ctx.consume(f"{prefix}_files")

    @reactive.effect
    @reactive.event(inp.in_upload)
    def _up_in():
        do_upload("in", "INPUT")

    @reactive.effect
    @reactive.event(inp.out_upload)
    def _up_out():
        do_upload("out", "OUTPUT")

    @reactive.effect
    @reactive.event(inp.sel_add)
    def _select():
        sid, u = current_session(), ctx.me()
        chosen = ctx.val("sel_input", ()) or ()
        if sid and u and chosen:
            run(ctx, lambda: [t.select_input(u["id"], sid, int(v)) for v in chosen], f"Added {len(chosen)} input(s).")

    @reactive.effect
    @reactive.event(inp.m_save)
    def _method():
        sid, u = current_session(), ctx.me()
        if not (sid and u):
            return
        snap = ctx.files("m_snapshot")
        snapshot = None
        if snap:
            with open(snap[0]["datapath"], "rb") as fh:
                snapshot = (snap[0]["name"], fh.read())
        if run(ctx, lambda: t.set_method(u["id"], sid, ctx.val("m_repo_name", ""), ctx.val("m_repo_path", ""),
                                         ctx.val("m_sha", ""), ctx.val("m_branch", ""), ctx.val("m_paths", ""),
                                         ctx.val("m_note", ""), snapshot=snapshot), "Method recorded."):
            ctx.consume("m_snapshot")

    @reactive.effect
    @reactive.event(inp.s_notes_save)
    def _notes():
        sid, u = current_session(), ctx.me()
        if sid and u:
            run(ctx, lambda: t.update_session_notes(u["id"], sid, ctx.val("s_notes", "")), "Notes saved.")

    @reactive.effect
    @reactive.event(inp.fin_open)
    def _fin_dialog():
        sid = current_session()
        if not sid:
            return
        problems = t.finalize_problems(sid)
        if problems:
            ctx.notify("Cannot finalize — missing: " + "; ".join(problems), "error")
            return
        s = t.session(sid)
        li = lambda vs: "".join(f"<li>{h(v['name'])} v{v['version_no']}</li>" for v in vs)
        ui.modal_show(ui.modal(
            HTML(f"""<p>This marks the session as <b>final and read-only</b>. Its inputs, method and outputs can no
            longer be changed. Notes, annotations and tags stay editable. To change anything later, start a new
            session from this one.</p><h3>Inputs</h3><ul>{li(s['inputs'])}</ul>
            <h3>Method</h3><p>{method_label(s['method'])}</p><h3>Outputs</h3><ul>{li(s['outputs'])}</ul>"""),
            title=f"Finalize “{s['title']}”?",
            footer=TagList(ui.modal_button("Cancel"), btn("fin_confirm", "Yes, finalize")),
            easy_close=True))

    @reactive.effect
    @reactive.event(inp.fin_confirm)
    def _finalize():
        sid, u = current_session(), ctx.me()
        ui.modal_remove()
        if sid and u:
            run(ctx, lambda: t.finalize(u["id"], sid), "Session finalized.")

    @reactive.effect
    @reactive.event(inp.s_start)
    async def _start_from():
        sid, u = current_session(), ctx.me()
        if not (sid and u):
            return
        new = run(ctx, lambda: t.start_from(u["id"], sid), "New session pre-filled with the same inputs and method.")
        if new:
            await ctx.goto(f"#/sessions/{new}")

    @reactive.effect
    @reactive.event(inp.ns_create)
    async def _create():
        u = ctx.me()
        if not u:
            return
        sid = run(ctx, lambda: t.create_session(u["id"], ctx.val("ns_title", ""), ctx.val("ns_projects", ()) or (),
                                                ctx.val("ns_notes", ""), ctx.val("ns_tags", "")),
                  "Session created. Add inputs, record the method, then finalize.")
        if sid:
            await ctx.goto(f"#/sessions/{sid}")
