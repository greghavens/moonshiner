"""Request bodies for the SDDC Manager credential operations.

Every object built here is serialized straight to JSON by the client, so the
dictionaries produced here are the wire representation.
"""

OPERATION_UPDATE = "UPDATE"
OPERATION_ROTATE = "ROTATE"
OPERATION_REMEDIATE = "REMEDIATE"


class TargetCredential:
    """The credential a broker manages.

    Only ``username`` and ``resource_type`` are structurally required by
    CredentialsUpdateSpec. The remaining attributes may be ``None``, which means
    the caller does not have a value for them.
    """

    def __init__(
        self,
        credential_id,
        username,
        resource_type,
        resource_name=None,
        resource_id=None,
        account_type=None,
        credential_type=None,
    ):
        self.credential_id = credential_id
        self.username = username
        self.resource_type = resource_type
        self.resource_name = resource_name
        self.resource_id = resource_id
        self.account_type = account_type
        self.credential_type = credential_type


def build_token_spec(username, password):
    """TokenCreationSpec for createToken."""
    return {
        "username": username,
        "password": password,
        "apiKey": "",
        "idToken": "",
    }


def build_update_spec(target, operation_type, password=None):
    """CredentialsUpdateSpec for updateOrRotatePasswords."""
    credential = {
        "credentialType": target.credential_type or "",
        "accountType": target.account_type or "",
        "username": target.username,
        "password": password or "",
    }
    element = {
        "resourceName": target.resource_name or "",
        "resourceId": target.resource_id or "",
        "resourceType": target.resource_type,
        "credentials": [credential],
    }
    return {
        "operationType": operation_type,
        "elements": [element],
        "autoRotatePolicy": None,
    }
