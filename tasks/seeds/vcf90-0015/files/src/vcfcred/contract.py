"""Access to the local REST contract in docs/contract.json."""

import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONTRACT_PATH = os.path.join(_ROOT, "docs", "contract.json")


class ContractError(Exception):
    """Raised when an operation is not part of the contract."""


class Contract:
    def __init__(self, path=None):
        self.path = path or DEFAULT_CONTRACT_PATH
        with open(self.path, "r", encoding="utf-8") as handle:
            self._document = json.load(handle)
        self._operations = {op["operationId"]: op for op in self._document["operations"]}

    @property
    def source(self):
        return self._document["source"]

    def operation_ids(self):
        return list(self._operations)

    def operation(self, operation_id):
        try:
            return self._operations[operation_id]
        except KeyError:
            raise ContractError(
                "operation %r is not named by %s" % (operation_id, self.path)
            )

    def method(self, operation_id):
        return self.operation(operation_id)["method"].upper()

    def success_status(self, operation_id):
        return self.operation(operation_id)["success"]["status"]

    def requires_auth(self, operation_id):
        return bool(self.operation(operation_id).get("authenticated"))

    def path_for(self, operation_id, **path_params):
        template = self.operation(operation_id)["path"]
        rendered = template
        for name, value in path_params.items():
            placeholder = "{%s}" % name
            if placeholder not in rendered:
                raise ContractError(
                    "operation %r has no path parameter %r" % (operation_id, name)
                )
            rendered = rendered.replace(placeholder, str(value))
        if "{" in rendered:
            raise ContractError("unresolved path parameters in %r" % (rendered,))
        return rendered
