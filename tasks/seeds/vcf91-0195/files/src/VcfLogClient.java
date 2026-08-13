import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;

/**
 * Minimal dependency-free client for the agent-secret operations selected from
 * the VCF Operations 9.1 Log Management OpenAPI specification.
 */
public final class VcfLogClient implements AutoCloseable {
    private static final long MIN_TTL_MILLIS = 60_000L;
    private static final long MAX_TTL_MILLIS = 15_552_000_000L;

    public record AgentSession(String accessToken, String name, String newSecret, long ttl) {}

    public record RotationResult(String retiredSecretName, String activeSecretName,
                                 AgentSession validatedSession) {}

    private static final class Credential {
        final String name;
        final String secret;

        Credential(String name, String secret) {
            this.name = name;
            this.secret = secret;
        }
    }

    private record CreatedSecret(String name, String secret) {}

    private final URI baseUri;
    private final String opsToken;
    private final HttpClient http;
    private final Object stateLock = new Object();
    private final Object rotationLock = new Object();
    private Credential active;
    private boolean closed;

    public VcfLogClient(URI baseUri, String opsToken, String initialSecretName,
                        String initialSecret) {
        this(baseUri, opsToken, initialSecretName, initialSecret,
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofSeconds(5))
                        .build());
    }

    /* Package-private transport injection keeps the deterministic harness offline. */
    VcfLogClient(URI baseUri, String opsToken, String initialSecretName,
                 String initialSecret, HttpClient http) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.opsToken = requireText(opsToken, "opsToken");
        this.active = new Credential(requireText(initialSecretName, "initialSecretName"),
                requireText(initialSecret, "initialSecret"));
        this.http = Objects.requireNonNull(http, "http");
    }

    /** Opens a session with the credential that is active when this method is called. */
    public CompletableFuture<AgentSession> openAgentSession(Long ttlMillis) {
        validateTtl(ttlMillis);
        Credential captured;
        synchronized (stateLock) {
            ensureOpen();
            captured = active;
        }
        return exchange(captured.secret, ttlMillis);
    }

    /**
     * Creates and validates a replacement, publishes it, and retires the old secret.
     * Rotation calls are serialized, while ordinary session calls remain concurrent.
     */
    public RotationResult rotateAgentSecret(String newSecretName, Long ttlMillis)
            throws IOException, InterruptedException {
        requireText(newSecretName, "newSecretName");
        validateTtl(ttlMillis);
        synchronized (rotationLock) {
            Credential retired;
            synchronized (stateLock) {
                ensureOpen();
                retired = active;
            }

            CreatedSecret created = createSecret(newSecretName);
            AgentSession validated = exchange(created.secret, ttlMillis).join();
            synchronized (stateLock) {
                ensureOpen();
                active = new Credential(created.name, created.secret);
            }

            // BUG: requests that captured retired may still be exchanging it here.
            revokeSecret(retired.name);
            return new RotationResult(retired.name, created.name, validated);
        }
    }

    /** Visible for operational diagnostics and the concurrency harness. */
    public String currentSecretName() {
        synchronized (stateLock) {
            return active.name;
        }
    }

    @Override
    public void close() {
        synchronized (stateLock) {
            closed = true;
        }
    }

    private CreatedSecret createSecret(String name) throws IOException, InterruptedException {
        String body = "{\"name\":\"" + jsonEscape(name) + "\"}";
        HttpResponse<String> response = send(bodyRequest("/api/v2/agent/secrets", body));
        expectStatus(response, 201, "createAgentSecret");
        return new CreatedSecret(readJsonString(response.body(), "name"),
                readJsonString(response.body(), "secret"));
    }

    private CompletableFuture<AgentSession> exchange(String secret, Long ttlMillis) {
        StringBuilder body = new StringBuilder("{\"secret\":\"")
                .append(jsonEscape(secret)).append('"');
        if (ttlMillis != null) {
            body.append(",\"ttl\":").append(ttlMillis);
        }
        body.append('}');

        HttpRequest request = bodyRequest("/api/v2/agent/secrets/exchange", body.toString());
        return http.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
                .thenApply(response -> {
                    if (response.statusCode() != 200) {
                        throw new CompletionException(new IOException(
                                "createAgentSession returned HTTP " + response.statusCode()));
                    }
                    return new AgentSession(
                            readJsonString(response.body(), "access_token"),
                            readJsonString(response.body(), "name"),
                            readJsonString(response.body(), "new_secret"),
                            readJsonLong(response.body(), "ttl"));
                });
    }

    private void revokeSecret(String secretName) throws IOException, InterruptedException {
        String path = "/api/v2/agent/secrets/" + encodePathSegment(secretName) + "/revoke";
        HttpRequest request = HttpRequest.newBuilder(resolve(path))
                .timeout(Duration.ofSeconds(10))
                .header("X-JWT-Token", opsToken)
                .POST(HttpRequest.BodyPublishers.noBody())
                .build();
        expectStatus(send(request), 200, "revokeAgentSecret");
    }

    private HttpRequest bodyRequest(String path, String body) {
        return HttpRequest.newBuilder(resolve(path))
                .timeout(Duration.ofSeconds(10))
                .header("X-JWT-Token", opsToken)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build();
    }

    private HttpResponse<String> send(HttpRequest request)
            throws IOException, InterruptedException {
        return http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    private URI resolve(String absolutePath) {
        return baseUri.resolve(absolutePath);
    }

    private static void expectStatus(HttpResponse<String> response, int expected,
                                     String operationId) throws IOException {
        if (response.statusCode() != expected) {
            throw new IOException(operationId + " returned HTTP " + response.statusCode());
        }
    }

    private void ensureOpen() {
        if (closed) {
            throw new IllegalStateException("client is closed");
        }
    }

    private static void validateTtl(Long ttlMillis) {
        if (ttlMillis != null && ttlMillis != 0
                && (ttlMillis < MIN_TTL_MILLIS || ttlMillis > MAX_TTL_MILLIS)) {
            throw new IllegalArgumentException("ttlMillis is outside the documented range");
        }
    }

    private static String requireText(String value, String name) {
        Objects.requireNonNull(value, name);
        if (value.isEmpty()) {
            throw new IllegalArgumentException(name + " must not be empty");
        }
        return value;
    }

    private static String encodePathSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static String jsonEscape(String value) {
        StringBuilder out = new StringBuilder(value.length() + 8);
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.toString();
    }

    private static String readJsonString(String json, String field) {
        int valueStart = valueStart(json, field);
        if (valueStart >= json.length() || json.charAt(valueStart) != '"') {
            throw new IllegalArgumentException("response field is not a string: " + field);
        }
        StringBuilder value = new StringBuilder();
        for (int i = valueStart + 1; i < json.length(); i++) {
            char c = json.charAt(i);
            if (c == '"') {
                return value.toString();
            }
            if (c != '\\') {
                value.append(c);
                continue;
            }
            if (++i >= json.length()) {
                break;
            }
            char escaped = json.charAt(i);
            switch (escaped) {
                case '"', '\\', '/' -> value.append(escaped);
                case 'b' -> value.append('\b');
                case 'f' -> value.append('\f');
                case 'n' -> value.append('\n');
                case 'r' -> value.append('\r');
                case 't' -> value.append('\t');
                case 'u' -> {
                    if (i + 4 >= json.length()) {
                        throw new IllegalArgumentException("bad unicode escape in response");
                    }
                    value.append((char) Integer.parseInt(json.substring(i + 1, i + 5), 16));
                    i += 4;
                }
                default -> throw new IllegalArgumentException("bad JSON escape in response");
            }
        }
        throw new IllegalArgumentException("unterminated response field: " + field);
    }

    private static long readJsonLong(String json, String field) {
        int start = valueStart(json, field);
        int end = start;
        if (end < json.length() && json.charAt(end) == '-') {
            end++;
        }
        while (end < json.length() && Character.isDigit(json.charAt(end))) {
            end++;
        }
        if (end == start) {
            throw new IllegalArgumentException("response field is not an integer: " + field);
        }
        return Long.parseLong(json.substring(start, end));
    }

    private static int valueStart(String json, String field) {
        String needle = "\"" + field + "\"";
        int key = json.indexOf(needle);
        if (key < 0) {
            throw new IllegalArgumentException("missing response field: " + field);
        }
        int colon = json.indexOf(':', key + needle.length());
        if (colon < 0) {
            throw new IllegalArgumentException("malformed response field: " + field);
        }
        int start = colon + 1;
        while (start < json.length() && Character.isWhitespace(json.charAt(start))) {
            start++;
        }
        return start;
    }
}
