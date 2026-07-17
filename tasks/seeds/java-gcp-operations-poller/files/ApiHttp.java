import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;

/**
 * Thin transport wrapper around java.net.http so clients can be pointed at a
 * local test server and credentials stay in one place. A null body sends no
 * body at all (and no Content-Type), which some Google endpoints require.
 */
final class ApiHttp {
    private final HttpClient client;

    ApiHttp(HttpClient client) {
        this.client = client;
    }

    record Response(int status, String body) {}

    Response get(String url, String token) {
        return send("GET", url, token, null);
    }

    Response post(String url, String token, String body) {
        return send("POST", url, token, body);
    }

    private Response send(String method, String url, String token, String body) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(url))
                .header("Authorization", "Bearer " + token);
        if (body == null) {
            builder.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            builder.header("Content-Type", "application/json; charset=utf-8");
            builder.method(method, HttpRequest.BodyPublishers.ofString(body));
        }
        try {
            HttpResponse<String> resp = client.send(builder.build(), HttpResponse.BodyHandlers.ofString());
            return new Response(resp.statusCode(), resp.body());
        } catch (IOException e) {
            throw new ApiException(0, "TRANSPORT", "transport failure: " + e.getMessage(), List.of());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ApiException(0, "TRANSPORT", "interrupted while waiting for the API", List.of());
        }
    }
}
