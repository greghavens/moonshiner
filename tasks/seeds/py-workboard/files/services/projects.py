"""Project operations."""


def _clean_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("project name must be a non-empty string")
    return name.strip()


class ProjectService:
    def __init__(self, store, summary):
        self._store = store
        self._summary = summary

    def create(self, name):
        return self._store.create_project(_clean_name(name))

    def get(self, project_id):
        return self._store.get_project(project_id)

    def list(self):
        return self._store.list_projects()

    def rename(self, project_id, new_name):
        return self._store.update_project(project_id,
                                          name=_clean_name(new_name))

    def archive(self, project_id):
        project = self._store.update_project(project_id, archived=True)
        self._summary.invalidate_project(project_id)
        return project
