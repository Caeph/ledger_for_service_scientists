"""Dashboard (home): one card per project, tag filter, archived toggle, recent activity, new project."""
import urllib.parse

from htmltools import HTML, TagList, tags
from shiny import reactive

from .common import (Ctx, archived_badge, area, btn, collapsible, entity_link, fmt_time, h, run, tag_chips, txt)


def page(ctx: Ctx, query):
    tag = query.get("tag") or None
    show_arch = query.get("archived") == "1"
    projects, all_tags = ctx.tracker.dashboard(include_archived=show_arch, tag=tag)

    def href(**kw):
        q = {"tag": tag or "", "archived": "1" if show_arch else ""}
        q.update(kw)
        q = {k: v for k, v in q.items() if v}
        return "#/" + ("?" + urllib.parse.urlencode(q) if q else "")

    cards = []
    for p in projects:
        lo = p["latest_output"]
        lo_html = '<span class="muted small">No final output yet</span>'
        if lo:
            thumb = (f'<br><img class="thumb" src="/files/versions/{lo["id"]}/raw" alt="">'
                     if lo["mime_type"].startswith("image/") else "")
            lo_html = (f'<span class="small">Latest output: <a href="#/artifacts/{lo["artifact_id"]}">'
                       f'{h(lo["original_filename"])}</a> v{lo["version_no"]}</span>{thumb}')
        cards.append(f"""<div class="box"><div class="row-flex"><h3><a href="#/projects/{p['id']}">{h(p['name'])}</a></h3>
        {archived_badge(p)}</div><div>{tag_chips(p['tags'])}</div>
        <p class="small" style="margin:8px 0"><span class="badge-st open">{p['n_open'] or 0} open</span>
        <span class="badge-st finalized">{p['n_final'] or 0} finalized</span></p>
        <p class="small muted" style="margin:0 0 6px">Last activity: {fmt_time(p['last_activity'])}</p>{lo_html}</div>""")

    tag_opts = "".join(f'<option value="{h(href(tag=t))}" {"selected" if t == tag else ""}>{h(t)}</option>'
                       for t in all_tags)
    filters = f"""<div class="row-flex right">
      <select class="form-select form-select-sm" style="width:auto" onchange="location.hash=this.value">
        <option value="{h(href(tag=''))}">All tags</option>{tag_opts}</select>
      <label class="small"><input type="checkbox" {"checked" if show_arch else ""}
        onchange="location.hash='{h(href(archived='' if show_arch else '1'))}'"> show archived</label>
      <a class="btn btn-sm btn-primary" href="#/new-session">New session</a></div>"""

    feed = "".join(
        f'<li class="small"><span class="muted">{fmt_time(a["at"])}</span> · {h(a["display_name"] or "?")} · '
        f'<b>{h(a["action"])}</b> {entity_link(a["entity_type"], a["entity_id"])}</li>'
        for a in ctx.tracker.recent_activity())

    return TagList(
        HTML(f'<div class="row-flex"><h1>Projects</h1>{filters}</div>'),
        collapsible("+ New project", tags.div(
            txt("np_name", "Name"), area("np_desc", "Description"), txt("np_tags", "Tags (comma separated)"),
            btn("np_create", "Create project"), class_="box", style="max-width:640px")),
        HTML(f'<div class="grid" style="margin-top:12px">'
             f'{"".join(cards) or "<p class=muted>No projects match.</p>"}</div>'
             f'<h2>Recent activity</h2><ul class="feed box">{feed or "<li class=muted>Nothing yet.</li>"}</ul>'),
    )


def register(ctx: Ctx):
    @reactive.effect
    @reactive.event(ctx.input.np_create)
    async def _create_project():
        u = ctx.me()
        if not u:
            return
        pid = run(ctx, lambda: ctx.tracker.create_project(u["id"], ctx.val("np_name", ""), ctx.val("np_desc", ""),
                                                          ctx.val("np_tags", "")), "Project created.")
        if pid:
            await ctx.goto(f"#/projects/{pid}")
