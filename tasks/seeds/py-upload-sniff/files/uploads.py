"""Screening for files posted to the attachments endpoint.

The web layer hands us the client-supplied filename and the raw bytes;
we decide whether to accept the upload before anything touches disk.
"""

DEFAULT_MAX_BYTES = 5 * 1024 * 1024

DEFAULT_ALLOWED = frozenset({
    "png", "jpg", "jpeg", "gif", "pdf", "txt", "zip", "docx",
})


class UploadError(Exception):
    """Base class: anything we reject raises a subclass of this."""


class TooLargeError(UploadError):
    pass


class ExtensionError(UploadError):
    pass


def split_ext(filename):
    """Lowercased extension without the dot; '' when there isn't one.

    Only the last path segment counts, and a leading dot alone (dotfiles
    like '.bashrc') is a hidden name, not an extension.
    """
    name = filename.rsplit("/", 1)[-1]
    if name.startswith(".") and name.count(".") == 1:
        return ""
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


class UploadValidator:
    def __init__(self, max_bytes=DEFAULT_MAX_BYTES, allowed=None):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        source = DEFAULT_ALLOWED if allowed is None else allowed
        self.allowed = frozenset(ext.lower() for ext in source)

    def validate(self, filename, data):
        """Return the normalized extension, or raise an UploadError."""
        if not filename or not filename.strip():
            raise ExtensionError("missing filename")
        if len(data) > self.max_bytes:
            raise TooLargeError("%d bytes exceeds the %d-byte limit"
                                % (len(data), self.max_bytes))
        ext = split_ext(filename)
        if not ext:
            raise ExtensionError("no file extension: %r" % (filename,))
        if ext not in self.allowed:
            raise ExtensionError("extension not allowed: %r" % (ext,))
        return ext
