package com.moonshiner.telemetry;

/** A stable, source-positioned diagnostic for malformed telemetry input. */
public final class TelemetryParseException extends IllegalArgumentException {
    private final int line;
    private final int column;
    private final String reason;

    public TelemetryParseException(int line, int column, String reason) {
        super("line " + line + ", column " + column + ": " + reason);
        this.line = line;
        this.column = column;
        this.reason = reason;
    }

    public int line() {
        return line;
    }

    public int column() {
        return column;
    }

    public String reason() {
        return reason;
    }
}
