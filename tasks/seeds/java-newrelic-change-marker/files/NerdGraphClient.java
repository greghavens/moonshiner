import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Thin NerdGraph transport: POSTs one GraphQL document plus variables as
 * {"query": ..., "variables": ...} to the configured endpoint with the
 * user API key in the documented API-Key header, and hands back the parsed
 * GraphQL envelope (data / errors). The key must never leak into URLs,
 * request bodies, or exception text.
 */
public final class NerdGraphClient {

    /** Non-2xx NerdGraph response. Carries status and raw body, never the key. */
    public static final class NerdGraphHttpException extends RuntimeException {
        private final int status;
        private final String body;

        public NerdGraphHttpException(int status, String body) {
            super("NerdGraph request failed with HTTP " + status);
            this.status = status;
            this.body = body;
        }

        public int status() {
            return status;
        }

        public String body() {
            return body;
        }
    }

    private final String endpointUrl;
    private final String apiKey;
    private final HttpClient http;

    public NerdGraphClient(String endpointUrl, String apiKey, HttpClient http) {
        this.endpointUrl = endpointUrl;
        this.apiKey = apiKey;
        this.http = http;
    }

    public String endpointUrl() {
        return endpointUrl;
    }

    /** Serializes the request body for the given document and variables. */
    public static String requestBody(String query, Map<String, Object> variables) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("query", query);
        body.put("variables", variables);
        return Json.write(body);
    }

    /**
     * Executes one GraphQL request and returns the parsed envelope, a map
     * with "data" and (when present) "errors" keys.
     */
    public Map<String, Object> execute(String query, Map<String, Object> variables)
            throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(endpointUrl))
            .header("API-Key", apiKey)
            .header("Content-Type", "application/json")
            .POST(HttpRequest.BodyPublishers.ofString(requestBody(query, variables)))
            .build();
        HttpResponse<String> response =
            http.send(request, HttpResponse.BodyHandlers.ofString());
        int status = response.statusCode();
        if (status < 200 || status >= 300) {
            throw new NerdGraphHttpException(status, response.body());
        }
        return Json.parseObject(response.body());
    }
}
