"""Server-rendered HTML pages."""
from html import escape

import templates
from httpio import html_response, int_param
from router import endpoint


@endpoint
def dashboard(ctx, request, params):
    rows = ctx.summary.dashboard_rows()
    if rows:
        body = "".join(
            templates.fragment("dashboard_row", id=row["id"],
                               name=escape(row["name"]),
                               open=row["open_items"],
                               done=row["done_items"])
            for row in rows)
    else:
        body = templates.fragment("dashboard_empty")
    page = templates.render("dashboard", title="Dashboard",
                            count=len(rows), rows=body)
    return html_response(page)


@endpoint
def project_page(ctx, request, params):
    project_id = int_param(params, "project_id")
    row = ctx.summary.project_row(project_id)
    items = ctx.items.list_for(project_id)
    if items:
        body = "".join(
            templates.fragment("project_item", status=item["status"],
                               title=escape(item["title"]))
            for item in items)
    else:
        body = templates.fragment("project_no_items")
    page = templates.render("project", title=escape(row["name"]),
                            name=escape(row["name"]),
                            open=row["open_items"], done=row["done_items"],
                            items=body)
    return html_response(page)


URLS = [
    ("GET", "/", "dashboard"),
    ("GET", "/projects/<project_id>", "project_page"),
]
