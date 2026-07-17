"""Contract tests for the device-registry client — protected file.

All HTTP is mocked with the `responses` library: no real network anywhere.
"""
import pytest
import requests
import responses
from responses import matchers

from client import RegistryClient, RegistryError

BASE = "http://registry.internal.test"
DEVICES = f"{BASE}/devices"


def make_client(**kwargs):
    """Client with a recording fake sleep — tests must never really sleep."""
    sleeps = []
    client = RegistryClient(BASE, sleep=sleeps.append, **kwargs)
    return client, sleeps


# ---------------------------------------------------------------- existing API


@responses.activate
def test_get_device_returns_payload():
    responses.add(responses.GET, f"{DEVICES}/d-42",
                  json={"id": "d-42", "site": "fresno", "status": "online"})
    client = RegistryClient(BASE)
    assert client.get_device("d-42") == {
        "id": "d-42", "site": "fresno", "status": "online"}
    assert len(responses.calls) == 1


@responses.activate
def test_get_device_unknown_id_raises_registry_error():
    responses.add(responses.GET, f"{DEVICES}/d-99",
                  body="no such device", status=404)
    client = RegistryClient(BASE)
    with pytest.raises(RegistryError) as exc:
        client.get_device("d-99")
    assert exc.value.status_code == 404
    assert "no such device" in exc.value.message


# ------------------------------------------------------------- retry/backoff


@responses.activate
def test_get_device_retries_on_503_then_succeeds():
    responses.add(responses.GET, f"{DEVICES}/d-1", body="restarting", status=503)
    responses.add(responses.GET, f"{DEVICES}/d-1", body="restarting", status=503)
    responses.add(responses.GET, f"{DEVICES}/d-1", json={"id": "d-1"})
    client, sleeps = make_client()
    assert client.get_device("d-1") == {"id": "d-1"}
    assert len(responses.calls) == 3
    assert sleeps == [0.5, 1.0]


@responses.activate
def test_retry_budget_exhausted_raises_registry_error_for_503():
    responses.add(responses.GET, f"{DEVICES}/d-1", body="still down", status=503)
    client, sleeps = make_client(max_retries=3)
    with pytest.raises(RegistryError) as exc:
        client.get_device("d-1")
    assert exc.value.status_code == 503
    assert len(responses.calls) == 4  # initial attempt + 3 retries
    assert sleeps == [0.5, 1.0, 2.0]


@responses.activate
def test_connection_error_is_retried():
    responses.add(responses.GET, f"{DEVICES}/d-2",
                  body=requests.exceptions.ConnectionError("dropped"))
    responses.add(responses.GET, f"{DEVICES}/d-2", json={"id": "d-2"})
    client, sleeps = make_client()
    assert client.get_device("d-2") == {"id": "d-2"}
    assert len(responses.calls) == 2
    assert sleeps == [0.5]


@responses.activate
def test_connection_error_exhaustion_propagates_original_error():
    responses.add(responses.GET, f"{DEVICES}/d-2",
                  body=requests.exceptions.ConnectionError("dropped"))
    client, sleeps = make_client(max_retries=1)
    with pytest.raises(requests.exceptions.ConnectionError):
        client.get_device("d-2")
    assert len(responses.calls) == 2
    assert sleeps == [0.5]


@responses.activate
def test_404_is_not_retried():
    responses.add(responses.GET, f"{DEVICES}/d-9", body="gone", status=404)
    client, sleeps = make_client()
    with pytest.raises(RegistryError) as exc:
        client.get_device("d-9")
    assert exc.value.status_code == 404
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_500_is_not_retried():
    responses.add(responses.GET, f"{DEVICES}/d-9", body="boom", status=500)
    client, sleeps = make_client()
    with pytest.raises(RegistryError) as exc:
        client.get_device("d-9")
    assert exc.value.status_code == 500
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_zero_retry_budget_fails_fast():
    responses.add(responses.GET, f"{DEVICES}/d-1", body="down", status=503)
    client, sleeps = make_client(max_retries=0)
    with pytest.raises(RegistryError):
        client.get_device("d-1")
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_backoff_base_is_configurable():
    responses.add(responses.GET, f"{DEVICES}/d-1", body="down", status=503)
    responses.add(responses.GET, f"{DEVICES}/d-1", body="down", status=503)
    responses.add(responses.GET, f"{DEVICES}/d-1", json={"id": "d-1"})
    client, sleeps = make_client(backoff_base=0.25)
    assert client.get_device("d-1") == {"id": "d-1"}
    assert sleeps == [0.25, 0.5]


# ---------------------------------------------------------------- pagination


@responses.activate
def test_list_devices_single_page():
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-1"}, {"id": "d-2"}],
                        "next_page": None},
                  match=[matchers.query_param_matcher({"page": "1"})])
    client, sleeps = make_client()
    assert client.list_devices() == [{"id": "d-1"}, {"id": "d-2"}]
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_list_devices_follows_next_page_in_order():
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-1"}, {"id": "d-2"}], "next_page": 2},
                  match=[matchers.query_param_matcher({"page": "1"})])
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-3"}, {"id": "d-4"}], "next_page": 3},
                  match=[matchers.query_param_matcher({"page": "2"})])
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-5"}], "next_page": None},
                  match=[matchers.query_param_matcher({"page": "3"})])
    client, _ = make_client()
    devices = client.list_devices()
    assert [d["id"] for d in devices] == ["d-1", "d-2", "d-3", "d-4", "d-5"]
    assert len(responses.calls) == 3


@responses.activate
def test_list_devices_sends_site_filter_on_every_page():
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-1"}], "next_page": 2},
                  match=[matchers.query_param_matcher(
                      {"page": "1", "site": "fresno"})])
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-3"}], "next_page": None},
                  match=[matchers.query_param_matcher(
                      {"page": "2", "site": "fresno"})])
    client, _ = make_client()
    devices = client.list_devices(site="fresno")
    assert [d["id"] for d in devices] == ["d-1", "d-3"]


@responses.activate
def test_list_devices_empty_registry():
    responses.add(responses.GET, DEVICES,
                  json={"devices": [], "next_page": None},
                  match=[matchers.query_param_matcher({"page": "1"})])
    client, _ = make_client()
    assert client.list_devices() == []


@responses.activate
def test_list_devices_retries_a_flaky_page():
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-1"}], "next_page": 2},
                  match=[matchers.query_param_matcher({"page": "1"})])
    responses.add(responses.GET, DEVICES, body="restarting", status=503,
                  match=[matchers.query_param_matcher({"page": "2"})])
    responses.add(responses.GET, DEVICES,
                  json={"devices": [{"id": "d-2"}], "next_page": None},
                  match=[matchers.query_param_matcher({"page": "2"})])
    client, sleeps = make_client()
    devices = client.list_devices()
    assert [d["id"] for d in devices] == ["d-1", "d-2"]
    assert len(responses.calls) == 3
    assert sleeps == [0.5]
