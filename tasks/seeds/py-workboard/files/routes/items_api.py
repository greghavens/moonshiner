"""JSON API: work items."""
from httpio import bad_request, int_param, json_response
from router import endpoint


@endpoint
def list_items(ctx, request, params):
    project_id = int_param(params, "project_id")
    return json_response({"items": ctx.items.list_for(project_id)})


@endpoint
def create_item(ctx, request, params):
    project_id = int_param(params, "project_id")
    body = request.json()
    if not isinstance(body, dict):
        return bad_request("expected a JSON object body")
    item = ctx.items.add(project_id, body.get("title"))
    return json_response(item, status=201)


@endpoint
def close_item(ctx, request, params):
    item_id = int_param(params, "item_id")
    return json_response(ctx.items.close(item_id))


URLS = [
    ("GET", "/api/projects/<project_id>/items", "list_items"),
    ("POST", "/api/projects/<project_id>/items", "create_item"),
    ("POST", "/api/items/<item_id>/close", "close_item"),
]
