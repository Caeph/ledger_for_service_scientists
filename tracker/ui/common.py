"""Shared helpers for the Shiny pages: formatting, small HTML pieces, CSS and the browser-side glue."""
from __future__ import annotations

import html
import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from htmltools import HTML, Tag, TagList, tags
from shiny import ui

h = html.escape


# ------------------------------------------------------------------ page context
@dataclass
class Ctx:
    """What every page module gets: the service layer and helpers bound to this Shiny session."""
    tracker: Any
    input: Any
    session: Any
    me: Callable[[], dict | None]          # current user (re-checked, so expired logins stop working)
    bump: Callable[[], None]               # re-render the current page after a write
    goto: Callable[[str], Any]             # async: navigate to a #/route
    notify: Callable[[str, str], None]     # (message, "message"|"error")
    val: Callable[..., Any]                # val(input_id, default) for inputs that may not exist yet
    files: Callable[[str], list]           # fresh (not yet consumed) uploads of a file input
    consume: Callable[[str], None]         # mark a file input's current uploads as used
    route: Callable[[], tuple]             # (name, id, query)
    max_bytes: int = 500 * 1024 * 1024
    extra: dict = field(default_factory=dict)


def run(ctx: Ctx, fn, ok: str | None = None):
    """Run a service call; show its error as a notification instead of crashing the session."""
    from ..services import ServiceError
    try:
        result = fn()
    except ServiceError as e:
        ctx.notify(str(e), "error")
        return None
    if ok:
        ctx.notify(ok, "message")
    ctx.bump()
    return result if result is not None else True


# ------------------------------------------------------------------ formatting
def fmt_time(t):
    return h((t or "").replace("T", " ").replace("Z", " UTC")) if t else "—"


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def tag_chips(tag_list):
    return "".join(f'<a class="tag" href="#/search?q={urllib.parse.quote(t)}">{h(t)}</a>' for t in tag_list)


def badge(status):
    return f'<span class="badge-st {h(status)}">{h(status)}</span>'


def archived_badge(row):
    return '<span class="badge-st archived">archived</span>' if row.get("archived_at") else ""


def kind_of(mime, name):
    name = name.lower()
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime in ("text/html", "application/xhtml+xml") or name.endswith((".html", ".htm")):
        return "html"
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return "excel"
    if mime.startswith("text/") or name.endswith((".tsv", ".csv", ".txt", ".py", ".r", ".md", ".json", ".ipynb",
                                                   ".yaml", ".yml", ".log", ".sh", ".sql", ".rmd", ".qmd")):
        return "text"
    return "binary"


def entity_link(et, eid):
    if et == "project":
        return f'<a href="#/projects/{eid}">project #{eid}</a>'
    if et == "session":
        return f'<a href="#/sessions/{eid}">session #{eid}</a>'
    if et == "artifact_version":
        return f'<a href="#/versions/{eid}">version #{eid}</a>'
    return f"{h(et)} #{eid}"


def annotation_view(ann):
    out = ""
    if ann.get("text"):
        out += f"<p>{h(ann['text'])}</p>"
    if ann.get("kv"):
        out += ('<table class="kv">' + "".join(f"<tr><th>{h(k)}</th><td>{h(str(v))}</td></tr>"
                                              for k, v in ann["kv"].items()) + "</table>")
    return out or '<p class="muted small">No annotation yet.</p>'


def method_label(m):
    """One-line description of a method ref: git commit, script snapshot, or both."""
    if not m:
        return "—"
    parts = []
    if m.get("commit_sha"):
        parts.append(f'<span class="mono" style="word-break:break-all">{h(m["repo_name"] or m["repo_path"])} @ '
                     f'{h(m["commit_sha"])}</span>')
    snap = m.get("snapshot") or {}
    name, no = snap.get("name") or m.get("snapshot_name"), snap.get("version_no") or m.get("snapshot_no")
    art = snap.get("artifact_id") or m.get("snapshot_artifact_id")
    if name:
        parts.append(f'script <a href="#/artifacts/{art}">{h(name)}</a> v{no}')
    return " + ".join(parts) or "—"


def kv_text(kv):
    return "\n".join(f"{k}={v}" for k, v in (kv or {}).items())


