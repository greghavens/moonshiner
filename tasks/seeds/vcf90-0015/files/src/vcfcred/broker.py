"""Credential broker: hands the managed secret to callers and rotates it.

Callers borrow the managed secret with :meth:`CredentialBroker.lease`. A lease is
held for as long as the caller is using the secret, which for an outbound request
means for the whole duration of that request.

Rotation retires the secret on the SDDC Manager side. Any request that is still
carrying the retiring secret when SDDC Manager applies the change is stranded: it
will come back 401. Rotation therefore has to be sequenced against the leases
rather than merely announced to them.
"""

import threading
import time
from contextlib import contextmanager

from .client import SddcManagerClient
from .spec import OPERATION_ROTATE, TargetCredential

TERMINAL_STATUSES = ("SUCCESSFUL", "FAILED", "CANCELLED")


class RotationFailed(Exception):
    def __init__(self, task_id, status, task):
        super().__init__("credentials task %s finished %s" % (task_id, status))
        self.task_id = task_id
        self.status = status
        self.task = task


class RotationTimeout(Exception):
    pass


class RotationResult:
    def __init__(self, task_id, status, password):
        self.task_id = task_id
        self.status = status
        self.password = password


class Lease:
    """A borrowed generation of the managed secret."""

    def __init__(self, broker, password):
        self._broker = broker
        self._password = password
        self._released = False

    @property
    def password(self):
        return self._password

    def create_token(self):
        """Authenticate to SDDC Manager with the leased secret."""
        return self._broker.client.create_token(self._broker.target.username, self._password)

    def release(self):
        if not self._released:
            self._released = True
            self._broker._release()


class CredentialBroker:
    def __init__(
        self,
        base_url,
        admin_username,
        admin_password,
        target,
        initial_password,
        client=None,
        poll_interval=0.02,
        poll_timeout=20.0,
    ):
        self.target = target
        self.client = client or SddcManagerClient(base_url)
        self._admin_username = admin_username
        self._admin_password = admin_password
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

        self._condition = threading.Condition(threading.Lock())
        self._password = initial_password
        self._in_flight = 0
        self._gate_open = True
        self._token = None
        self._token_lock = threading.Lock()

    # -- secret access --------------------------------------------------------

    def current_password(self):
        with self._condition:
            return self._password

    def leases_in_flight(self):
        with self._condition:
            return self._in_flight

    @contextmanager
    def lease(self):
        borrowed = self._acquire()
        try:
            yield borrowed
        finally:
            borrowed.release()

    def _acquire(self):
        with self._condition:
            password = self._password
            while not self._gate_open:
                self._condition.wait()
            self._in_flight += 1
        return Lease(self, password)

    def _release(self):
        with self._condition:
            self._in_flight -= 1
            self._condition.notify_all()

    # -- gate -----------------------------------------------------------------

    def _close_gate(self):
        with self._condition:
            self._gate_open = False

    def _open_gate(self):
        with self._condition:
            self._gate_open = True
            self._condition.notify_all()

    # -- rotation -------------------------------------------------------------

    def _access_token(self):
        with self._token_lock:
            if self._token is None:
                self._token = self.client.create_token(
                    self._admin_username, self._admin_password
                )
            return self._token

    def _resolve(self, token):
        """Confirm the managed credential is present and pick up its id."""
        elements = self.client.list_credentials(
            token,
            resourceName=self.target.resource_name,
            resourceType=self.target.resource_type,
            accountType=self.target.account_type,
        )
        for element in elements:
            if element.get("username") == self.target.username:
                if self.target.credential_id is None:
                    self.target.credential_id = element.get("id")
                return element
        raise LookupError("no credential for %s" % (self.target.username,))

    def _await_task(self, task_id, token):
        deadline = time.monotonic() + self._poll_timeout
        while True:
            task = self.client.get_credentials_task(task_id, token)
            if task.get("status") in TERMINAL_STATUSES:
                return task
            if time.monotonic() >= deadline:
                raise RotationTimeout("credentials task %s did not settle" % task_id)
            time.sleep(self._poll_interval)

    def rotate(self, operation_type=OPERATION_ROTATE):
        """Rotate the managed secret and publish the replacement."""
        token = self._access_token()
        self._resolve(token)

        self._close_gate()
        try:
            acknowledgement = self.client.rotate_passwords(
                self.target, token, operation_type=operation_type
            )
            task_id = acknowledgement["id"]
            task = self._await_task(task_id, token)
            if task["status"] != "SUCCESSFUL":
                raise RotationFailed(task_id, task["status"], task)

            credential = self.client.get_credential(self.target.credential_id, token)
            replacement = credential.get("password")
            if not replacement:
                raise RotationFailed(task_id, task["status"], task)
            with self._condition:
                self._password = replacement
            return RotationResult(task_id, task["status"], replacement)
        finally:
            self._open_gate()
