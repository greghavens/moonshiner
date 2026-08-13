import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Dependency-free, single-file client for the VCF Automation 9.1 REST surface.
 *
 * <p>The wire contract this client must honour is docs/contract.json, transcribed from the
 * Broadcom xAPIs reference pages recorded in docs/official_sources.json. VCF Automation
 * publishes no machine-readable specification, so the contract file is the authority here.
 *
 * <p>{@link #runChange} performs one multi-step change against a tenant org and returns a
 * report of what actually happened. The report is the deliverable: it is consumed by change
 * records and by on-call engineers who did not watch the run, so every step's outcome, and
 * every change that outlived a failure, has to be stated exactly as the server reported it.
 */
public final class VcfaChangeClient {

    public static final String STEP_AUTHENTICATE = "authenticate";
    public static final String STEP_CREATE_PROJECT = "createProject";
    public static final String STEP_REQUEST_CATALOG_ITEM = "requestCatalogItem";
    public static final String STEP_AWAIT_DEPLOYMENT = "awaitDeployment";
    public static final String STEP_SUBMIT_RESOURCE_ACTION = "submitResourceAction";
    public static final String STEP_AWAIT_RESOURCE_ACTION = "awaitResourceAction";

    public static final String SUCCEEDED = "SUCCEEDED";
    public static final String FAILED = "FAILED";

    private static final Duration POLL_INTERVAL = Duration.ofMillis(200);
    private static final int MAX_POLL_ATTEMPTS = 25;

    /** One element of ProjectSpecification.zoneAssignmentConfigurations; null limits are unset. */
    public record ZoneAssignment(String zoneId, Integer priority, Integer maxNumberInstances,
                                 Integer memoryLimitMB, Integer cpuLimit, Integer storageLimitGB) {
    }

    /**
     * Caller-supplied ProjectSpecification plus the two optional query parameters of
     * createProject. Every field except {@code name} is optional: a null value, an empty string,
     * an empty list and an empty map all mean "the caller did not set this".
     */
    public record ProjectSpec(String name, String description,
                              List<ZoneAssignment> zoneAssignmentConfigurations,
                              Long operationTimeout, String machineNamingTemplate,
                              Boolean sharedResources, String placementPolicy,
                              Map<String, String> customProperties,
                              String apiVersion, Boolean validatePrincipals) {
    }

    /**
     * Caller-supplied CatalogItemRequest. {@code projectId} is not a field here: the client
     * fills it in from the project it created earlier in the run.
     */
    public record CatalogRequestSpec(String catalogItemId, Integer bulkRequestCount,
                                     String deploymentName, Map<String, String> inputs,
                                     String reason, String version) {
    }

    /** Caller-supplied ResourceActionRequest for the day-2 step. */
    public record ActionSpec(String actionId, Map<String, String> inputs, String reason) {
    }

    /** Outcome of one attempted step. {@code status} is {@link #SUCCEEDED} or {@link #FAILED}. */
    public record StepOutcome(String name, String status, String detail) {
    }

    /** A change that exists on the appliance after the run, whatever the run's outcome. */
    public record PersistedChange(String kind, String id, String state) {
    }

    /**
     * What the run did. {@code outcome} is {@link #SUCCEEDED} only when every step succeeded;
     * {@code failedStep} names the first step that did not, or is null when none failed.
     * {@code steps} holds one entry per attempted step, in execution order.
     * {@code persistedChanges} lists the changes that remain on the appliance.
     * {@code failedRequestId} and {@code failureDetail} carry the day-2 request identifier and
     * the server's own explanation when a submitted request reaches a non-successful terminal
     * status; both are null otherwise.
     */
    public record ChangeReport(String outcome, String failedStep, List<StepOutcome> steps,
                               List<PersistedChange> persistedChanges,
                               String failedRequestId, String failureDetail) {
    }

    private final URI baseUri;
    private final String tenant;
    private final String refreshToken;
    private final HttpClient http;

    public VcfaChangeClient(URI baseUri, String tenant, String refreshToken) {
        this.baseUri = baseUri;
        this.tenant = tenant;
        this.refreshToken = refreshToken;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    /**
     * Runs the change: authenticate, create the project, request the catalog item into it, wait
     * for the deployment to reach a terminal state, submit the day-2 action and wait for that
     * request to reach a terminal state.
     *
     * <p>A step that the server rejects, or a submitted request that reaches a non-successful
     * terminal status, ends the run: later steps are not attempted. That is not an exception —
     * a rejected change is a normal result, so this method returns the report describing it and
     * only throws when the run could not be carried out at all (transport failure, malformed
     * response, or polling that never reached a terminal status).
     *
     * <p>The contract exposes no operation that undoes an earlier step, so nothing is rolled
     * back and nothing outside docs/contract.json may be called.
     */
    public ChangeReport runChange(ProjectSpec project, CatalogRequestSpec catalogRequest,
                                  ActionSpec action) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("runChange is not implemented");
    }

    /**
     * exchangeRefreshToken: POST /tm/oauth/tenant/{tenant}/token with a form-encoded
     * grant_type/refresh_token payload. Returns the access_token.
     */
    private String authenticate() throws IOException, InterruptedException {
        throw new UnsupportedOperationException("authenticate is not implemented");
    }

    /** createProject: POST /iaas/api/projects. Returns the created project id. */
    private String createProject(String accessToken, ProjectSpec spec)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("createProject is not implemented");
    }

    /**
     * requestCatalogItemInstances: POST /catalog/api/items/{id}/request into the created
     * project. Returns the deploymentId of the single requested instance.
     */
    private String requestCatalogItem(String accessToken, CatalogRequestSpec spec, String projectId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("requestCatalogItem is not implemented");
    }

    /** getDeploymentById: polls until the deployment reports a terminal status, which it returns. */
    private String awaitDeployment(String accessToken, String deploymentId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("awaitDeployment is not implemented");
    }

    /**
     * submitDeploymentActionRequest: POST /deployment/api/deployments/{deploymentId}/requests.
     * Returns the parsed Request object the server accepted.
     */
    private Map<String, Object> submitResourceAction(String accessToken, String deploymentId,
                                                     ActionSpec spec)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("submitResourceAction is not implemented");
    }

    /** getRequest: polls until the request reports a terminal status, and returns it parsed. */
    private Map<String, Object> awaitResourceAction(String accessToken, String requestId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("awaitResourceAction is not implemented");
    }

    // ---------------------------------------------------------------------------------------
    // Transport and JSON reading helpers. These are complete; request bodies and query strings
    // are the client's own job.
    // ---------------------------------------------------------------------------------------

    /** Sends a prepared request and returns the response with its body as text. */
    private HttpResponse<String> send(HttpRequest request) throws IOException, InterruptedException {
        return http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    /** Builds a request against {@code baseUri} for the given already-encoded path and query. */
    private HttpRequest.Builder requestTo(String pathAndQuery) {
        return HttpRequest.newBuilder(baseUri.resolve(pathAndQuery))
                .timeout(Duration.ofSeconds(10));
    }

    /** Percent-encodes one path segment. */
    private static String encodeSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8)
                .replace("+", "%20")
                .replace("*", "%2A");
    }

    /** Sleeps one poll interval. */
    private static void awaitNextPoll() throws InterruptedException {
        Thread.sleep(POLL_INTERVAL.toMillis());
    }

    /** Minimal recursive-descent reader for the JSON the appliance returns. */
    static final class Json {
        private final String text;
        private int at;

        private Json(String text) {
            this.text = text;
        }

        /** Parses one JSON document into Map, List, String, Double, Boolean or null. */
        static Object parse(String text) {
            Json reader = new Json(text);
            reader.skipWhitespace();
            Object value = reader.readValue();
            reader.skipWhitespace();
            if (reader.at != text.length()) {
                throw new IllegalArgumentException("trailing content in JSON response");
            }
            return value;
        }

        /** Reads {@code field} from a parsed object as a string, or null when absent. */
        @SuppressWarnings("unchecked")
        static String string(Object object, String field) {
            Object value = object instanceof Map ? ((Map<String, Object>) object).get(field) : null;
            return value instanceof String text ? text : null;
        }

        /** Escapes a Java string for inclusion in a JSON string literal. */
        static String escape(String value) {
            StringBuilder escaped = new StringBuilder(value.length() + 8);
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                switch (character) {
                    case '"' -> escaped.append("\\\"");
                    case '\\' -> escaped.append("\\\\");
                    case '\n' -> escaped.append("\\n");
                    case '\r' -> escaped.append("\\r");
                    case '\t' -> escaped.append("\\t");
                    default -> {
                        if (character < 0x20) {
                            escaped.append(String.format("\\u%04x", (int) character));
                        } else {
                            escaped.append(character);
                        }
                    }
                }
            }
            return escaped.toString();
        }

        private Object readValue() {
            char character = peek();
            return switch (character) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            Map<String, Object> object = new LinkedHashMap<>();
            expect('{');
            skipWhitespace();
            if (peek() == '}') {
                at++;
                return object;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                object.put(key, readValue());
                skipWhitespace();
                char next = text.charAt(at++);
                if (next == '}') {
                    return object;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("malformed JSON object");
                }
            }
        }

        private List<Object> readArray() {
            List<Object> array = new ArrayList<>();
            expect('[');
            skipWhitespace();
            if (peek() == ']') {
                at++;
                return array;
            }
            while (true) {
                skipWhitespace();
                array.add(readValue());
                skipWhitespace();
                char next = text.charAt(at++);
                if (next == ']') {
                    return array;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("malformed JSON array");
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder value = new StringBuilder();
            while (true) {
                char character = text.charAt(at++);
                if (character == '"') {
                    return value.toString();
                }
                if (character != '\\') {
                    value.append(character);
                    continue;
                }
                char escaped = text.charAt(at++);
                switch (escaped) {
                    case '"', '\\', '/' -> value.append(escaped);
                    case 'b' -> value.append('\b');
                    case 'f' -> value.append('\f');
                    case 'n' -> value.append('\n');
                    case 'r' -> value.append('\r');
                    case 't' -> value.append('\t');
                    case 'u' -> {
                        value.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                    }
                    default -> throw new IllegalArgumentException("bad JSON escape: " + escaped);
                }
            }
        }

        private Object readNumber() {
            int start = at;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            return Double.valueOf(text.substring(start, at));
        }

        private Object readLiteral(String literal, Object value) {
            if (!text.startsWith(literal, at)) {
                throw new IllegalArgumentException("malformed JSON literal at " + at);
            }
            at += literal.length();
            return value;
        }

        private char peek() {
            if (at >= text.length()) {
                throw new IllegalArgumentException("unexpected end of JSON");
            }
            return text.charAt(at);
        }

        private void expect(char expected) {
            if (peek() != expected) {
                throw new IllegalArgumentException("expected " + expected + " at offset " + at);
            }
            at++;
        }

        private void skipWhitespace() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }
    }
}
