"""Admin page: users, archived items, audit log. Every action is also checked in the service layer."""
from htmltools import HTML, TagList, tags
from shiny import reactive, ui

from .common import Ctx, act_html, btn, collapsible, entity_link, fmt_time, h, run, txt


def page(ctx: Ctx):
    t = ctx.tracker
    users = "".join(f"""<tr><td>{h(u['username'])}</td><td>{h(u['display_name'])}</td>
        <td>{'admin' if u['is_admin'] else 'user'}</td><td>{'active' if u['is_active'] else '<b>inactive</b>'}</td>
        <td><div class="row-flex" data-form>
        {act_html('Deactivate' if u['is_active'] else 'Activate', 'user_active', u['id'], active=0 if u['is_active'] else 1)}
        <input type="password" name="password" class="form-control form-control-sm" placeholder="new password"
         style="width:150px" autocomplete="new-password">
        {act_html('Reset password', 'user_password', u['id'])}</div></td></tr>""" for u in t.list_users())
    arch = t.archived_items()
    arch_html = "".join(
        f'<div class="item">Project <a href="#/projects/{p["id"]}">{h(p["name"])}</a> · {fmt_time(p["archived_at"])} '
        f'{act_html("Unarchive", "archive", p["id"], kind="project", archived=0)}</div>' for p in arch["projects"]) + "".join(
        f'<div class="item">Session <a href="#/sessions/{s["id"]}">{h(s["title"])}</a> · {fmt_time(s["archived_at"])} '
        f'{act_html("Unarchive", "archive", s["id"], kind="session", archived=0)}</div>' for s in arch["sessions"])
    audit = "".join(f'<tr><td class="small">{fmt_time(a["at"])}</td><td>{h(a["display_name"] or "")}</td>'
                    f'<td>{h(a["action"])}</td><td>{entity_link(a["entity_type"], a["entity_id"])}</td>'
                    f'<td class="mono small">{h(a["details"][:160])}</td></tr>' for a in t.audit(100))
    return TagList(
        HTML("<h1>Admin</h1><h2>Users</h2>"),
        tags.div(
            HTML(f"""<table class="t"><tr><th>Username</th><th>Name</th>
            <th>Role</th><th>Status</th><th>Actions</th></tr>{users}</table>"""),
            collapsible("+ Create user", tags.div(
                txt("nu_username", "Username"), txt("nu_display", "Display name"),
                ui.input_password("nu_password", "Initial password (min 8)", width="100%"),
                ui.input_checkbox("nu_admin", "admin"), btn("nu_create", "Create user"), style="max-width:420px")),
            class_="box"),
        HTML(f"""<h2>Archived items</h2><div class="box">{arch_html or '<p class="muted small">Nothing archived.</p>'}</div>
        <h2>Audit log (latest 100)</h2><div class="box" style="overflow:auto"><table class="t"><tr><th>When</th><th>User</th>
        <th>Action</th><th>Entity</th><th>Details</th></tr>{audit}</table></div>"""),
    )


def register(ctx: Ctx):
    @reactive.effect
    @reactive.event(ctx.input.nu_create)
    def _create_user():
        u = ctx.me()
        if u:
            run(ctx, lambda: ctx.tracker.create_user(u["id"], ctx.val("nu_username", ""), ctx.val("nu_display", ""),
                                                     ctx.val("nu_password", ""), bool(ctx.val("nu_admin", False))),
                "User created.")
