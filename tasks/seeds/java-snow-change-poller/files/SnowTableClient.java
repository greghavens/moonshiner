import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;

/**
 * Thin ServiceNow Table API reader over java.net.http. Fetches one page per
 * call; callers own pagination and retry policy. Redirects are never
 * followed so Basic credentials cannot leak to another origin.
 */
public class SnowTableClient {

    public static final class Page {
        public final List<Map<String, Object>> records;
        public final int totalCount; // from the X-Total-Count header

        public Page(List<Map<String, Object>> records, int totalCount) {
            this.records = records;
            this.totalCount = totalCount;
        }
    }

    private final String baseUrl;
    private final String authHeader;
    private final HttpClient http = HttpClient.newBuilder()
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();

    public SnowTableClient(String instanceUrl, String username, String password) {
        this.baseUrl = instanceUrl.replaceAll("/+$", "");
        this.authHeader = "Basic " + Base64.getEncoder().encodeToString(
                (username + ":" + password).getBytes(StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    public Page fetchPage(String table, String query, List<String> fields,
                          int limit, int offset,
                          boolean displayValue, boolean excludeReferenceLink)
            throws SnowApiException, IOException, InterruptedException {
        StringBuilder qs = new StringBuilder();
        if (query != null && !query.isEmpty()) {
            append(qs, "sysparm_query", query);
        }
        if (fields != null && !fields.isEmpty()) {
            append(qs, "sysparm_fields", String.join(",", fields));
        }
        append(qs, "sysparm_display_value", String.valueOf(displayValue));
        append(qs, "sysparm_exclude_reference_link", String.valueOf(excludeReferenceLink));
        append(qs, "sysparm_limit", String.valueOf(limit));
        append(qs, "sysparm_offset", String.valueOf(offset));

        HttpRequest request = HttpRequest.newBuilder(
                        URI.create(baseUrl + "/api/now/table/" + table + "?" + qs))
                .header("Accept", "application/json")
                .header("Authorization", authHeader)
                .GET()
                .build();
        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw toException(response);
        }
        Map<String, Object> envelope = (Map<String, Object>) Json.parse(response.body());
        List<Object> raw = (List<Object>) envelope.get("result");
        List<Map<String, Object>> records = new ArrayList<>();
        for (Object o : raw) {
            records.add((Map<String, Object>) o);
        }
        int total = Integer.parseInt(response.headers()
                .firstValue("X-Total-Count")
                .orElse(String.valueOf(records.size())));
        return new Page(records, total);
    }

    private static void append(StringBuilder qs, String key, String value) {
        if (qs.length() > 0) {
            qs.append('&');
        }
        qs.append(key).append('=').append(URLEncoder.encode(value, StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    private static SnowApiException toException(HttpResponse<String> response) {
        String message = "HTTP " + response.statusCode();
        String detail = "";
        try {
            Map<String, Object> body = (Map<String, Object>) Json.parse(response.body());
            Object err = body.get("error");
            if (err instanceof Map) {
                Map<String, Object> em = (Map<String, Object>) err;
                if (em.get("message") != null) message = em.get("message").toString();
                if (em.get("detail") != null) detail = em.get("detail").toString();
            }
        } catch (RuntimeException ignored) {
            // non-JSON error body: keep the HTTP fallback message
        }
        int retryAfter = response.headers().firstValue("Retry-After")
                .map(Integer::parseInt).orElse(0);
        return new SnowApiException(response.statusCode(), message, detail, retryAfter);
    }
}
