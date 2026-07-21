package com.moonshiner.lease;

/** Raised when a worker attempts to renew an expired or superseded lease. */
public final class LeaseLostException extends IllegalStateException {
    private static final long serialVersionUID = 1L;

    public LeaseLostException(String message) {
        super(message);
    }
}
