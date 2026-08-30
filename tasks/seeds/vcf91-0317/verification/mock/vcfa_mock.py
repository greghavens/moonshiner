#!/usr/bin/env python3
"""Loopback mock of the two VCF Automation 9.1 catalog operations named in
docs/contract.json.

The mock is deliberately strict: it implements the contract, including the
clauses the contract pins where the Broadcom xAPIs reference is ambiguous, and
rejects any request that deviates. It serves nothing else -- every route the
contract does not name answers 404.

It listens on 127.0.0.1 only. It never contacts a VMware endpoint.

Usage:
    python3 mock/vcfa_mock.py --log /tmp/requests.jsonl [--port 0] [--token TOK]

On startup it prints a single line "READY <host> <port>" to stdout and flushes,
so a caller can wait for readiness and learn the ephemeral port.

Every request -- accepted or rejected -- appends one JSON object to the log
file, one object per line. Log entry fields:

    seq            1-based arrival order
    method         HTTP method
    path           request path, no query string
    raw_query      the query string exactly as received, "" if none
    query_pairs    [[name, value], ...] in wire order, blanks preserved
    headers        {lowercased name: value} for the headers below
    status         HTTP status this request was answered with
    rejection      null, or a short reason when status >= 400
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "dataset.json")

SUPPORTED_VERSIONS = {"9.1.0"}
DEFAULT_TOKEN = "vcfa-test-token"

# Contract: conventions.collection_query_style
FORBIDDEN_PARAMS = ("$top", "$skip", "$orderby", "$filter")

# Contract: operations[listCatalogItems].query_parameters
LIST_PARAMS = {
    "page": "integer",
    "size": "integer",
    "sort": "string[]",
    "search": "string",
    "projects": "string[]",
    "types": "string[]",
    "expandProjects": "boolean",
    "expand": "string",
}
# Contract: operations[getCatalogItem].query_parameters
ITEM_PARAMS = {"expandProjects": "boolean", "expand": "string"}

EXPAND_MEMBERS = {"spec", "user"}

PROJECT_NAMES = {
    "a4f1c0d2-1b34-4a55-8c66-9d77e88f9a01": "Platform Engineering",
    "b5e2d1c3-2c45-4b66-9d77-ae88f99a0b12": "Payments",
    "c6f3e2d4-3d56-4c77-ae88-bf99a00b1c23": "Data Services",
}

LATER_OCCURRENCE_SUFFIX = " [updated after first collection sighting]"


class ContractViolation(Exception):
    def __init__(self, status, error_code, message):
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message


def load_dataset():
    with open(DATASET, encoding="utf-8") as fh:
        raw = json.load(fh)
    by_id = {item["id"]: item for item in raw["items"]}
    if len(by_id) != len(raw["items"]):
        raise SystemExit("dataset.json contains duplicate ids")
    for item_id in raw["serverOrder"]:
        if item_id not in by_id:
            raise SystemExit("serverOrder references unknown id %s" % item_id)
    return by_id, list(raw["serverOrder"])


def parse_pairs(raw_query):
    """Split a raw query string into ordered (name, value) pairs.

    Written by hand rather than with parse_qs because the contract's
    omission_of_unset_parameters clause makes "search=" meaningfully different
    from an absent "search", and parse_qs erases that distinction.
    """
    if not raw_query:
        return []
    pairs = []
    for chunk in raw_query.split("&"):
        if chunk == "":
            pairs.append(("", ""))
            continue
        name, sep, value = chunk.partition("=")
        pairs.append((unquote_plus(name), unquote_plus(value) if sep else None))
    return pairs


def check_params(pairs, allowed):
    """Validate the wire shape of a query string against the contract."""
    seen = {}
    for name, value in pairs:
        if name == "":
            raise ContractViolation(400, "INVALID_QUERY", "empty query segment")
        if name in FORBIDDEN_PARAMS:
            raise ContractViolation(
                400,
                "FORBIDDEN_QUERY_PARAMETER",
                "parameter %r is forbidden by the contract; use page/size" % name,
            )
        if name not in allowed:
            raise ContractViolation(
                400,
                "UNKNOWN_QUERY_PARAMETER",
                "parameter %r is not defined for this operation" % name,
            )
        # Contract: conventions.array_parameter_serialization
        if name in seen:
            raise ContractViolation(
                400,
                "REPEATED_QUERY_PARAMETER",
                "parameter %r was sent %d times; multi-valued parameters must be "
                "sent once, comma-joined" % (name, seen[name] + 1),
            )
        seen[name] = 1
        # Contract: conventions.omission_of_unset_parameters
        if value is None:
            raise ContractViolation(
                400,
                "VALUELESS_QUERY_PARAMETER",
                "parameter %r was sent without a value; unset parameters must be "
                "omitted" % name,
            )
        if value.strip() == "":
            raise ContractViolation(
                400,
                "EMPTY_QUERY_PARAMETER",
                "parameter %r was sent empty; unset parameters must be omitted "
                "rather than sent empty" % name,
            )
        if value.strip().lower() in ("none", "null", "nil", "undefined"):
            raise ContractViolation(
                400,
                "EMPTY_QUERY_PARAMETER",
                "parameter %r was sent as %r; unset parameters must be omitted"
                % (name, value),
            )

        kind = allowed[name]
        if kind == "integer":
            if not (value.isdigit() or (value.startswith("-") and value[1:].isdigit())):
                raise ContractViolation(
                    400, "INVALID_QUERY", "parameter %r must be an integer" % name
                )
        elif kind == "boolean":
            # Contract: conventions.boolean_parameter_serialization
            if value not in ("true", "false"):
                raise ContractViolation(
                    400,
                    "INVALID_BOOLEAN",
                    "parameter %r must be the lowercase literal 'true' or 'false', "
                    "got %r" % (name, value),
                )
        elif kind == "string[]":
            if any(part.strip() == "" for part in value.split(",")):
                raise ContractViolation(
                    400,
                    "INVALID_QUERY",
                    "parameter %r has an empty member in its comma-joined value"
                    % name,
                )
    return {name: value for name, value in pairs}


def check_expand(values):
    for member in values.get("expand", "").split(","):
        member = member.strip()
        if member and member not in EXPAND_MEMBERS:
            raise ContractViolation(
                400, "INVALID_QUERY", "expand member %r is not supported" % member
            )


def project_view(item):
    return [
        {"id": pid, "name": PROJECT_NAMES.get(pid, "Unknown Project")}
        for pid in item["projectIds"]
    ]


def render(item, values):
    out = dict(item)
    if values.get("expandProjects") == "true":
        out["projects"] = project_view(item)
    expand = {m.strip() for m in values.get("expand", "").split(",") if m.strip()}
    if "spec" in expand:
        out["spec"] = {"catalogItemId": item["id"], "inputs": {}}
    if "user" in expand:
        out["creator"] = {"email": item["createdBy"], "name": "Service Catalog"}
        out["lastUpdater"] = {"email": item["lastUpdatedBy"], "name": "Provider Admin"}
    return out


class Catalog:
    def __init__(self, by_id, server_order):
        self.by_id = by_id
        self.server_order = server_order

    def matching(self, values):
        """Apply the contract's filter parameters, preserving server order."""
        ids = list(self.server_order)

        search = values.get("search")
        if search is not None:
            needle = search.casefold()
            ids = [
                i
                for i in ids
                if needle in self.by_id[i]["name"].casefold()
                or needle in self.by_id[i]["description"].casefold()
            ]

        projects = values.get("projects")
        if projects is not None:
            wanted = {p.strip() for p in projects.split(",")}
            ids = [i for i in ids if wanted & set(self.by_id[i]["projectIds"])]

        types = values.get("types")
        if types is not None:
            wanted = {t.strip() for t in types.split(",")}
            ids = [i for i in ids if self.by_id[i]["type"]["id"] in wanted]

        sort = values.get("sort")
        if sort is not None:
            # A server-side sort produces a consistent snapshot, so the
            # re-sort drift described by pagination.id_stability cannot occur.
            # The pinned one-occurrence array form flattens each
            # property,direction member into adjacent comma-separated tokens.
            tokens = [token.strip() for token in sort.split(",")]
            if len(tokens) % 2:
                raise ContractViolation(
                    400,
                    "INVALID_SORT",
                    "sort must contain property,direction pairs",
                )
            criteria = []
            for offset in range(0, len(tokens), 2):
                prop, direction = tokens[offset : offset + 2]
                if prop not in ("name", "id", "createdAt", "lastUpdatedAt"):
                    raise ContractViolation(
                        400, "INVALID_SORT", "cannot sort on property %r" % prop
                    )
                direction = direction.lower()
                if direction not in ("asc", "desc"):
                    raise ContractViolation(
                        400, "INVALID_SORT", "sort direction must be asc or desc"
                    )
                criteria.append((prop, direction))
            unique = list(dict.fromkeys(ids))
            # Stable sorts in reverse criterion order implement the declared
            # precedence without imposing an unrequested tie breaker.
            for prop, direction in reversed(criteria):
                unique.sort(
                    key=lambda i, field=prop: self.by_id[i][field],
                    reverse=direction == "desc",
                )
            ids = unique

        return ids

    def page(self, values):
        ids = self.matching(values)
        page = int(values.get("page", 0))
        size = int(values.get("size", 20))
        if page < 0:
            raise ContractViolation(400, "INVALID_QUERY", "page must be >= 0")
        if size < 1 or size > 2000:
            raise ContractViolation(400, "INVALID_QUERY", "size must be 1..2000")

        total_pages = max(1, -(-len(ids) // size))
        if page > total_pages - 1:
            # Contract pagination.page_range.
            raise ContractViolation(
                400,
                "PAGE_OUT_OF_RANGE",
                "page %d is past the last page (%d); the walk should have stopped "
                "when last was true" % (page, total_pages - 1),
            )

        start = page * size
        window = ids[start : start + size]
        first_positions = {}
        for position, item_id in enumerate(ids):
            first_positions.setdefault(item_id, position)
        content = []
        for offset, item_id in enumerate(window, start=start):
            item = render(self.by_id[item_id], values)
            if offset != first_positions[item_id]:
                # pagination.id_stability pins first-wins de-duplication. A
                # later occurrence carries a changed non-key field so replacing
                # the first object is observably wrong.
                item["description"] += LATER_OCCURRENCE_SUFFIX
            content.append(item)
        sorted_flag = values.get("sort") is not None
        return {
            "content": content,
            "empty": not window,
            "first": page == 0,
            "last": page >= total_pages - 1,
            "number": page,
            "numberOfElements": len(window),
            "pageable": {
                "offset": page * size,
                "pageNumber": page,
                "pageSize": size,
                "paged": True,
                "unpaged": False,
                "sort": {
                    "empty": not sorted_flag,
                    "sorted": sorted_flag,
                    "unsorted": not sorted_flag,
                },
            },
            "size": size,
            "sort": {
                "empty": not sorted_flag,
                "sorted": sorted_flag,
                "unsorted": not sorted_flag,
            },
            # Contract PageOfCatalogItem.totalElements: entries the query yields,
            # repeats included -- not a count of distinct catalog items.
            "totalElements": len(ids),
            "totalPages": total_pages,
        }


class RequestLog:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.seq = 0
        with open(path, "w", encoding="utf-8"):
            pass

    def append(self, entry):
        with self.lock:
            self.seq += 1
            entry["seq"] = self.seq
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vcfa-mock/1.0"
    sys_version = ""

    catalog = None
    request_log = None
    token = DEFAULT_TOKEN

    def log_message(self, fmt, *args):  # silence stderr access logging
        pass

    # -- contract checks ---------------------------------------------------

    def check_auth(self):
        header = self.headers.get("Authorization")
        if not header:
            raise ContractViolation(401, "UNAUTHORIZED", "Authorization header absent")
        if not header.startswith("Bearer "):
            raise ContractViolation(
                401, "UNAUTHORIZED", "Authorization header is not a bearer token"
            )
        presented = header[len("Bearer ") :]
        if presented != self.token:
            raise ContractViolation(401, "UNAUTHORIZED", "bearer token not recognised")

    def check_accept(self):
        header = self.headers.get("Accept")
        if not header:
            raise ContractViolation(
                406, "NOT_ACCEPTABLE", "Accept header absent; API version is required"
            )
        media, _, params = header.partition(";")
        if media.strip() != "application/json":
            raise ContractViolation(
                406, "NOT_ACCEPTABLE", "media type %r is not served" % media.strip()
            )
        version = None
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip() == "version":
                version = value.strip()
        if version is None:
            raise ContractViolation(
                406,
                "NOT_ACCEPTABLE",
                "Accept header carries no version parameter; expected "
                "application/json;version=9.1.0",
            )
        if version not in SUPPORTED_VERSIONS:
            raise ContractViolation(
                406, "NOT_ACCEPTABLE", "API version %r is not served" % version
            )

    # -- routing -----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        pairs = parse_pairs(parsed.query)
        status = 500
        rejection = None
        body = None
        try:
            path = parsed.path.rstrip("/") or "/"
            if path == "/catalog/api/items":
                self.check_auth()
                self.check_accept()
                values = check_params(pairs, LIST_PARAMS)
                check_expand(values)
                body = self.catalog.page(values)
            elif path.startswith("/catalog/api/items/") and path.count("/") == 4:
                self.check_auth()
                self.check_accept()
                values = check_params(pairs, ITEM_PARAMS)
                check_expand(values)
                item_id = path.rsplit("/", 1)[1]
                item = self.catalog.by_id.get(item_id)
                if item is None:
                    raise ContractViolation(
                        404, "NOT_FOUND", "no catalog item with id %r" % item_id
                    )
                body = render(item, values)
            else:
                raise ContractViolation(
                    404,
                    "NOT_FOUND",
                    "%s is not an operation named by docs/contract.json" % parsed.path,
                )
            status = 200
        except ContractViolation as exc:
            status = exc.status
            rejection = "%s: %s" % (exc.error_code, exc.message)
            body = {
                "serverMessage": exc.message,
                "errorCode": exc.error_code,
                "statusCode": exc.status,
            }
        finally:
            self.record(parsed, pairs, status, rejection)

        self.respond(status, body)

    def do_POST(self):
        self.reject_method()

    def do_PUT(self):
        self.reject_method()

    def do_PATCH(self):
        self.reject_method()

    def do_DELETE(self):
        self.reject_method()

    def reject_method(self):
        parsed = urlparse(self.path)
        pairs = parse_pairs(parsed.query)
        message = "%s is not an operation named by docs/contract.json" % self.command
        self.record(parsed, pairs, 405, "METHOD_NOT_ALLOWED: " + message)
        self.respond(
            405,
            {
                "serverMessage": message,
                "errorCode": "METHOD_NOT_ALLOWED",
                "statusCode": 405,
            },
            extra_headers=[("Allow", "GET")],
        )

    # -- plumbing ----------------------------------------------------------

    def record(self, parsed, pairs, status, rejection):
        self.request_log.append(
            {
                "method": self.command,
                "path": parsed.path,
                "raw_query": parsed.query,
                "query_pairs": [[n, v] for n, v in pairs],
                "headers": {
                    name: self.headers.get(name.title())
                    for name in ("authorization", "accept", "user-agent", "host")
                },
                "status": status,
                "rejection": rejection,
            }
        )

    def respond(self, status, body, extra_headers=()):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json;version=9.1.0")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--log", required=True, help="path for the JSONL request log")
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("this mock binds loopback only")

    by_id, server_order = load_dataset()
    Handler.catalog = Catalog(by_id, server_order)
    Handler.request_log = RequestLog(args.log)
    Handler.token = args.token

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    host, port = httpd.socket.getsockname()[:2]
    sys.stdout.write("READY %s %d\n" % (host, port))
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
