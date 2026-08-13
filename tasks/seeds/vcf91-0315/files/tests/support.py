"""Shared test fixtures. PROTECTED FILE -- do not modify."""

import os
import tempfile
import unittest

from mock import MockConfig, MockProvisioningService

TOKEN = "vcfa-mock-token"
API_VERSION = "2021-07-15"
POLL_INTERVAL = 2.5


class SleepRecorder:
    """Stands in for ``time.sleep`` so the tests never actually wait."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


class MockServiceTestCase(unittest.TestCase):
    """Starts the in-process HTTP fixture for each test and tears it down."""

    config_overrides = {}

    def setUp(self):
        handle, self.log_path = tempfile.mkstemp(prefix="vcfa-requests-", suffix=".jsonl")
        os.close(handle)
        self.addCleanup(self._remove_log)

        overrides = dict(self.config_overrides)
        overrides.setdefault("token", TOKEN)
        overrides.setdefault("api_version", API_VERSION)
        self.config = MockConfig(**overrides)

        self.service = MockProvisioningService(self.log_path, self.config)
        self.base_url = self.service.start()
        self.addCleanup(self.service.stop)

        self.sleeper = SleepRecorder()

    def _remove_log(self):
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def make_client(self, **kwargs):
        from vcfa_provision import VcfAutomationClient

        params = {
            "poll_interval": POLL_INTERVAL,
            "max_poll_attempts": 10,
            "sleep": self.sleeper,
        }
        params.update(kwargs)
        return VcfAutomationClient(self.base_url, TOKEN, API_VERSION, **params)

    def requests(self):
        return self.service.requests()

    def wire_calls(self):
        """(method, path) for every request the mock received, in order."""
        return [(entry["method"], entry["path"]) for entry in self.requests()]


def minimal_spec(**overrides):
    from vcfa_provision import MachineSpec

    params = {
        "name": "app-node-01",
        "project_id": "0f3a1c58-9b74-42d6-8e05-7c1d2b9a6e43",
        "flavor": "medium",
        "flavor_ref": "vcf.medium",
        "image": "ubuntu-22-04",
        "image_ref": "content-library/ubuntu-22-04-server",
    }
    params.update(overrides)
    return MachineSpec(**params)


MINIMAL_WIRE_KEYS = {
    "name", "projectId", "flavor", "flavorRef", "image", "imageRef",
}


def walk(value, path="$"):
    """Yield (json-pointer-ish path, value) for every node in a decoded body."""
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            for item in walk(child, "{0}.{1}".format(path, key)):
                yield item
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for item in walk(child, "{0}[{1}]".format(path, index)):
                yield item
