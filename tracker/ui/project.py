"""Project page: description, annotation and tags; sessions newest first."""
from htmltools import HTML, TagList, tags
from shiny import reactive

from .common import (Ctx, act_html, annotation_inputs, annotation_view, archived_badge, area, badge, btn, collapsible,
                     fmt_time, h, run, tag_chips)


def page(ctx: Ctx, pid, query):
    show_arch = query.get("archived") == "1"
    p, sessions = ctx.tracker.project(pid, include_archived=show_arch)
    ann = ctx.tracker.annotation("project", pid)
    user = ctx.me()
    rows = "".join(
        f'<tr><td><a href="#/sessions/{s["id"]}">{h(s["title"])}</a> {archived_badge(s)}</td><td>{badge(s["status"])}</td>'
        f'<td class="small">{fmt_time(s["created_at"])}</td><td class="small">{h(s["author"] or "")}</td>'
        f'<td>{tag_chips(s["tags"])}</td></tr>' for s in sessions)
    admin = ""
    if user["is_admin"]:
        arch = bool(p["archived_at"])
        admin = act_html("Unarchive project" if arch else "Archive project", "archive", pid,
                         kind="project", archived="0" if arch else "1")
    toggle = f'#/projects/{pid}' + ("" if show_arch else "?archived=1")
    return TagList(
        HTML(f"""<p class="small"><a href="#/">← Dashboard</a></p>
        <div class="row-flex"><h1>{h(p['name'])}</h1>{archived_badge(p)}
        <div class="right row-flex"><a class="btn btn-sm btn-primary" href="#/new-session?project={pid}">New session</a>
        {admin}</div></div><div>{tag_chips(p['tags'])}</div>"""),
        tags.div(
            HTML(f"<p>{h(p['description']) or '<span class=muted>No description.</span>'}</p>{annotation_view(ann)}"),
            collapsible("Edit description", area("p_desc", "Description", p["description"], rows=4),
                        btn("p_desc_save", "Save description")),
            annotation_inputs(ann, p["tags"]),
            class_="box", style="margin-top:10px"),
        HTML(f"""<div class="row-flex"><h2>Sessions</h2><a class="small right" href="{toggle}">
        {'Hide' if show_arch else 'Show'} archived</a></div>
        <div class="box"><table class="t"><tr><th>Title</th><th>Status</th><th>Created</th><th>Author</th><th>Tags</th></tr>
        {rows}</table>{'' if rows else '<p class="muted">No sessions yet.</p>'}</div>"""),
    )


def register(ctx: Ctx):
    @reactive.effect
    @reactive.event(ctx.input.p_desc_save)
    def _save_desc():
        name, pid, _ = ctx.route()
        u = ctx.me()
        if u and name == "project":
            run(ctx, lambda: ctx.tracker.update_project_description(u["id"], pid, ctx.val("p_desc", "")), "Saved.")
