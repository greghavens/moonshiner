"""Contract tests for the inventory API — protected file."""
import pytest

from app import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_list_starts_empty(client):
    r = client.get("/api/items")
    assert r.status_code == 200
    assert r.get_json() == {"items": []}


def test_create_returns_201_and_item(client):
    r = client.post("/api/items", json={"name": "widget"})
    assert r.status_code == 201
    assert r.get_json() == {"id": 1, "name": "widget"}


def test_ids_count_up_and_list_is_ordered(client):
    client.post("/api/items", json={"name": "b"})
    client.post("/api/items", json={"name": "a"})
    r = client.get("/api/items")
    assert r.get_json() == {"items": [{"id": 1, "name": "b"},
                                      {"id": 2, "name": "a"}]}


def test_get_by_id(client):
    client.post("/api/items", json={"name": "widget"})
    r = client.get("/api/items/1")
    assert r.status_code == 200
    assert r.get_json() == {"id": 1, "name": "widget"}


def test_get_unknown_id_is_json_404(client):
    r = client.get("/api/items/99")
    assert r.status_code == 404
    assert r.get_json() == {"error": "not found"}


def test_create_missing_name_400(client):
    r = client.post("/api/items", json={"sku": "x"})
    assert r.status_code == 400
    assert r.get_json() == {"error": "name is required"}


def test_create_blank_name_400(client):
    r = client.post("/api/items", json={"name": "   "})
    assert r.status_code == 400
    assert r.get_json() == {"error": "name is required"}


def test_create_non_json_400(client):
    r = client.post("/api/items", data="name=widget",
                    content_type="text/plain")
    assert r.status_code == 400
    assert r.get_json() == {"error": "name is required"}


def test_unknown_route_is_json_404(client):
    r = client.get("/definitely/not/here")
    assert r.status_code == 404
    assert r.get_json() == {"error": "not found"}


def test_wrong_method_is_json_405(client):
    r = client.delete("/api/items")
    assert r.status_code == 405
    assert r.get_json() == {"error": "method not allowed"}


def test_instances_do_not_share_store():
    a = create_app()
    b = create_app()
    ca, cb = a.test_client(), b.test_client()
    ca.post("/api/items", json={"name": "only-in-a"})
    assert cb.get("/api/items").get_json() == {"items": []}
    assert ca.get("/api/items").get_json()["items"][0]["name"] == "only-in-a"


def test_factory_does_not_export_module_level_app():
    import app as app_module
    assert not hasattr(app_module, "app"), \
        "no module-level app object — factory only"
