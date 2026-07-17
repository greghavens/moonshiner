import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;

/**
 * Small Microsoft Graph v1.0 drive client used by the asset-sync jobs.
 * Metadata reads work today; large uploads are still done by hand.
 */
public final class GraphDriveClient {
    private final String baseUrl;
    private final String accessToken;
    private final HttpClient http;

    public GraphDriveClient(String baseUrl, String accessToken) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.accessToken = accessToken;
        this.http = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    public String baseUrl() {
        return baseUrl;
    }

    public String accessToken() {
        return accessToken;
    }

    public HttpClient http() {
        return http;
    }

    /** GET /drives/{driveId}/items/{itemId}?$select=id,name,size */
    public DriveItem getItem(String driveId, String itemId) {
        String url = baseUrl + "/drives/" + driveId + "/items/" + itemId + "?$select=id,name,size";
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .header("Authorization", "Bearer " + accessToken)
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<String> response = send(request);
        if (response.statusCode() != 200) {
            throw decodeError(response);
        }
        return DriveItem.fromJson(Json.object(Json.parse(response.body())));
    }

    public HttpResponse<String> send(HttpRequest request) {
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new GraphApiException(0, "TransportError", "request failed: " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new GraphApiException(0, "Interrupted", "request interrupted");
        }
    }

    public static GraphApiException decodeError(HttpResponse<String> response) {
        String code = null;
        String message = "Microsoft Graph request failed with status " + response.statusCode();
        try {
            Map<String, Object> root = Json.object(Json.parse(response.body()));
            Map<String, Object> error = Json.object(root.get("error"));
            if (error.get("code") instanceof String c) code = c;
            if (error.get("message") instanceof String m) message = m;
        } catch (RuntimeException ignored) {
            // keep the generic message for unparseable bodies
        }
        return new GraphApiException(response.statusCode(), code, message);
    }
}
