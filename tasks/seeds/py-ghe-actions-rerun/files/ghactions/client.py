"""Thin GitHub Enterprise Cloud REST client used by the Actions tooling.

Speaks the current REST conventions: the vnd.github+json accept media type,
the dated X-GitHub-Api-Version header, bearer auth, and GitHub's
{message, documentation_url} error bodies.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = "application/vnd.github+json"
API_VERSION = "2026-03-10"
USER_AGENT = "shop-floor-actions-monitor/1.0"


class GitHubApiError(Exception):
    """A non-2xx GitHub REST response, with the decoded message field."""

    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(f"github api error {status}: {message}")


class GitHubClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self._token = token

    def request(self, method, url, body=None):
        """Perform one authenticated API call; returns (status, headers, raw)."""
        data = None
        req = urllib.request.Request(url, method=method)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        req.add_header("Accept", ACCEPT)
        req.add_header("X-GitHub-Api-Version", API_VERSION)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, data) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            message = exc.reason
            try:
                decoded = json.loads(raw.decode("utf-8"))
                message = decoded.get("message", message)
            except (ValueError, UnicodeDecodeError):
                pass
            raise GitHubApiError(exc.code, message) from None

    def get_json(self, url):
        status, headers, raw = self.request("GET", url)
        return json.loads(raw.decode("utf-8")), headers

    def list_workflow_runs(self, owner, repo, status=None, branch=None,
                           per_page=30):
        """One page of workflow runs: {total_count, workflow_runs}."""
        params = {"per_page": str(per_page)}
        if status is not None:
            params["status"] = status
        if branch is not None:
            params["branch"] = branch
        url = (f"{self.base_url}/repos/{owner}/{repo}/actions/runs"
               f"?{urllib.parse.urlencode(params)}")
        page, _headers = self.get_json(url)
        return page

    def get_workflow_run(self, owner, repo, run_id):
        url = f"{self.base_url}/repos/{owner}/{repo}/actions/runs/{run_id}"
        run, _headers = self.get_json(url)
        return run
