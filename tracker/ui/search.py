"""Search across projects, sessions and file versions (live as you type)."""
from htmltools import HTML, TagList, tags
from shiny import render, ui

from .common import Ctx, badge, h, txt


def page(ctx: Ctx, query):
    return TagList(
        HTML("<h1>Search</h1>"),
        tags.div(txt("search_q", None, query.get("q", ""), placeholder="Search…"), style="max-width:480px"),
        HTML('<p class="small muted">Searches titles, notes, annotations, tags, filenames and commit SHAs.</p>'),
        ui.output_ui("search_results"),
    )


def register(ctx: Ctx):
    @render.ui
    def search_results():
        q = (ctx.val("search_q", "") or "").strip()
        if not q:
            return None
        r = ctx.tracker.search(q)

        def sec(title, items):
            return (f'<h2>{title} <span class="muted small">({len(items)})</span></h2><div class="box">'
                    + ("".join(items) or '<p class="muted small">No matches.</p>') + "</div>")
        return HTML(
            sec("Projects", [f'<div class="item"><a href="#/projects/{p["id"]}">{h(p["name"])}</a> '
                             f'<span class="small muted">{h(p["description"][:140])}</span></div>' for p in r["projects"]])
            + sec("Sessions", [f'<div class="item"><a href="#/sessions/{s["id"]}">{h(s["title"])}</a> {badge(s["status"])} '
                               f'<span class="small muted">{h(s["notes"][:140])}</span></div>' for s in r["sessions"]])
            + sec("File versions", [f'<div class="item"><a href="#/artifacts/{v["artifact_id"]}">{h(v["original_filename"])}'
                                    f'</a> v{v["version_no"]} <span class="small muted">{v["class"]} · {h(v["note"][:100])}'
                                    f'</span></div>' for v in r["versions"]]))
