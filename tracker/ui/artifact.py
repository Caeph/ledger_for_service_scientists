"""Artifact page (version history + lineage) and single-version preview page."""
import csv
import io

from htmltools import HTML, TagList

from .common import (Ctx, act_html, annotation_view, badge, fmt_size, fmt_time, h, kind_of, kv_text, method_label,
                     tag_chips)


def _annotation_form(v, ann):
    """Per-version form handled by the generic action bridge (several of these share one page)."""
    return f"""<details><summary>Edit annotation &amp; tags</summary><div data-form>
    <label class="form-label">Text</label><textarea class="form-control" name="text" rows="2">{h(ann['text'])}</textarea>
    <label class="form-label">Key = value fields (one per line)</label>
    <textarea class="form-control mono" name="kv" rows="2">{h(kv_text(ann['kv']))}</textarea>
    <label class="form-label">Tags (comma separated)</label>
    <input class="form-control" name="tags" value="{h(', '.join(v['tags']))}">
    <div style="margin-top:8px">{act_html('Save', 'annotate_version', v['id'], cls='btn-primary')}</div></div></details>"""


def page(ctx: Ctx, aid):
    a = ctx.tracker.artifact(aid)
    rows = []
    for v in a["versions"]:
        prov = ""
        if v["used_in"]:
            prov += '<div class="small"><b>Used in sessions:</b> ' + ", ".join(
                f'<a href="#/sessions/{s["id"]}">{h(s["title"])}</a> {badge(s["status"])}' for s in v["used_in"]) + "</div>"
        for s in v["produced_by"]:
            ins = ", ".join(f'<a href="#/artifacts/{i["artifact_id"]}">{h(i["original_filename"])}</a> v{i["version_no"]}'
                            for i in s["inputs"]) or "—"
            meth = method_label(s["method"])
            prov += f"""<div class="lineage small"><b>Produced by session:</b> <a href="#/sessions/{s['id']}">
            {h(s['title'])}</a> {badge(s['status'])}<br><b>Inputs:</b> {ins}<br><b>Method:</b> {meth}</div>"""
        if v["snapshot_for"]:
            prov += '<div class="small"><b>Code snapshot for:</b> ' + ", ".join(
                f'<a href="#/sessions/{s["id"]}">{h(s["title"])}</a>' for s in v["snapshot_for"]) + "</div>"
        rows.append(f"""<div class="box" style="margin-bottom:10px"><div class="row-flex"><h3>v{v['version_no']}</h3>
        <span class="small muted">{h(v['original_filename'])}</span>
        <span class="right small"><a href="#/versions/{v['id']}">preview</a> ·
        <a href="/files/versions/{v['id']}/download">download</a></span></div>
        <table class="kv" style="width:100%"><tr><th>SHA-256</th><td class="mono" style="word-break:break-all">{h(v['blob_sha256'])}</td></tr>
        <tr><th>Size</th><td>{fmt_size(v['size_bytes'])} · {h(v['mime_type'])}</td></tr>
        <tr><th>Uploaded</th><td>{h(v['uploader'] or '')} · {fmt_time(v['uploaded_at'])}</td></tr>
        <tr><th>Note</th><td>{h(v['note']) or '—'}</td></tr></table>{prov}
        <div>{tag_chips(v['tags'])}</div>{annotation_view(v['annotation'])}{_annotation_form(v, v['annotation'])}</div>""")
    color = {"INPUT": "var(--in)", "METHOD": "var(--me)", "OUTPUT": "var(--out)"}[a["class"]]
    return HTML(f"""<p class="small"><a href="#/">← Dashboard</a></p><div class="row-flex"><h1>{h(a['name'])}</h1>
    <span class="badge-st" style="color:{color};border:1px solid {color}">{a['class']}</span></div>
    <p class="small muted">{len(a['versions'])} version(s), newest first. Versions are immutable.</p>{''.join(rows)}""")


def preview_page(ctx: Ctx, vid):
    v, data = ctx.tracker.read_version(vid)
    kind = kind_of(v["mime_type"], v["original_filename"])
    raw = f"/files/versions/{vid}/raw"
    if kind == "image":
        view = f'<img class="preview" src="{raw}" alt="{h(v["original_filename"])}">'
    elif kind == "pdf":
        view = f'<iframe class="preview" src="{raw}"></iframe>'
    elif kind == "html":
        view = f'<iframe class="preview" sandbox="allow-scripts" src="{raw}"></iframe>'
    elif kind == "text":
        text = data.decode("utf-8", "replace")
        name = v["original_filename"].lower()
        if name.endswith((".tsv", ".csv")):
            delim = "\t" if name.endswith(".tsv") else ","
            rows = []
            for i, r in enumerate(csv.reader(io.StringIO(text), delimiter=delim)):
                if i > 200:
                    break
                cell = "th" if i == 0 else "td"
                rows.append("<tr>" + "".join(f"<{cell}>{h(c)}</{cell}>" for c in r) + "</tr>")
            more = '<p class="small muted">Showing first 200 rows.</p>' if len(rows) > 200 else ""
            view = f'<div class="box" style="overflow:auto;max-height:75vh"><table class="t">{"".join(rows)}</table></div>{more}'
        else:
            lines = text.splitlines()
            more = '<p class="small muted">Showing first 2000 lines.</p>' if len(lines) > 2000 else ""
            view = f'<pre class="box mono" style="overflow:auto;max-height:75vh">{h(chr(10).join(lines[:2000]))}</pre>{more}'
    elif kind == "excel":
        view = '<div class="box">Excel files are download-only in v1.</div>'
    else:
        view = '<div class="box">No inline preview for this file type.</div>'
    return HTML(f"""<p class="small"><a href="#/artifacts/{v['artifact_id']}">← {h(v['name'])} (all versions)</a></p>
    <div class="row-flex"><h1>{h(v['original_filename'])} <span class="muted">v{v['version_no']}</span></h1>
    <a class="btn btn-sm btn-primary right" href="/files/versions/{vid}/download">Download</a></div>
    <p class="small muted mono" style="word-break:break-all">sha256 {h(v['blob_sha256'])} · {fmt_size(v['size_bytes'])}</p>
    {view}""")
