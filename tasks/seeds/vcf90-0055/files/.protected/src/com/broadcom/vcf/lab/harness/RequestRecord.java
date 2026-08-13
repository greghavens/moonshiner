package com.broadcom.vcf.lab.harness;

import java.util.List;
import java.util.Map;

/**
 * One entry in {@link MockVcenter}'s request log: everything the mock saw on the wire for a single
 * HTTP exchange, plus the status it answered with.
 *
 * <p>Part of the protected harness: do not modify.
 */
public final class RequestRecord {

    /** 1-based position in the request log. */
    public final int seq;
    public final String method;
    /** Decoded request path, for example {@code /api/vcenter/vm}. */
    public final String path;
    /** Raw query string exactly as received, or {@code null} when the request carried none. */
    public final String rawQuery;
    /** Parsed query string; a repeated key keeps every value in arrival order. */
    public final Map<String, List<String>> query;
    /** Request headers with lower-cased names. */
    public final Map<String, String> headers;
    /** Request body decoded as UTF-8; the empty string when no body was sent. */
    public final String body;
    /** operationId this request routed to, or {@code null} when nothing in the contract matched. */
    public final String operationId;
    /** Status the mock answered with. */
    public final int status;

    RequestRecord(int seq, String method, String path, String rawQuery,
                  Map<String, List<String>> query, Map<String, String> headers,
                  String body, String operationId, int status) {
        this.seq = seq;
        this.method = method;
        this.path = path;
        this.rawQuery = rawQuery;
        this.query = query;
        this.headers = headers;
        this.body = body;
        this.operationId = operationId;
        this.status = status;
    }

    public String header(String name) {
        return headers.get(name.toLowerCase(java.util.Locale.ROOT));
    }

    public String target() {
        return rawQuery == null ? path : path + "?" + rawQuery;
    }

    @Override
    public String toString() {
        return String.format("#%d %s %s -> %d [%s]%s",
                seq, method, target(), status,
                operationId == null ? "no matching operation" : operationId,
                body.isEmpty() ? "" : " body=" + body);
    }
}
