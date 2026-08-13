#!/usr/bin/env python3
"""Derive docs/contract.json and docs/official_sources.json from the pinned
VCF Operations OpenAPI specification.

The specification is *not* vendored into this repository. Fetch it yourself from
the pinned commit and point this script at the local copy:

    curl -sSLO https://raw.githubusercontent.com/vmware/vcf-api-specs/\
85151f6b1bb58f13b6ac0304bfec53904bea085f/specifications/vcf-operations/vcf-operations-openapi.json
    python3 tools/derive_contract.py --spec vcf-operations-openapi.json

Nothing in the test path runs this script; it exists so the generated contract
can be re-derived and audited against the specification.
"""

import argparse
import hashlib
import json
import pathlib
import sys

REPOSITORY = "https://github.com/vmware/vcf-api-specs"
LICENSE = "Apache-2.0"
TAG = "9.0.0.0"
COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-openapi.json"
BLOB_SHA1 = "622d099b91f5944cc5a5abe6dd4830501220486a"
SHA256 = "e5c7bac42fcde67011f8f880144b1a5c690e737380f5275e694be29bb545dd0e"

# operationId -> (authorization mode, human note)
OPERATIONS = {
    "acquireToken": ("issues-token", "Exchanges username/password for an access token."),
    "getCurrentVersionOfServer": ("bearer", "Probed by Connect-VcfOpsServer right after the token is issued."),
    "queryAlert": ("bearer", "Paged alert search driven by an alert-query filter body."),
    "modifyAlerts": ("bearer", "Bulk alert action; the action is a required query parameter."),
    "releaseToken": ("bearer", "Invalidates the access token at the end of the run."),
}


def json_pointer(path, method):
    escaped = path.replace("~", "~0").replace("/", "~1")
    return "#/paths/{}/{}".format(escaped, method)


def resolve_ref(ref):
    return ref.rsplit("/", 1)[-1]


def describe_property(name, prop):
    out = {"name": name}
    if "$ref" in prop:
        out["type"] = "object"
        out["schema"] = resolve_ref(prop["$ref"])
        return out
    out["type"] = prop.get("type", "string")
    if out["type"] == "array":
        items = prop.get("items", {})
        if "$ref" in items:
            out["items"] = {"type": "object", "schema": resolve_ref(items["$ref"])}
        else:
            item = {"type": items.get("type", "string")}
            if items.get("format"):
                item["format"] = items["format"]
            if items.get("enum"):
                item["enum"] = items["enum"]
            out["items"] = item
    else:
        if prop.get("format"):
            out["format"] = prop["format"]
        if prop.get("enum"):
            out["enum"] = prop["enum"]
    return out


def describe_schema(schemas, name):
    schema = schemas[name]
    required = sorted(schema.get("required", []))
    props = schema.get("properties", {})
    return {
        "schema": name,
        "requiredProperties": required,
        "optionalProperties": sorted(k for k in props if k not in required),
        "properties": {k: describe_property(k, v) for k, v in sorted(props.items())},
    }


def describe_parameters(op):
    query = {}
    path_params = {}
    for param in op.get("parameters", []):
        entry = {
            "required": bool(param.get("required", False)),
            "type": param.get("schema", {}).get("type", "string"),
        }
        if "default" in param.get("schema", {}):
            entry["default"] = param["schema"]["default"]
        if param["in"] == "query":
            query[param["name"]] = entry
        elif param["in"] == "path":
            path_params[param["name"]] = entry
    return query, path_params


