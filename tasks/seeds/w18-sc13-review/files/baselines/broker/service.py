"""Previous production implementation of package retrieval."""


def authorize_package(package, context, audit):
    if package.tenant_id != context.tenant_id:
        audit("tenant_mismatch", context, package.package_id)
        raise PermissionError(package.package_id)
    if package.owner_id != context.actor_id:
        audit("owner_mismatch", context, package.package_id)
        raise LookupError(package.package_id)
    return package.contents
