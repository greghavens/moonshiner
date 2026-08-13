package com.vmware.vcf.opsnet;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Client for VMware Cloud Foundation Operations for Networks 9.1.
 *
 * <p>The wire contract this client implements is pinned in {@code docs/contract.json},
 * which is derived from the product OpenAPI specification recorded in
 * {@code docs/official_sources.json}. The contract is the authority: when this file
 * and the contract disagree, the contract wins.
 *
 * <p>This class is intentionally a single self-contained file with no dependencies
 * outside the JDK. Keep it that way.
 */
public final class VcfOpsNetworksClient {

    /** Server base path declared by the specification (`servers: [{url: /api/ni}]`). */
    public static final String BASE_PATH = "/api/ni";

    private final String baseUrl;
    private final HttpClient http;

    private long pollIntervalMillis = 100L;
    private long pollTimeoutMillis = 20_000L;

    /** Auth token from the most recent successful {@link #authenticate}. */
    private String token;

    /**
     * @param baseUrl origin of the VCF Operations for Networks platform, e.g.
     *                {@code http://127.0.0.1:8080} - with no trailing slash and no path.
     */
    public VcfOpsNetworksClient(String baseUrl) {
        if (baseUrl == null || baseUrl.isBlank()) {
            throw new IllegalArgumentException("baseUrl is required");
        }
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    public VcfOpsNetworksClient withPollIntervalMillis(long millis) {
        this.pollIntervalMillis = millis;
        return this;
    }

    public VcfOpsNetworksClient withPollTimeoutMillis(long millis) {
        this.pollTimeoutMillis = millis;
        return this;
    }

    /** The token held by this client, or {@code null} before authenticating. */
    public String token() {
        return token;
    }

    // ---------------------------------------------------------------------
    // Model
    // ---------------------------------------------------------------------

    /**
     * One field mutation inside a bulk data source entity
     * (spec schema {@code BulkDataSourceEntityField}).
     *
     * @param actionOnField one of {@code OVERRIDE_TAGS}, {@code ADD_TAGS},
     *                      {@code REMOVE_TAGS}, or {@code null} when the caller did
     *                      not set it.
     */
    public record FieldUpdate(String key, String value, String actionOnField) {
        /** A field mutation with no {@code action_on_field}. */
        public static FieldUpdate of(String key, String value) {
            return new FieldUpdate(key, value, null);
        }
    }

    /** One data source in a bulk request (spec schema {@code BulkDataSourceEntity}). */
    public record DataSourceEntity(String entityId, List<FieldUpdate> fields) {}

    /** A data source the bulk operation could not process (spec schema {@code DataSourceDetails}). */
    public record FailedDataSource(String entityId, String reason) {}

    /** A bulk operation progress report (spec schema {@code BulkOperationReportResponse}). */
    public record BulkOperationReport(
            int totalCount,
            int successCount,
            int failedCount,
            List<String> successfulDataSources,
            List<FailedDataSource> failedDataSources) {}

    /** Raised for transport failures and for any non-success response from the platform. */
    public static final class VcfOpsNetworksException extends RuntimeException {
        private final int statusCode;

        public VcfOpsNetworksException(String message) {
            this(message, -1);
        }

        public VcfOpsNetworksException(String message, int statusCode) {
            super(message);
            this.statusCode = statusCode;
        }

        public VcfOpsNetworksException(String message, Throwable cause) {
            super(message, cause);
            this.statusCode = -1;
        }

        /** HTTP status that produced this failure, or {@code -1} for transport failures. */
        public int statusCode() {
            return statusCode;
        }
    }

    // ---------------------------------------------------------------------
    // Operations
    // ---------------------------------------------------------------------

    /**
     * operationId {@code create} - POST /api/ni/auth/token.
     *
     * <p>Exchanges credentials for an auth token and remembers the token for
     * subsequent calls on this client.
     *
     * @param username    user name, always sent
     * @param password    password, always sent
     * @param domainType  {@code LDAP} or {@code LOCAL}, or {@code null} when unset
     * @param domainValue domain value, or {@code null} when unset
     * @return the token string returned by the platform
     */
    public String authenticate(String username, String password, String domainType, String domainValue) {
        // TODO: implement operationId `create` per docs/contract.json.
        throw new UnsupportedOperationException("authenticate is not implemented");
    }

    /**
     * operationId {@code bulkDataSourceOperation} - POST /api/ni/data-sources/bulk.
     *
     * <p>Submits the bulk request. The platform answers 202 Submitted with a request id;
     * the operation itself is still running when this method returns.
     *
     * @return the {@code request_id} to poll with {@link #awaitBulkOperation}
     */
    public String submitBulkOperation(String actionType, List<DataSourceEntity> dataSources) {
        // TODO: implement operationId `bulkDataSourceOperation` per docs/contract.json.
        throw new UnsupportedOperationException("submitBulkOperation is not implemented");
    }

    /**
     * operationId {@code getBulkOperationDetails} -
     * GET /api/ni/data-sources/bulk/view-details/{request_id}.
     *
     * <p>Polls the bulk operation until it reaches the terminal state defined by the
     * contract, then returns the terminal report.
     *
     * @throws VcfOpsNetworksException if the operation does not reach a terminal state
     *                                 within the configured poll timeout
     */
    public BulkOperationReport awaitBulkOperation(String requestId) {
        // TODO: implement operationId `getBulkOperationDetails` and the polling loop
        //       per docs/contract.json -> async_workflow.
        throw new UnsupportedOperationException("awaitBulkOperation is not implemented");
    }

    // ---------------------------------------------------------------------
    // Transport helper
    // ---------------------------------------------------------------------

    /**
     * Sends a request and returns the response. Provided so every operation shares one
     * transport path; the header set, body and URL are the caller's responsibility.
     */
    private HttpResponse<String> send(HttpRequest request) {
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new VcfOpsNetworksException("request to " + request.uri() + " failed", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new VcfOpsNetworksException("interrupted while calling " + request.uri(), e);
        }
    }

    // ---------------------------------------------------------------------
    // Minimal JSON reader - provided, no need to change.
    //
    // Json.parse maps a JSON document onto Map<String,Object> / List<Object> /
    // String / Double / Boolean / null. There is deliberately no JSON *writer*
    // here: producing request bodies that satisfy the contract is this client's job.
    // ---------------------------------------------------------------------

    static final class Json {

        static Object parse(String text) {
            Parser p = new Parser(text);
            p.skipWs();
            Object value = p.readValue();
            p.skipWs();
            if (p.pos != text.length()) {
                throw new IllegalArgumentException("trailing content at offset " + p.pos);
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> parseObject(String text) {
            Object value = parse(text);
            if (!(value instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object, got " + describe(value));
            }
            return (Map<String, Object>) value;
        }

        static String str(Map<String, Object> obj, String key) {
            Object value = obj.get(key);
            return value == null ? null : String.valueOf(value);
        }

        static int intAt(Map<String, Object> obj, String key) {
            Object value = obj.get(key);
            if (value == null) {
                return 0;
            }
            if (value instanceof Number n) {
                return n.intValue();
            }
            throw new IllegalArgumentException("field '" + key + "' is not a number");
        }

        @SuppressWarnings("unchecked")
        static List<Object> list(Map<String, Object> obj, String key) {
            Object value = obj.get(key);
            if (value == null) {
                return List.of();
            }
            if (!(value instanceof List)) {
                throw new IllegalArgumentException("field '" + key + "' is not an array");
            }
            return (List<Object>) value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> asObject(Object value) {
            if (!(value instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object, got " + describe(value));
            }
            return (Map<String, Object>) value;
        }

        private static String describe(Object value) {
            return value == null ? "null" : value.getClass().getSimpleName();
        }

        private static final class Parser {
            private final String s;
            private int pos;

            Parser(String s) {
                this.s = s;
            }

            void skipWs() {
                while (pos < s.length() && Character.isWhitespace(s.charAt(pos))) {
                    pos++;
                }
            }

            Object readValue() {
                skipWs();
                if (pos >= s.length()) {
                    throw new IllegalArgumentException("unexpected end of input");
                }
                char c = s.charAt(pos);
                switch (c) {
                    case '{':
                        return readObject();
                    case '[':
                        return readArray();
                    case '"':
                        return readString();
                    case 't':
                        expect("true");
                        return Boolean.TRUE;
                    case 'f':
                        expect("false");
                        return Boolean.FALSE;
                    case 'n':
                        expect("null");
                        return null;
                    default:
                        return readNumber();
                }
            }

            private Map<String, Object> readObject() {
                Map<String, Object> out = new LinkedHashMap<>();
                pos++; // '{'
                skipWs();
                if (pos < s.length() && s.charAt(pos) == '}') {
                    pos++;
                    return out;
                }
                while (true) {
                    skipWs();
                    String key = readString();
                    skipWs();
                    require(':');
                    out.put(key, readValue());
                    skipWs();
                    char c = next();
                    if (c == '}') {
                        return out;
                    }
                    if (c != ',') {
                        throw new IllegalArgumentException("expected ',' or '}' at offset " + (pos - 1));
                    }
                }
            }

            private List<Object> readArray() {
                List<Object> out = new ArrayList<>();
                pos++; // '['
                skipWs();
                if (pos < s.length() && s.charAt(pos) == ']') {
                    pos++;
                    return out;
                }
                while (true) {
                    out.add(readValue());
                    skipWs();
                    char c = next();
                    if (c == ']') {
                        return out;
                    }
                    if (c != ',') {
                        throw new IllegalArgumentException("expected ',' or ']' at offset " + (pos - 1));
                    }
                }
            }

            private String readString() {
                require('"');
                StringBuilder sb = new StringBuilder();
                while (true) {
                    char c = next();
                    if (c == '"') {
                        return sb.toString();
                    }
                    if (c != '\\') {
                        sb.append(c);
                        continue;
                    }
                    char esc = next();
                    switch (esc) {
                        case '"' -> sb.append('"');
                        case '\\' -> sb.append('\\');
                        case '/' -> sb.append('/');
                        case 'b' -> sb.append('\b');
                        case 'f' -> sb.append('\f');
                        case 'n' -> sb.append('\n');
                        case 'r' -> sb.append('\r');
                        case 't' -> sb.append('\t');
                        case 'u' -> {
                            sb.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                            pos += 4;
                        }
                        default -> throw new IllegalArgumentException("bad escape \\" + esc);
                    }
                }
            }

            private Double readNumber() {
                int start = pos;
                while (pos < s.length() && "+-.eE0123456789".indexOf(s.charAt(pos)) >= 0) {
                    pos++;
                }
                if (start == pos) {
                    throw new IllegalArgumentException("unexpected character at offset " + pos);
                }
                return Double.valueOf(s.substring(start, pos));
            }

            private void expect(String literal) {
                if (!s.startsWith(literal, pos)) {
                    throw new IllegalArgumentException("expected '" + literal + "' at offset " + pos);
                }
                pos += literal.length();
            }

            private void require(char c) {
                if (next() != c) {
                    throw new IllegalArgumentException("expected '" + c + "' at offset " + (pos - 1));
                }
            }

            private char next() {
                if (pos >= s.length()) {
                    throw new IllegalArgumentException("unexpected end of input");
                }
                return s.charAt(pos++);
            }
        }
    }
}
