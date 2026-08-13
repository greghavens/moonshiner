package com.vmware.vcfops.networks;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Client for the VCF Operations for Networks application inventory sweep.
 *
 * <p>Talks to the operations pinned in {@code docs/contract.json}, which are derived from
 * {@code specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml} in
 * vmware/vcf-api-specs.
 *
 * <p>Status: first draft. It logs in and reads the first page of applications, which is enough
 * against a small lab appliance, but it has not been finished for a real inventory:
 *
 * <ul>
 *   <li>it stops after one page instead of following the {@code cursor};
 *   <li>it has no answer for a token that expires part way through a sweep;
 *   <li>it never releases the token it acquired.
 * </ul>
 */
public class NetworkInsightInventoryClient {

    /** One application, flattened from the {@code Application} schema. */
    public record ApplicationSummary(String entityId, String name, int tierCount, int memberCount) {}

    /** Page size to request from listApplications. */
    private static final int PAGE_SIZE = 5;

    private final String baseUrl;
    private final String username;
    private final String password;
    private final HttpClient http;

    private String token;

    public NetworkInsightInventoryClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.username = username;
        this.password = password;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .build();
    }

    /**
     * Enumerates every application on the appliance and returns a summary of each, in the order
     * the appliance listed them.
     */
    public List<ApplicationSummary> collectApplicationInventory() throws Exception {
        authenticate();

        List<String> entityIds = new ArrayList<>();
        Map<String, Object> page = getJson(applicationsUri(null));
        List<Object> results = Json.listAt(page, "results");
        if (results != null) {
            for (Object entry : results) {
                @SuppressWarnings("unchecked")
                Map<String, Object> entity = (Map<String, Object>) entry;
                entityIds.add(Json.stringAt(entity, "entity_id"));
            }
        }

        List<ApplicationSummary> summaries = new ArrayList<>();
        for (String entityId : entityIds) {
            Map<String, Object> app = getJson(applicationUri(entityId));
            summaries.add(new ApplicationSummary(
                    Json.stringAt(app, "entity_id"),
                    Json.stringAt(app, "name"),
                    Json.intAt(app, "tier_count", 0),
                    Json.intAt(app, "member_count", 0)));
        }
        return summaries;
    }

    // -------------------------------------------------------- operationId: create

    /** POST /api/ni/auth/token */
    private void authenticate() throws Exception {
        Map<String, Object> domain = new LinkedHashMap<>();
        domain.put("domain_type", "LOCAL");
        domain.put("value", "");

        Map<String, Object> credential = new LinkedHashMap<>();
        credential.put("username", username);
        credential.put("password", password);
        credential.put("domain", domain);

        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + "/api/ni/auth/token"))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(Json.write(credential)))
                .build();

        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IllegalStateException(
                    "Authentication failed: HTTP " + response.statusCode() + " " + response.body());
        }
        this.token = Json.stringAt(Json.parseObject(response.body()), "token");
    }

    // ------------------------------------------------- operationId: listApplications

    /** GET /api/ni/groups/applications */
    private URI applicationsUri(String cursor) {
        Map<String, String> query = new LinkedHashMap<>();
        query.put("size", String.valueOf(PAGE_SIZE));
        query.put("cursor", cursor == null ? "" : cursor);
        query.put("modifiedAfter", "");
        return URI.create(baseUrl + "/api/ni/groups/applications" + queryString(query));
    }

    // ---------------------------------------------- operationId: getApplicationById

    /** GET /api/ni/groups/applications/{id} */
    private URI applicationUri(String entityId) {
        Map<String, String> query = new LinkedHashMap<>();
        query.put("fetch_member_counts", "true");
        query.put("fetch_update_status", "");
        return URI.create(baseUrl + "/api/ni/groups/applications/"
                + encode(entityId) + queryString(query));
    }

    // ------------------------------------------------------------------ plumbing

    private Map<String, Object> getJson(URI uri) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(uri)
                .header("Authorization", "NetworkInsight " + token)
                .header("Accept", "application/json")
                .GET()
                .build();

        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw new IllegalStateException(
                    "GET " + uri.getPath() + " failed: HTTP " + response.statusCode()
                            + " " + response.body());
        }
        return Json.parseObject(response.body());
    }

    private static String queryString(Map<String, String> query) {
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : query.entrySet()) {
            sb.append(sb.isEmpty() ? '?' : '&')
                    .append(encode(e.getKey()))
                    .append('=')
                    .append(encode(e.getValue()));
        }
        return sb.toString();
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }
}
