# workboard

A small internal work-item board the platform team runs on the ops subnet.
Pure stdlib on purpose: it ships inside the provisioning image, where pip is
not available. Server-rendered HTML pages for humans, a JSON API for the
CLI tooling and dashboards.

## Layout

    app.py            composition root: create_app() returns a WSGI callable
    httpio.py         Request/Response objects + WSGI plumbing
    router.py         url table, endpoint registry, dispatch
    templates.py      HTML templates as string constants + render()
    routes/           handlers: HTML pages and the JSON API
    services/         business logic: projects, items, dashboard summaries
    repo/             in-memory persistence layer

## Running

    python3 app.py            # dev server on http://127.0.0.1:8004

## Endpoints

HTML pages: `GET /` (dashboard), `GET /projects/<id>`.
JSON API under `/api/`: projects CRUD-ish (list/create/get/rename/archive),
items (list/create/close), plus `GET /api/routes` which dumps the live
route table for debugging.

Tests: `python3 test_workboard.py` (drives the WSGI app directly, no
network).
