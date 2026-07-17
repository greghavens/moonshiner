import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;
import java.util.function.Supplier;

/**
 * Plain authenticated transport for SDDC Manager: attaches whatever bearer
 * token the supplier hands out and decodes the VCF error envelope on non-2xx
 * answers. Knows nothing about token lifecycles.
 */
public final class SddcHttpClient implements VcfTransport {

    private final String baseUrl;
    private final HttpClient http;
    private final Supplier<String> tokenSource;

    public SddcHttpClient(String baseUrl, HttpClient http, Supplier<String> tokenSource) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.http = http;
        this.tokenSource = tokenSource;
    }

    @Override
    public Object get(String path) {
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .header("Authorization", "Bearer " + tokenSource.get())
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<String> response = send(request);
        if (response.statusCode() / 100 != 2) {
            throw decodeError(response);
        }
        return Json.parse(response.body());
    }

    private HttpResponse<String> send(HttpRequest request) {
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new RuntimeException("SDDC Manager request failed: " + e.getMessage(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("SDDC Manager request interrupted", e);
        }
    }

    static VcfApiException decodeError(HttpResponse<String> response) {
        String errorCode = null;
        String message = null;
        String referenceToken = null;
        try {
            Map<String, Object> envelope = Json.object(Json.parse(response.body()));
            errorCode = asString(envelope.get("errorCode"));
            message = asString(envelope.get("message"));
            referenceToken = asString(envelope.get("referenceToken"));
        } catch (RuntimeException ignored) {
            // non-JSON error body: report the status alone
        }
        if (response.statusCode() == 401) {
            return new VcfAuthException(401, errorCode, message, referenceToken);
        }
        return new VcfApiException(response.statusCode(), errorCode, message, referenceToken);
    }

    private static String asString(Object v) {
        return v == null ? null : String.valueOf(v);
    }
}
