"""Thin Azure Key Vault data-plane session used by our deploy tooling.

Handles bearer-token injection and JSON decoding; callers deal in vault
URLs. The data-plane API version is date-based since 2025 -- 2025-07-01 is
the current GA version for the secrets operations we use.
"""

import json
import urllib.error
import urllib.request

API_VERSION = "2025-07-01"


class VaultSession:
    """One vault + one credential. ``token_provider`` is a zero-arg callable
    returning a bearer token; tests inject a dummy."""

    def __init__(self, vault_url, token_provider, opener=None):
        self.vault_url = vault_url.rstrip("/")
        self._token_provider = token_provider
        self._opener = opener or urllib.request.build_opener()

    def get_json(self, url):
        """GET an absolute vault URL.

        Returns ``(status, headers, body)`` where ``headers`` is a plain dict
        and ``body`` is the decoded JSON object ({} when the response has no
        JSON body). HTTP error statuses are returned, not raised, so callers
        can map them.
        """
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer " + self._token_provider(),
                "Accept": "application/json",
            },
        )
        try:
            with self._opener.open(request) as response:
                return response.status, dict(response.headers), _decode(response.read())
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), _decode(err.read())

    def get_secret(self, name):
        """Fetch a single secret VALUE (used by deploy jobs; inventory code
        must never call this -- values do not belong in reports)."""
        url = "%s/secrets/%s?api-version=%s" % (self.vault_url, name, API_VERSION)
        status, _headers, body = self.get_json(url)
        if status != 200:
            raise RuntimeError("get_secret(%s) failed with HTTP %d" % (name, status))
        return body["value"]


def _decode(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}
