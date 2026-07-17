import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Minimal core/v1 pod client: bearer-authenticated GETs against one API
 * server. Redirects are never followed — our token must not leave the host
 * we were configured with — and non-2xx list responses decode the standard
 * Status body into KubeStatusException.
 */
public final class PodClient {

    private final String baseUrl;
    private final String token;
    private final HttpClient http = HttpClient.newBuilder()
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();

    public PodClient(String baseUrl, String token) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.token = token;
    }

    /** RFC 3986 percent-encoding for one path segment or query value. */
    static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    /**
     * Issue a streaming GET for pathAndQuery. Any 3xx is refused outright.
     * The caller owns the response body stream (and non-2xx handling).
     */
    public HttpResponse<InputStream> open(String pathAndQuery)
            throws IOException, InterruptedException {
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + pathAndQuery))
                .header("Authorization", "Bearer " + token)
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<InputStream> resp = http.send(req, HttpResponse.BodyHandlers.ofInputStream());
        if (resp.statusCode() >= 300 && resp.statusCode() < 400) {
            try (InputStream body = resp.body()) {
                body.readAllBytes();
            }
            throw new KubeStatusException(resp.statusCode(), "Redirect",
                    "refusing to follow a redirect issued by the API server");
        }
        return resp;
    }

    /** List pod names in a namespace matching a label selector. */
    public List<String> listPodNames(String namespace, String labelSelector)
            throws IOException, InterruptedException {
        String path = "/api/v1/namespaces/" + encode(namespace) + "/pods"
                + "?labelSelector=" + encode(labelSelector);
        HttpResponse<InputStream> resp = open(path);
        String body;
        try (InputStream in = resp.body()) {
            body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
        if (resp.statusCode() != 200) {
            throw KubeStatusException.fromStatusBody(resp.statusCode(), body);
        }
        List<String> names = new ArrayList<>();
        Map<String, Object> list = Json.asObject(Json.parse(body));
        for (Object item : Json.asArray(list.get("items"))) {
            Map<String, Object> metadata = Json.asObject(Json.asObject(item).get("metadata"));
            names.add((String) metadata.get("name"));
        }
        return names;
    }
}