# ------------------------------------------------------------------ input shortcuts
def txt(id, label, value="", **kw):
    return ui.input_text(id, label, value=value or "", width="100%", **kw)


def area(id, label, value="", rows=3):
    return ui.input_text_area(id, label, value=value or "", width="100%", rows=rows)


def btn(id, label, cls="btn-primary"):
    return ui.input_action_button(id, label, class_=f"btn-sm {cls}")


def collapsible(summary, *children, open=False):
    return tags.details(tags.summary(summary), *children, open=open or None)


def annotation_inputs(ann, tags_list):
    """Native Shiny form for the page's main annotation (project or session)."""
    return collapsible("Edit annotation & tags",
                       area("ann_text", "Text", ann.get("text", "")),
                       area("ann_kv", "Key = value fields (one per line)", kv_text(ann.get("kv"))),
                       txt("ann_tags", "Tags (comma separated)", ", ".join(tags_list)),
                       btn("ann_save", "Save annotation"))


def act_button(label, act, id=None, cls="btn-outline-secondary", confirm=None, **data):
    """A button handled by the generic 'act' bridge (for per-row actions)."""
    attrs = {"data-act": act, "class": f"btn btn-sm {cls}", "type": "button"}
    if id is not None:
        attrs["data-id"] = str(id)
    if confirm:
        attrs["data-confirm"] = confirm
    for k, v in data.items():
        attrs[f"data-{k}"] = str(v)
    return tags.button(label, **attrs)


def act_html(label, act, id=None, cls="btn-outline-secondary", **data):
    return str(act_button(label, act, id, cls, **data))


