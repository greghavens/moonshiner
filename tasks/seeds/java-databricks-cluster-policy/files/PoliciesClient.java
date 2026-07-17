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
import java.util.function.LongConsumer;

/**
 * Client for the Databricks Cluster Policies API (/api/2.0/policies/clusters)
 * and the read-only Policy Families API (/api/2.0/policy-families).
 *
 * Every call carries Bearer auth and Accept: application/json. 429 responses
 * are retried after the Retry-After interval (waiting goes through the
 * injected sleeper so tests never block); other failures raise
 * DbxApiException.
 */
public class PoliciesClient {

    static final String POLICIES = "/api/2.0/policies/clusters";
    static final String FAMILIES = "/api/2.0/policy-families";

    private final String baseUrl;
    private final String token;
    private final LongConsumer sleeper;
    private final int maxRetries;
    private final HttpClient http = HttpClient.newHttpClient();

    public PoliciesClient(String baseUrl, String token, LongConsumer sleeper, int maxRetries) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.token = token;
        this.sleeper = sleeper;
        this.maxRetries = maxRetries;
    }

    /** Lists every cluster policy. The response is a single, unpaginated document. */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> listPolicies() {
        Map<String, Object> doc = request("GET",
                POLICIES + "/list?sort_column=POLICY_NAME&sort_order=ASC", null);
        List<Map<String, Object>> policies = (List<Map<String, Object>>) (List<?>) doc
                .getOrDefault("policies", new ArrayList<>());
        return policies;
    }

    public Map<String, Object> getPolicy(String policyId) {
        return request("GET", POLICIES + "/get?policy_id=" + encode(policyId), null);
    }

    /** Creates a policy and returns its policy_id. */
    public String createPolicy(Map<String, Object> fields) {
        Map<String, Object> doc = request("POST", POLICIES + "/create", Json.write(fields));
        return (String) doc.get("policy_id");
    }

    /** Edits a policy in place; {@code fields} must include policy_id. */
    public void editPolicy(Map<String, Object> fields) {
        if (!fields.containsKey("policy_id")) {
            throw new IllegalArgumentException("editPolicy requires policy_id");
        }
        request("POST", POLICIES + "/edit", Json.write(fields));
    }

    /** Walks the paginated policy-families listing until next_page_token runs out. */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> listPolicyFamilies(int maxResults) {
        List<Map<String, Object>> out = new ArrayList<>();
        String pageToken = null;
        while (true) {
            StringBuilder path = new StringBuilder(FAMILIES)
                    .append("?max_results=").append(maxResults);
            if (pageToken != null) {
                path.append("&page_token=").append(encode(pageToken));
            }
            Map<String, Object> doc = request("GET", path.toString(), null);
            List<Map<String, Object>> page = (List<Map<String, Object>>) (List<?>) doc
                    .getOrDefault("policy_families", new ArrayList<>());
            out.addAll(page);
            pageToken = (String) doc.get("next_page_token");
            if (pageToken == null || pageToken.isEmpty()) {
                return out;
            }
        }
    }

    public Map<String, Object> getPolicyFamily(String familyId) {
        return request("GET", FAMILIES + "/" + encode(familyId), null);
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private Map<String, Object> request(String method, String path, String body) {
        for (int attempt = 0; ; attempt++) {
            HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + path))
                    .header("Authorization", "Bearer " + token)
                    .header("Accept", "application/json");
            if (body != null) {
                builder.header("Content-Type", "application/json")
                        .method(method, HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8));
            } else {
                builder.method(method, HttpRequest.BodyPublishers.noBody());
            }
            HttpResponse<String> resp;
            try {
                resp = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
            } catch (IOException e) {
                throw new DbxApiException(0, "TRANSPORT_ERROR", "request failed: " + e.getClass().getSimpleName());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new DbxApiException(0, "INTERRUPTED", "request interrupted");
            }
            if (resp.statusCode() / 100 == 2) {
                String text = resp.body();
                return text == null || text.isBlank() ? Map.of() : Json.parseObject(text);
            }
            String errorCode = "UNKNOWN";
            String message = "";
            try {
                Map<String, Object> env = Json.parseObject(resp.body());
                if (env.get("error_code") instanceof String code) {
                    errorCode = code;
                }
                if (env.get("message") instanceof String msg) {
                    message = msg;
                }
            } catch (RuntimeException ignored) {
                // non-JSON error body; keep defaults
            }
            if (resp.statusCode() == 429) {
                long retryAfter = resp.headers().firstValue("Retry-After")
                        .map(Long::parseLong).orElse(1L);
                if (attempt < maxRetries) {
                    sleeper.accept(retryAfter);
                    continue;
                }
                throw new RateLimitException(errorCode, message, retryAfter);
            }
            throw new DbxApiException(resp.statusCode(), errorCode, message);
        }
    }
}
