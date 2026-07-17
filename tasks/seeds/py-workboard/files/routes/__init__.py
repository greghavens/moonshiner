"""Route registration.

Each routes module exports URLS: a flat (method, pattern, endpoint-name)
table. register_all() folds them into the router and adds the built-in
route dump used by the ops tooling.
"""
from httpio import json_response
from router import endpoint
from routes import items_api, pages, projects_api

_MODULES = (pages, projects_api, items_api)


def register_all(router):
    seen = set()
    for module in _MODULES:
        for method, pattern, name in module.URLS:
            if (method, pattern) in seen:
                raise ValueError("duplicate route %s %s" % (method, pattern))
            seen.add((method, pattern))
            router.add(method, pattern, name)

    def route_table(ctx, request, params):
        return json_response({"routes": router.table()})

    endpoint(route_table)
    router.add("GET", "/api/routes", "route_table")
