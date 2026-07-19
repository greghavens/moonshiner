"""Deployed settings for the document-index service."""

MIDDLEWARE = [
    "service.web.SecurityHeadersMiddleware",
    "service.web.RequestIdMiddleware",
    "service.web.SessionMiddleware",
    "service.web.AuditMiddleware",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "var/service-index.sqlite3",
        "CONN_MAX_AGE": 45,
        "ATOMIC_REQUESTS": True,
    }
}

DEFAULT_FILE_STORAGE = "service.storage.TenantDocumentStorage"
DEFAULT_FILE_STORAGE_OPTIONS = {
    "root": "var/documents",
    "permissions": "private",
}
STATICFILES_STORAGE = "service.storage.ManifestStaticStorage"
STATICFILES_STORAGE_OPTIONS = {
    "manifest": "assets/static-manifest.json",
}

