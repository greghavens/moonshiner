import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Base64;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** A small, dependency-free client for the pinned VCF Automation contract. */
public final class AutomationClient {
    private static final Pattern JSON_STRING = Pattern.compile(
            "\\\"([^\\\"]+)\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\"\\\\])*)\\\"");

    private final HttpClient http;
    private final URI baseUri;
    private final String clientId;
    private final String clientSecret;
    private final Duration pollInterval;
    private String accessToken;
    private String refreshToken;

    public AutomationClient(
            URI baseUri,
            String accessToken,
            String refreshToken,
            String clientId,
            String clientSecret,
            Duration pollInterval) {
        this(HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build(),
                baseUri, accessToken, refreshToken, clientId, clientSecret, pollInterval);
    }

    AutomationClient(
            HttpClient http,
            URI baseUri,
            String accessToken,
            String refreshToken,
            String clientId,
            String clientSecret,
            Duration pollInterval) {
        this.http = Objects.requireNonNull(http, "http");
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = requireText(accessToken, "accessToken");
        this.refreshToken = requireText(refreshToken, "refreshToken");
        this.clientId = requireText(clientId, "clientId");
        this.clientSecret = Objects.requireNonNull(clientSecret, "clientSecret");
        this.pollInterval = Objects.requireNonNull(pollInterval, "pollInterval");
        if (pollInterval.isNegative()) {
            throw new IllegalArgumentException("pollInterval must not be negative");
        }
    }

    /** Submit one deployment action and wait for its request to reach a terminal state. */
    public OperationResult runDeploymentAction(String deploymentId, String actionId, String reason)
            throws IOException, InterruptedException {
        requireText(deploymentId, "deploymentId");
        requireText(actionId, "actionId");
        Objects.requireNonNull(reason, "reason");

        String submitPath = "/deployment/api/deployments/" + pathSegment(deploymentId) + "/requests";
        String body = "{\"actionId\":\"" + jsonEscape(actionId)
                + "\",\"inputs\":{},\"reason\":\"" + jsonEscape(reason) + "\"}";

        HttpResponse<String> submitted = sendAuthorized("POST", submitPath, "application/json", body);
        requireSuccess(submitted, "submit deployment action");
        String requestId = requiredJsonString(submitted.body(), "id");
        String status = requiredJsonString(submitted.body(), "status");

        while (!isTerminal(status)) {
            if (!pollInterval.isZero()) {
                Thread.sleep(pollInterval.toMillis());
            }
            HttpResponse<String> polled = sendAuthorized(
                    "GET", "/deployment/api/requests/" + pathSegment(requestId), null, null);
            requireSuccess(polled, "poll deployment request " + requestId);

            String responseRequestId = requiredJsonString(polled.body(), "id");
            if (!requestId.equals(responseRequestId)) {
                throw new IOException("poll response changed request id from "
                        + requestId + " to " + responseRequestId);
            }
            status = requiredJsonString(polled.body(), "status");
        }
        return new OperationResult(requestId, status);
    }

    private HttpResponse<String> sendAuthorized(
            String method, String path, String contentType, String body)
            throws IOException, InterruptedException {
        HttpResponse<String> response = send(method, path, contentType, body, "Bearer " + accessToken);
        if (response.statusCode() == 401) {
            refreshAccessToken();
            // BUG: the interrupted operation is still returned as unauthorized instead of
            // being retried with the refreshed bearer token.
        }
        return response;
    }