# ------------------------------------------------------------------ head: CSS + JS
CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--accent:#2f5fd0;
--in:#2f7d4f;--me:#8a5a00;--out:#7a3fb0;--warn:#b3261e;--warn-bg:#fdecea;--open:#e8f0fe;--open-ink:#1f4fa8;
--fin:#e6f4ea;--fin-ink:#1e6b3a}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--card:#1f1f22;--ink:#ececef;--muted:#a0a0a8;--line:#323236;
--accent:#7aa2ff;--in:#6fcf97;--me:#e3b341;--out:#c39bf0;--warn:#ff8a80;--warn-bg:#3a1c1a;--open:#1c2a4a;
--open-ink:#a9c3ff;--fin:#173323;--fin-ink:#8fe0ad}}
body{background:var(--bg)!important;color:var(--ink);font-size:15px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.topbar{display:flex;gap:18px;align-items:center;padding:10px 24px;background:var(--card);border-bottom:1px solid var(--line);
flex-wrap:wrap;margin:0 -12px}.topbar .brand{font-weight:700;color:var(--ink)}.topbar nav{display:flex;gap:14px;flex:1}
.topbar input{max-width:220px}
.main{max-width:1280px;margin:0 auto;padding:20px 12px 60px}
h1{font-size:24px;margin:4px 0 6px;font-weight:650}h2{font-size:17px;margin:22px 0 8px;font-weight:650}
h3{font-size:15px;margin:0 0 8px;font-weight:650}
.muted{color:var(--muted)}.small{font-size:13px}.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.cols{display:grid;gap:14px;grid-template-columns:1fr 1fr 1fr}@media(max-width:900px){.cols{grid-template-columns:1fr}}
.col-INPUT{border-top:4px solid var(--in)}.col-METHOD{border-top:4px solid var(--me)}.col-OUTPUT{border-top:4px solid var(--out)}
.tag{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 8px;margin:2px 4px 2px 0;
font-size:12px;color:var(--muted);background:var(--bg)}
.badge-st{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:600;vertical-align:middle}
.badge-st.open{background:var(--open);color:var(--open-ink)}.badge-st.finalized,.badge-st.verified{background:var(--fin);color:var(--fin-ink)}
.badge-st.unverified,.badge-st.archived,.badge-st.newer{background:var(--warn-bg);color:var(--warn)}
table.t{width:100%;border-collapse:collapse}table.t th,table.t td,table.kv th,table.kv td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);vertical-align:top}table.t th,table.kv th{font-size:12px;text-transform:uppercase;
letter-spacing:.04em;color:var(--muted);font-weight:600}
.item{border-bottom:1px solid var(--line);padding:8px 0}.item:last-child{border-bottom:0}
details>summary{cursor:pointer;color:var(--accent);font-weight:600;margin:10px 0 6px}
.thumb{max-width:100%;max-height:120px;border-radius:6px;border:1px solid var(--line);margin-top:6px;background:#fff}
iframe.preview{width:100%;height:75vh;border:1px solid var(--line);border-radius:8px;background:#fff}
img.preview{max-width:100%;border:1px solid var(--line);border-radius:8px;background:#fff}
.lineage{border-left:3px solid var(--accent);padding-left:12px;margin:8px 0}
.warnbox{border:1px solid var(--warn);border-radius:10px;padding:10px 14px;background:var(--card)}
.row-flex{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.right{margin-left:auto}
.shiny-input-container{margin-bottom:8px}.form-label,.control-label{font-size:13px;color:var(--muted);margin-bottom:2px}
.feed li{margin:3px 0}.btn-link-act{background:none;border:0;padding:0;color:var(--muted);font-size:12px}
.loading{padding:40px;color:var(--muted)}
.btn-outline-secondary{--bs-btn-color:var(--ink);--bs-btn-border-color:var(--line);--bs-btn-hover-bg:var(--line);
--bs-btn-hover-color:var(--ink);--bs-btn-hover-border-color:var(--muted);--bs-btn-active-bg:var(--line);
--bs-btn-active-color:var(--ink)}
"""

JS = """
(function(){
  // Bootstrap dark mode follows the OS setting
  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  const setTheme = () => document.documentElement.setAttribute('data-bs-theme', mq.matches ? 'dark' : 'light');
  setTheme(); mq.addEventListener('change', setTheme);

  // Hash routing: #/sessions/3 -> input.route
  function sendRoute(){ if (window.Shiny && Shiny.setInputValue) Shiny.setInputValue('route', location.hash || '#/', {priority:'event'}); }
  $(document).on('shiny:sessioninitialized', sendRoute);  // not 'connected': that fires before Shiny's init message
  window.addEventListener('hashchange', () => { sendRoute(); window.scrollTo(0, 0); });

  // Navigation and redirects requested by the server
  $(document).on('shiny:connected', () => {
    Shiny.addCustomMessageHandler('goto', m => {
      if (m.url) { location.href = m.url; return; }
      if (location.hash === m.hash) sendRoute(); else location.hash = m.hash;
    });
  });

  // Generic action bridge for per-row buttons: <button data-act="..." data-id="..."> inside an optional [data-form]
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    e.preventDefault();
    if (b.dataset.confirm && !confirm(b.dataset.confirm)) return;
    const fields = {}; const box = b.closest('[data-form]');
    if (box) box.querySelectorAll('[name]').forEach(el => {
      if ((el.type === 'checkbox' || el.type === 'radio') && !el.checked) return;
      fields[el.name] = el.value;
    });
    const data = Object.assign({}, b.dataset); delete data.act; delete data.id; delete data.confirm;
    Shiny.setInputValue('act', {act: b.dataset.act, id: b.dataset.id ? +b.dataset.id : null, fields, data,
                                nonce: Math.random()}, {priority: 'event'});
  });

  // Header search box -> #/search?q=
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.id === 'top-search') {
      location.hash = '#/search?q=' + encodeURIComponent(e.target.value); e.target.value = '';
    }
  });
})();
"""


def head():
    return TagList(tags.style(HTML(CSS)), tags.script(HTML(JS)), tags.title("Analysis Tracker"))


def topbar(user):
    admin = '<a href="#/admin">Admin</a>' if user["is_admin"] else ""
    return HTML(f"""<div class="topbar"><a class="brand" href="#/">Analysis Tracker</a>
    <nav><a href="#/">Dashboard</a><a href="#/new-session">New session</a>{admin}</nav>
    <input id="top-search" class="form-control form-control-sm" type="search" placeholder="Search…">
    <span class="muted small">{h(user['display_name'])}</span>
    <form method="post" action="/logout" style="margin:0"><button class="btn btn-sm btn-outline-secondary">Log out</button></form>
    </div>""")


def json_attr(obj):
    return h(json.dumps(obj))
