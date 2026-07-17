"""Service layer wiring.

build_services(store) is the one place the object graph is assembled;
handlers receive the resulting Services as their ctx argument.
"""
from services.cache import Cache
from services.items import ItemService
from services.projects import ProjectService
from services.summary import SummaryService


class Services:
    def __init__(self, store):
        self.store = store
        self.cache = Cache()
        self.summary = SummaryService(store, self.cache)
        self.projects = ProjectService(store, self.summary)
        self.items = ItemService(store, self.summary)


def build_services(store):
    return Services(store)
