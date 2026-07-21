package com.moonshiner.lease;

/** Raised by a fenced resource when an older lease epoch attempts a write. */
public final class StaleFencingTokenException extends IllegalStateException {
    private static final long serialVersionUID = 1L;

    public StaleFencingTokenException(long attemptedToken, long currentToken) {
        super("fencing token " + attemptedToken + " is older than " + currentToken);
    }
}
