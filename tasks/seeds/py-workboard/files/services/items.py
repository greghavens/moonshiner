"""Work-item operations."""


class ItemService:
    def __init__(self, store, summary):
        self._store = store
        self._summary = summary

    def add(self, project_id, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("item title must be a non-empty string")
        item = self._store.create_item(project_id, title.strip())
        self._summary.invalidate_project(project_id)
        return item

    def close(self, item_id):
        item = self._store.update_item(item_id, status="done")
        self._summary.invalidate_project(item["project_id"])
        return item

    def list_for(self, project_id):
        return self._store.items_for_project(project_id)
