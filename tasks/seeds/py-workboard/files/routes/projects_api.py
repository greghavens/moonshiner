"""JSON API: projects."""
from httpio import bad_request, int_param, json_response
from router import endpoint


def confirmed(fn):
    """Guard for destructive actions: body must carry {"confirm": true}."""
    def wrapper(ctx, request, params):
        body = request.json()
        if not isinstance(body, dict) or body.get("confirm") is not True:
            return bad_request('confirmation required: send {"confirm": true}')
        return fn(ctx, request, params)
    return wrapper


@endpoint
def list_projects(ctx, request, params):
    return json_response({"projects": ctx.projects.list()})


@endpoint
def create_project(ctx, request, params):
    body = request.json()
    if not isinstance(body, dict):
        return bad_request("expected a JSON object body")
    return json_response(ctx.projects.create(body.get("name")), status=201)


@endpoint
def get_project(ctx, request, params):
    project_id = int_param(params, "project_id")
    return json_response(ctx.projects.get(project_id))


@endpoint
def rename_project(ctx, request, params):
    project_id = int_param(params, "project_id")
    body = request.json()
    if not isinstance(body, dict):
        return bad_request("expected a JSON object body")
    return json_response(ctx.projects.rename(project_id, body.get("name")))


@endpoint
@confirmed
def archive_project(ctx, request, params):
    project_id = int_param(params, "project_id")
    return json_response(ctx.projects.archive(project_id))


URLS = [
    ("GET", "/api/projects", "list_projects"),
    ("POST", "/api/projects", "create_project"),
    ("GET", "/api/projects/<project_id>", "get_project"),
    ("PATCH", "/api/projects/<project_id>", "rename_project"),
    ("POST", "/api/projects/<project_id>/archive", "archive_project"),
]
