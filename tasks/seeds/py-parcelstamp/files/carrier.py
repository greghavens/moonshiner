"""TLS client configuration for the carrier's label-purchase API.

Only builds the context; the depot gateway owns the actual connection.
"""

import ssl


def client_context():
    """A client-side TLS context per the carrier's integration guide:
    certificate and hostname always verified, TLS 1.2 or newer only."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS)
    ctx.options |= ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx
