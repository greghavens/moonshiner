"""formdata.py — multipart/form-data parsing for the inspection intake API.

Field tablets POST inspection reports as multipart/form-data: text fields
(inspector, site, notes) plus file parts (photos, sensor dumps, saved
message digests). This module turns a raw request body into something the
intake handlers can use without dragging in a web framework.

Deliberately tolerant where real clients are messy: header lines we don't
understand are skipped, and a part that arrives without a field name is
dropped rather than failing the whole submission.
"""

__all__ = ["parse_form_data", "FormData", "UploadedFile", "MultipartError"]


class MultipartError(ValueError):
    """Raised when a request body is not usable multipart/form-data."""


class UploadedFile:
    """One file part: filename and content type as sent, payload as bytes."""

    def __init__(self, filename, content_type, data):
        self.filename = filename
        self.content_type = content_type
        self.data = data

    @property
    def size(self):
        return len(self.data)

    def __repr__(self):
        return f"<UploadedFile {self.filename!r} ({self.size} bytes)>"


class FormData:
    """Parsed form: text fields in .fields, file uploads in .files."""

    def __init__(self):
        self.fields = {}
        self.files = {}

    def __repr__(self):
        return f"<FormData fields={sorted(self.fields)} files={sorted(self.files)}>"


def _decode(raw):
    # Header parameters and text fields arrive as raw bytes off the socket;
    # latin-1 maps every byte to a character, so decoding can never fail
    # halfway through a request.
    return raw.decode("latin-1")


def _get_boundary(content_type):
    if not content_type:
        raise MultipartError("missing Content-Type value")
    segments = [seg.strip() for seg in content_type.split(";")]
    if segments[0].lower() != "multipart/form-data":
        raise MultipartError(f"not multipart/form-data: {segments[0]!r}")
    for param in segments[1:]:
        key, _, value = param.partition("=")
        if key.strip().lower() == "boundary":
            value = value.strip()
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            if value:
                return value.encode("ascii")
    raise MultipartError("no boundary parameter in Content-Type")


def _classify_line(line, delim):
    """Is this body line part data, a part delimiter, or the closing terminator?"""
    if not line.startswith(delim):
        return "data"
    if line[len(delim):].startswith(b"--"):
        return "terminator"
    return "delimiter"


def _parse_part_headers(lines):
    headers = {}
    for line in lines:
        if b":" not in line:
            continue  # junk/continuation lines from odd clients
        name, _, value = line.partition(b":")
        headers[_decode(name).strip().lower()] = _decode(value).strip()
    return headers


def _parse_disposition(value):
    """Pull name= and filename= out of a Content-Disposition header value."""
    name = filename = None
    for param in value.split(";")[1:]:
        key, _, val = param.strip().partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        key = key.strip().lower()
        if key == "name":
            name = val
        elif key == "filename":
            filename = val
    return name, filename


def parse_form_data(body, content_type):
    """Parse a multipart/form-data request body.

    body: the raw bytes exactly as read from the client.
    content_type: the request's Content-Type header value (with boundary).
    Returns FormData; raises MultipartError when the envelope is unusable.
    """
    if not isinstance(body, (bytes, bytearray)):
        raise MultipartError("body must be bytes")
    delim = b"--" + _get_boundary(content_type)
    lines = bytes(body).split(b"\r\n")
    form = FormData()
    i, n = 0, len(lines)
    # Skip any preamble before the first delimiter line.
    while i < n and _classify_line(lines[i], delim) == "data":
        i += 1
    while i < n:
        if _classify_line(lines[i], delim) != "delimiter":
            break  # closing terminator (or a truncated body)
        i += 1
        # Part headers run until the first blank line.
        header_lines = []
        while i < n and lines[i] != b"":
            header_lines.append(lines[i])
            i += 1
        i += 1  # step past the blank separator
        headers = _parse_part_headers(header_lines)
        # Part payload runs until the next delimiter line.
        data_lines = []
        while i < n and _classify_line(lines[i], delim) == "data":
            data_lines.append(lines[i])
            i += 1
        data = b"\r\n".join(data_lines)
        name, filename = _parse_disposition(headers.get("content-disposition", ""))
        if name is None:
            continue  # nameless part: nothing to file it under
        if filename is not None:
            ctype = headers.get("content-type", "application/octet-stream")
            form.files[name] = UploadedFile(filename, ctype, data)
        else:
            form.fields[name] = _decode(data)
    return form