    private HttpResponse<String> send(
            String method, String path, String contentType, String body, String authorization)
            throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(baseUri.resolve(path))
                .timeout(Duration.ofSeconds(5))
                .header("Accept", "application/json")
                .header("Authorization", authorization);
        if (body == null) {
            builder.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            builder.header("Content-Type", contentType)
                    .method(method, HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8));
        }
        return http.send(builder.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    private void refreshAccessToken() throws IOException, InterruptedException {
        String form = "grant_type=refresh_token&refresh_token=" + formEncode(refreshToken);
        String basic = Base64.getEncoder().encodeToString(
                (clientId + ":" + clientSecret).getBytes(StandardCharsets.UTF_8));
        HttpResponse<String> response = send(
                "POST",
                "/csp/gateway/am/api/auth/token",
                "application/x-www-form-urlencoded",
                form,
                "Basic " + basic);
        requireSuccess(response, "refresh access token");
        accessToken = requiredJsonString(response.body(), "access_token");
        String rotated = optionalJsonString(response.body(), "refresh_token");
        if (rotated != null && !rotated.isEmpty()) {
            refreshToken = rotated;
        }
    }

    private static boolean isTerminal(String status) {
        return "SUCCESSFUL".equals(status)
                || "FAILED".equals(status)
                || "ABORTED".equals(status)
                || "APPROVAL_REJECTED".equals(status)
                || "COMPLETION".equals(status); // BUG: COMPLETION is not terminal.
    }

    private static void requireSuccess(HttpResponse<String> response, String operation) throws IOException {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IOException(operation + " failed with HTTP " + response.statusCode()
                    + ": " + response.body());
        }
    }

    private static String requiredJsonString(String json, String name) throws IOException {
        String value = optionalJsonString(json, name);
        if (value == null || value.isEmpty()) {
            throw new IOException("response is missing required JSON string field: " + name);
        }
        return value;
    }

    private static String optionalJsonString(String json, String name) throws IOException {
        Matcher matcher = JSON_STRING.matcher(json);
        while (matcher.find()) {
            if (name.equals(unescapeJson(matcher.group(1)))) {
                return unescapeJson(matcher.group(2));
            }
        }
        return null;
    }

    private static String unescapeJson(String value) throws IOException {
        StringBuilder result = new StringBuilder(value.length());
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (ch != '\\') {
                result.append(ch);
                continue;
            }
            if (++i >= value.length()) {
                throw new IOException("invalid JSON string escape");
            }
            char escaped = value.charAt(i);
            switch (escaped) {
                case '\"': result.append('\"'); break;
                case '\\': result.append('\\'); break;
                case '/': result.append('/'); break;
                case 'b': result.append('\b'); break;
                case 'f': result.append('\f'); break;
                case 'n': result.append('\n'); break;
                case 'r': result.append('\r'); break;
                case 't': result.append('\t'); break;
                case 'u':
                    if (i + 4 >= value.length()) {
                        throw new IOException("short JSON unicode escape");
                    }
                    try {
                        result.append((char) Integer.parseInt(value.substring(i + 1, i + 5), 16));
                    } catch (NumberFormatException e) {
                        throw new IOException("invalid JSON unicode escape", e);
                    }
                    i += 4;
                    break;
                default: throw new IOException("invalid JSON escape: \\" + escaped);
            }
        }
        return result.toString();
    }

    private static String jsonEscape(String value) {
        StringBuilder result = new StringBuilder(value.length() + 16);
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            switch (ch) {
                case '\"': result.append("\\\""); break;
                case '\\': result.append("\\\\"); break;
                case '\b': result.append("\\b"); break;
                case '\f': result.append("\\f"); break;
                case '\n': result.append("\\n"); break;
                case '\r': result.append("\\r"); break;
                case '\t': result.append("\\t"); break;
                default:
                    if (ch < 0x20) {
                        result.append(String.format("\\u%04x", (int) ch));
                    } else {
                        result.append(ch);
                    }
            }
        }
        return result.toString();
    }

    private static String pathSegment(String value) {
        return formEncode(value).replace("+", "%20");
    }

    private static String formEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String requireText(String value, String name) {
        Objects.requireNonNull(value, name);
        if (value.isEmpty()) {
            throw new IllegalArgumentException(name + " must not be empty");
        }
        return value;
    }

    public static final class OperationResult {
        private final String requestId;
        private final String status;

        public OperationResult(String requestId, String status) {
            this.requestId = requireText(requestId, "requestId");
            this.status = requireText(status, "status");
        }

        public String requestId() {
            return requestId;
        }

        public String status() {
            return status;
        }

        @Override
        public String toString() {
            return "OperationResult{requestId='" + requestId + "', status='" + status + "'}";
        }
    }
}
