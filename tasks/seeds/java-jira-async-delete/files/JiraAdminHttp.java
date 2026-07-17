import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * Thin HTTP layer for the Jira Cloud REST API: basic auth from an account
 * email plus API token, JSON accept header, and no automatic redirect
 * following — asynchronous Jira operations answer with redirect statuses
 * that callers must interpret themselves.
 */
final class JiraAdminHttp {
    private final HttpClient client;
    private final String baseUrl;
    private final String authHeader;

    JiraAdminHttp(String baseUrl, String email, String apiToken) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        byte[] raw = (email + ":" + apiToken).getBytes(StandardCharsets.UTF_8);
        this.authHeader = "Basic " + Base64.getEncoder().encodeToString(raw);
        this.client = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build();
    }

    String baseUrl() {
        return baseUrl;
    }

    /** Sends a request; {@code pathOrUrl} is a /-rooted API path or an absolute URL. */
    HttpResponse<String> send(String method, String pathOrUrl, String jsonBody) {
        String url = pathOrUrl.startsWith("http://") || pathOrUrl.startsWith("https://")
                ? pathOrUrl
                : baseUrl + pathOrUrl;
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url))
                .header("Authorization", authHeader)
                .header("Accept", "application/json");
        if (jsonBody == null) {
            builder.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            builder.header("Content-Type", "application/json")
                    .method(method, HttpRequest.BodyPublishers.ofString(jsonBody, StandardCharsets.UTF_8));
        }
        try {
            return client.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        } catch (Exception e) {
            throw new IllegalStateException("HTTP " + method + " " + url + " did not complete", e);
        }
    }
}
