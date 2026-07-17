import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;

/**
 * Thin authenticated transport for the Vault HTTP API. Every request goes to
 * {baseUrl}/v1/{apiPath} with the X-Vault-Token header, JSON bodies, and —
 * when a namespace is configured — the X-Vault-Namespace header. Non-2xx
 * responses become VaultApiException with the decoded errors array.
 */
public final class VaultClient {

    private final HttpClient http = HttpClient.newHttpClient();
    private final String baseUrl;
    private final String token;
    private final String namespace;

    public VaultClient(String baseUrl, String token, String namespace) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.token = token;
        this.namespace = namespace;
    }

    /**
     * POST /v1/{apiPath}. The apiPath is used verbatim, so any segment that
     * needs percent-escaping must arrive already escaped.
     */
    public Map<String, Object> post(String apiPath, Map<String, Object> body)
            throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + "/v1/" + apiPath))
                .header("X-Vault-Token", token)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(Json.write(body)));
        if (namespace != null && !namespace.isEmpty()) {
            builder.header("X-Vault-Namespace", namespace);
        }
        HttpResponse<String> resp = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        int status = resp.statusCode();
        if (status == 204) return Map.of();
        if (status >= 200 && status < 300) return Json.parseObject(resp.body());
        throw VaultApiException.fromResponse(status, resp.body());
    }
}
