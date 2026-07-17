import java.io.IOException;
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
 * Read side of the Dynatrace Settings API 2.0 (/api/v2/settings/objects).
 *
 * Auth is an API token with the settings.read scope, sent as
 * "Authorization: Api-Token ...". Listing uses cursor pagination: the
 * first page carries schemaIds/fields/pageSize, and every follow-up
 * request carries the nextPageKey cursor and nothing else.
 */
public class SettingsClient {

    public static final String OBJECTS_PATH = "/api/v2/settings/objects";

    private final String baseUrl;
    private final String apiToken;
    private final HttpClient http;

    public SettingsClient(String baseUrl, String apiToken, HttpClient http) {
        this.baseUrl = baseUrl.endsWith("/")
            ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.apiToken = apiToken;
        this.http = http;
    }

    /** Lists every settings object of one schema, walking all pages. */
    public List<Map<String, Object>> listObjects(String schemaId, String fields,
                                                 int pageSize) {
        List<Map<String, Object>> items = new ArrayList<>();
        String query = "schemaIds=" + encode(schemaId)
            + "&fields=" + encode(fields)
            + "&pageSize=" + pageSize;
        while (true) {
            Map<String, Object> page = getJson(OBJECTS_PATH + "?" + query);
            Object pageItems = page.get("items");
            if (pageItems instanceof List<?> list) {
                for (Object item : list) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> object = (Map<String, Object>) item;
                    items.add(object);
                }
            }
            Object next = page.get("nextPageKey");
            if (next == null) {
                return items;
            }
            // Subsequent pages: the cursor must be the only parameter.
            query = "nextPageKey=" + encode((String) next);
        }
    }

    /** Fetches one settings object (full shape, including updateToken). */
    public Map<String, Object> getObject(String objectId) {
        return getJson(OBJECTS_PATH + "/" + encode(objectId));
    }

    Map<String, Object> getJson(String pathAndQuery) {
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl + pathAndQuery))
            .header("Authorization", "Api-Token " + apiToken)
            .GET()
            .build();
        HttpResponse<String> response = send(request);
        if (response.statusCode() / 100 != 2) {
            throw SettingsApiException.fromBody(
                response.statusCode(), response.body());
        }
        return Json.parseObject(response.body());
    }

    HttpResponse<String> send(HttpRequest request) {
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new RuntimeException("settings request failed", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("settings request interrupted", e);
        }
    }

    String baseUrl() {
        return baseUrl;
    }

    String apiToken() {
        return apiToken;
    }

    static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }
}
