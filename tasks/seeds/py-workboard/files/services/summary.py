"""Dashboard summaries.

A summary row is the cached, page-ready view of one project: its name,
archived flag, and open/done item counts. Pages render exclusively from
these rows; write paths call invalidate_project() after touching anything
a row is derived from.
"""


class SummaryService:
    def __init__(self, store, cache):
        self._store = store
        self._cache = cache

    @staticmethod
    def row_key(project_id):
        return ("project-row", project_id)

    def project_row(self, project_id):
        return self._cache.get_or(self.row_key(project_id),
                                  lambda: self._build_row(project_id))

    def dashboard_rows(self):
        rows = [self.project_row(p["id"])
                for p in self._store.list_projects()]
        return [r for r in rows if not r["archived"]]

    def invalidate_project(self, project_id):
        self._cache.invalidate(self.row_key(project_id))

    def _build_row(self, project_id):
        project = self._store.get_project(project_id)
        items = self._store.items_for_project(project_id)
        return {
            "id": project["id"],
            "name": project["name"],
            "archived": project["archived"],
            "open_items": sum(1 for i in items if i["status"] == "open"),
            "done_items": sum(1 for i in items if i["status"] == "done"),
        }
