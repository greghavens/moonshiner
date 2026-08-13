package com.vmware.vcfops.networks.test;

import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/**
 * One HTTP request as observed by {@link MockNiServer}, captured before any interpretation.
 *
 * <p>{@code rawQuery} is the query string exactly as it arrived on the wire (null when the
 * request-target carried no '?'), which is what lets the verifier distinguish an omitted
 * parameter from one sent with an empty value.
 *
 * <p>DO NOT MODIFY.
 */
public record RecordedRequest(
        int sequence,
        String method,
        String path,
        String rawQuery,
        String authorizationHeader,
        String contentTypeHeader,
        String body,
        int responseStatus,
        String operationId) {

    /**
     * Parses {@link #rawQuery} into ordered key -> value pairs without collapsing empty values.
     * A parameter written as {@code cursor=} yields an entry mapped to the empty string; a
     * parameter written as {@code cursor} (no '=') yields an entry mapped to null.
     */
    public Map<String, String> queryParameters() {
        Map<String, String> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            if (eq < 0) {
                out.put(urlDecode(pair), null);
            } else {
                out.put(urlDecode(pair.substring(0, eq)), urlDecode(pair.substring(eq + 1)));
            }
        }
        return out;
    }

    /** The set of query parameter names present on the wire, in arrival order. */
    public Set<String> queryParameterNames() {
        return new LinkedHashSet<>(queryParameters().keySet());
    }

    private static String urlDecode(String s) {
        return java.net.URLDecoder.decode(s, java.nio.charset.StandardCharsets.UTF_8);
    }

    /** A short human-readable form used in assertion failure messages. */
    public String describe() {
        return "#" + sequence + " " + method + " " + path
                + (rawQuery == null ? "" : "?" + rawQuery)
                + " -> " + responseStatus;
    }

    /** The bearer value carried in the Authorization header, or null if absent/not our scheme. */
    public String presentedToken() {
        if (authorizationHeader == null) {
            return null;
        }
        String prefix = "NetworkInsight ";
        return authorizationHeader.startsWith(prefix)
                ? authorizationHeader.substring(prefix.length())
                : null;
    }
}
