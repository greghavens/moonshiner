"""Permission checks for the admin console.

Permissions are dotted "resource.action" strings. Users carry a list of
role names; a request is allowed when any of their roles grants the
permission the endpoint demands.
"""

ROLE_GRANTS = {
    "viewer": frozenset({
        "article.read", "comment.read",
    }),
    "editor": frozenset({
        "article.read", "article.write",
        "comment.read", "comment.write",
    }),
    "moderator": frozenset({
        "comment.read", "comment.hide", "user.warn",
    }),
    "admin": frozenset({
        "article.read", "article.write", "article.delete",
        "comment.read", "comment.write", "comment.hide",
        "user.warn", "user.ban",
    }),
}


class PermissionDenied(PermissionError):
    def __init__(self, permission):
        super().__init__("permission denied: %s" % permission)
        self.permission = permission


def known_roles():
    return sorted(ROLE_GRANTS)


def grants_for(role):
    """Permissions a single role carries; unknown roles are a KeyError."""
    return ROLE_GRANTS[role]


def roles_granting(permission):
    """Which roles carry a permission, sorted — used by the audit screen."""
    return sorted(r for r, perms in ROLE_GRANTS.items() if permission in perms)


def has_permission(roles, permission):
    """True when any of the user's roles grants the permission."""
    granted = set()
    for role in roles:
        granted |= grants_for(role)
    return permission in granted


def require(roles, permission):
    """Gate an endpoint: raises PermissionDenied instead of returning False."""
    if not has_permission(roles, permission):
        raise PermissionDenied(permission)
