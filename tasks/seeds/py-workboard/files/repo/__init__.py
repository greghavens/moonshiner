"""In-memory persistence layer.

One Store per app instance. Rows are plain dicts; the store hands out
copies so callers can't reach into its tables, and every mutation goes
through an explicit method. A sqlite backend is planned once the image
gets a writable data dir; keep this interface stable.
"""
import itertools


class NotFoundError(LookupError):
    """Raised for lookups of ids that don't exist (mapped to HTTP 404)."""


class Store:
    def __init__(self):
        self._projects = {}
        self._items = {}
        self._project_ids = itertools.count(1)
        self._item_ids = itertools.count(1)

    # -- projects ----------------------------------------------------

    def create_project(self, name):
        project = {"id": next(self._project_ids), "name": name,
                   "archived": False}
        self._projects[project["id"]] = project
        return dict(project)

    def get_project(self, project_id):
        try:
            return dict(self._projects[project_id])
        except KeyError:
            raise NotFoundError("no project with id %s" % project_id)

    def update_project(self, project_id, **fields):
        if project_id not in self._projects:
            raise NotFoundError("no project with id %s" % project_id)
        row = self._projects[project_id]
        for key, value in fields.items():
            if key not in row or key == "id":
                raise ValueError("unknown project field %r" % key)
            row[key] = value
        return dict(row)

    def list_projects(self):
        return [dict(p) for p in sorted(self._projects.values(),
                                        key=lambda p: p["id"])]

    # -- items -------------------------------------------------------

    def create_item(self, project_id, title):
        if project_id not in self._projects:
            raise NotFoundError("no project with id %s" % project_id)
        item = {"id": next(self._item_ids), "project_id": project_id,
                "title": title, "status": "open"}
        self._items[item["id"]] = item
        return dict(item)

    def get_item(self, item_id):
        try:
            return dict(self._items[item_id])
        except KeyError:
            raise NotFoundError("no item with id %s" % item_id)

    def update_item(self, item_id, **fields):
        if item_id not in self._items:
            raise NotFoundError("no item with id %s" % item_id)
        row = self._items[item_id]
        for key, value in fields.items():
            if key not in row or key in ("id", "project_id"):
                raise ValueError("unknown item field %r" % key)
            row[key] = value
        return dict(row)

    def items_for_project(self, project_id):
        if project_id not in self._projects:
            raise NotFoundError("no project with id %s" % project_id)
        rows = [i for i in self._items.values()
                if i["project_id"] == project_id]
        return [dict(i) for i in sorted(rows, key=lambda i: i["id"])]