def build(spec):
    schemas = spec["components"]["schemas"]
    index = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                index[op["operationId"]] = (path, method, op)

    base_path = spec["servers"][0]["url"]
    operations = {}
    referenced = set()
    for op_id, (auth, note) in OPERATIONS.items():
        if op_id not in index:
            sys.exit("operationId %r is not present in the specification" % op_id)
        path, method, op = index[op_id]
        query, path_params = describe_parameters(op)
        entry = {
            "operationId": op_id,
            "method": method.upper(),
            "path": path,
            "requestPath": base_path + path,
            "specPointer": json_pointer(path, method),
            "authorization": auth,
            "note": note,
            "queryParameters": query,
        }
        if path_params:
            entry["pathParameters"] = path_params
        body = op.get("requestBody")
        if body:
            schema_name = resolve_ref(body["content"]["application/json"]["schema"]["$ref"])
            referenced.add(schema_name)
            entry["requestBody"] = dict(
                describe_schema(schemas, schema_name),
                required=bool(body.get("required", False)),
                contentType="application/json",
            )
        responses = {}
        for status, resp in sorted(op.get("responses", {}).items()):
            schema = resp.get("content", {}).get("application/json", {}).get("schema")
            if schema and "$ref" in schema:
                name = resolve_ref(schema["$ref"])
                referenced.add(name)
                responses[status] = {"schema": name}
            else:
                responses[status] = {"schema": None}
        entry["responses"] = responses
        operations[op_id] = entry

    # Response models the mock has to be able to render.
    for name in ("alerts", "alert", "page-info", "auth-token", "version"):
        referenced.add(name)
    models = {name: describe_schema(schemas, name) for name in sorted(referenced)}

    return {
        "$comment": (
            "Generated by tools/derive_contract.py from the pinned VCF Operations "
            "OpenAPI specification. Do not hand-edit."
        ),
        "source": {
            "repository": REPOSITORY,
            "license": LICENSE,
            "tag": TAG,
            "commit": COMMIT,
            "path": SPEC_PATH,
            "openapi": spec["openapi"],
            "title": spec["info"]["title"],
        },
        "securityScheme": {
            "header": "Authorization",
            "valuePrefix": "OpsToken ",
            "specName": "Token-based-authorization",
        },
        "basePath": base_path,
        "operations": operations,
        "models": models,
    }


def sources_doc(contract):
    return {
        "$comment": "Provenance for docs/contract.json. Every entry is checkable against the pinned commit.",
        "specification": {
            "repository": REPOSITORY,
            "license": LICENSE,
            "path": SPEC_PATH,
            "tag": TAG,
            "commit": COMMIT,
            "gitBlobSha1": BLOB_SHA1,
            "sha256": SHA256,
            "rawUrl": "https://raw.githubusercontent.com/vmware/vcf-api-specs/{}/{}".format(COMMIT, SPEC_PATH),
            "treeUrl": "https://github.com/vmware/vcf-api-specs/blob/{}/{}".format(TAG, SPEC_PATH),
            "openapi": contract["source"]["openapi"],
            "title": contract["source"]["title"],
            "basePath": contract["basePath"],
        },
        "notPinned": {
            "tag": "9.1.0.0",
            "path": SPEC_PATH,
            "gitBlobSha1": "a56a00c504d4156aa765339c47d585d15db68768",
            "reason": (
                "The 9.1 revision of the same file is a different blob and is explicitly "
                "not the source of this contract."
            ),
        },
        "operations": [
            {
                "operationId": op["operationId"],
                "method": op["method"],
                "path": op["path"],
                "requestPath": op["requestPath"],
                "specPointer": op["specPointer"],
            }
            for op in sorted(contract["operations"].values(), key=lambda o: o["operationId"])
        ],
        "sdk": {
            "name": "VMware.Sdk.Vcf.Ops",
            "source": "https://www.powershellgallery.com/packages/VMware.Sdk.Vcf.Ops",
            "role": (
                "PowerCLI module that issues every request in this contract. Installed by the "
                "environment as a prerequisite; never vendored into this repository."
            ),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="local copy of vcf-operations-openapi.json")
    ap.add_argument("--out-dir", default="docs")
    args = ap.parse_args()

    raw = pathlib.Path(args.spec).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SHA256:
        sys.exit(
            "spec sha256 mismatch: expected %s (tag %s), got %s. Fetch the file at commit %s."
            % (SHA256, TAG, digest, COMMIT)
        )

    spec = json.loads(raw)
    contract = build(spec)
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "contract.json").write_text(json.dumps(contract, indent=2, sort_keys=False) + "\n")
    (out / "official_sources.json").write_text(json.dumps(sources_doc(contract), indent=2) + "\n")
    print("wrote %s and %s" % (out / "contract.json", out / "official_sources.json"))


if __name__ == "__main__":
    main()
