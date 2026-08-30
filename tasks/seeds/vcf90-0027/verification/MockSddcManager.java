import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Loopback SDDC Manager stand-in pinned to {@code docs/contract.json}.
 *
 * <p>Routing, the allowed query parameter names and the response envelopes are
 * read out of the contract at startup, so the mock can only serve the two
 * operations the contract names: {@code createToken} (POST /v1/tokens) and
 * {@code getCredentials} (GET /v1/credentials). Anything else is answered 404
 * and recorded with a null operationId.
 *
 * <p>Every exchange is appended to a JSON Lines request log the harness reads
 * back to assert the wire shape. The server binds 127.0.0.1 on an ephemeral
 * port; no route reaches a live VMware endpoint.
 */
public final class MockSddcManager {

    /** Fixture credentials. Not real, and only ever accepted over loopback. */
    public static final String USERNAME = "administrator@vsphere.local";
    public static final String PASSWORD = "FixtureOnly!NotASecret1";
    public static final String API_KEY = "fixture-only-api-key-0000";
    public static final String ACCESS_TOKEN = "fixture.access.token.7a1c9d";
    public static final String REFRESH_TOKEN_ID = "c1a6f0d4-2001-4b9e-8f13-5d2c7e4a9b60";

    /** Documented resource types, from the getCredentials parameter description. */
    private static final Set<String> RESOURCE_TYPES =
            Set.of("ESXI", "VCENTER", "PSC", "NSXT_MANAGER", "NSXT_EDGE", "NSX_ALB", "BACKUP");

    /**
     * Server-side inventory, deliberately not in the order the client is asked
     * to emit: pagination alone must not produce the required ordering.
     */
    private static final String CREDENTIALS_JSON = """
            [
              {
                "id": "8e5f3c20-1007-4b1d-9c68-2a7d0f5e9b07",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "creationTimestamp": "2025-03-11T08:14:22.000Z",
                "modificationTimestamp": "2025-06-02T11:41:07.000Z",
                "resource": {
                  "resourceId": "0d1f4a72-3001-4c8e-9a56-71b2d3e4f501",
                  "resourceName": "esxi-04.vrack.vsphere.local",
                  "resourceType": "ESXI",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "4d7b1c62-1004-4e58-8a3b-9f0c2e6d5a04",
                "credentialType": "SSO",
                "accountType": "USER",
                "username": "administrator@vsphere.local",
                "creationTimestamp": "2025-03-11T08:09:55.000Z",
                "modificationTimestamp": "2025-03-11T08:09:55.000Z",
                "resource": {
                  "resourceId": "5b8c1e39-3004-4f21-b7d0-98a3c6e5f204",
                  "resourceName": "vcenter-1.vrack.vsphere.local",
                  "resourceType": "VCENTER",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "2b9c4d1e-1002-4f3a-8c15-3d6e9a0b7c02",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "creationTimestamp": "2025-03-11T08:10:31.000Z",
                "modificationTimestamp": "2025-07-18T04:22:16.000Z",
                "expiry": {
                  "expiryDate": "2026-03-11T08:10:31.000Z",
                  "lastCheckedDate": "2025-07-18T04:22:16.000Z",
                  "connectivityStatus": "ACTIVE",
                  "status": "ACTIVE"
                },
                "resource": {
                  "resourceId": "1a2b3c4d-3002-4d19-8e64-2f5a7b9c1d02",
                  "resourceName": "esxi-01.vrack.vsphere.local",
                  "resourceType": "ESXI",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "a1c4d7e9-1009-4e2b-9f45-8b3c6d0a2e09",
                "credentialType": "API",
                "accountType": "SYSTEM",
                "username": "svc-vcf",
                "creationTimestamp": "2025-03-11T08:10:33.000Z",
                "modificationTimestamp": "2025-07-18T04:22:18.000Z",
                "autoRotatePolicy": {
                  "frequencyInDays": 90,
                  "nextSchedule": "2025-10-16T04:22:18.000Z"
                },
                "resource": {
                  "resourceId": "1a2b3c4d-3002-4d19-8e64-2f5a7b9c1d02",
                  "resourceName": "esxi-01.vrack.vsphere.local",
                  "resourceType": "ESXI",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "7a2d9b41-1006-4a9f-8d72-1c6e4b8f3a06",
                "credentialType": "API",
                "accountType": "SYSTEM",
                "username": "admin",
                "creationTimestamp": "2025-04-02T15:26:40.000Z",
                "modificationTimestamp": "2025-04-02T15:26:40.000Z",
                "resource": {
                  "resourceId": "9f0e8d7c-3006-4a53-b21f-4c7d8e9a0b06",
                  "resourceName": "NSX-ALB-01.vrack.vsphere.local",
                  "resourceType": "NSX_ALB",
                  "domainNames": ["sfo-m01", "sfo-w01"]
                }
              },
              {
                "id": "0a4c7f13-1001-4b6e-9a2f-7c1d5e93b201",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "creationTimestamp": "2025-05-27T09:03:12.000Z",
                "modificationTimestamp": "2025-05-27T09:03:12.000Z",
                "resource": {
                  "resourceId": "6c5b4a39-3001-4e77-8d10-3b6f2a1c9e01",
                  "resourceName": "ESXi-Spare-01.vrack.vsphere.local",
                  "resourceType": "ESXI"
                }
              },
              {
                "id": "b3e8f1a6-1011-4c5d-8e29-4f7a1b9d6c11",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "creationTimestamp": "2025-03-11T08:16:05.000Z",
                "modificationTimestamp": "2025-06-02T11:41:09.000Z",
                "resource": {
                  "resourceId": "7e6d5c4b-3011-4b82-9c31-8a0f1d2e3c11",
                  "resourceName": "esxi-03.vrack.vsphere.local",
                  "resourceType": "ESXI",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "6f1a8e77-1005-4d2c-b0a9-58c3e7d41d05",
                "credentialType": "API",
                "accountType": "SYSTEM",
                "username": "root",
                "creationTimestamp": "2025-03-11T08:10:35.000Z",
                "modificationTimestamp": "2025-03-11T08:10:35.000Z",
                "resource": {
                  "resourceId": "1a2b3c4d-3002-4d19-8e64-2f5a7b9c1d02",
                  "resourceName": "esxi-01.vrack.vsphere.local",
                  "resourceType": "ESXI",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "9b6a2e58-1008-4f7c-8b30-6d1e3a9c4f08",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "creationTimestamp": "2025-03-11T08:20:44.000Z",
                "modificationTimestamp": "2025-03-11T08:20:44.000Z",
                "resource": {
                  "resourceId": "4a3b2c1d-3008-4c60-9f8e-5d4c3b2a1f08",
                  "resourceName": "nsxt-01.vrack.vsphere.local",
                  "resourceType": "NSXT_MANAGER",
                  "domainNames": ["sfo-m01"]
                }
              },
              {
                "id": "3c8e5a90-1003-4c7d-9e11-5b2f8d4a1e03",
                "credentialType": "SSH",
                "accountType": "USER",
                "username": "root",
                "creationTimestamp": "2025-03-11T08:12:48.000Z",
                "modificationTimestamp": "2025-06-02T11:41:08.000Z",
                "resource": {
                  "resourceId": "2d3e4f5a-3003-4a94-8b27-6e1c0d9b8a03",
                  "resourceName": "esxi-02.vrack.vsphere.local",
                  "resourceType": "ESXI",
                  "domainNames": ["sfo-m01"]
                }
              }
            ]
            """;

    private final Path requestLogPath;
    private final Map<String, String> routes;
    private final Set<String> credentialsQueryParameters;
    private final List<Object> inventory;
    private final AtomicInteger sequence = new AtomicInteger();

    private HttpServer server;

    public MockSddcManager(Path contractPath, Path requestLogPath) throws IOException {
        this.requestLogPath = requestLogPath;
        this.inventory = Json.array(Json.parse(CREDENTIALS_JSON));

        Map<String, Object> contract = Json.object(Json.parse(Files.readString(contractPath)));
        Map<String, Object> paths = Json.object(contract.get("paths"));
        Map<String, String> discovered = new LinkedHashMap<>();
        Set<String> queryParameters = null;
        for (Map.Entry<String, Object> pathEntry : paths.entrySet()) {
            for (Map.Entry<String, Object> methodEntry : Json.object(pathEntry.getValue()).entrySet()) {
                Map<String, Object> operation = Json.object(methodEntry.getValue());
                String operationId = Json.string(operation.get("operationId"));
                String method = methodEntry.getKey().toUpperCase(Locale.ROOT);
                discovered.put(method + " " + pathEntry.getKey(), operationId);
                if ("getCredentials".equals(operationId)) {
                    queryParameters = new java.util.LinkedHashSet<>();
                    for (Object parameter : Json.array(operation.get("parameters"))) {
                        Map<String, Object> spec = Json.object(parameter);
                        if ("query".equals(Json.string(spec.get("in")))) {
                            queryParameters.add(Json.string(spec.get("name")));
                        }
                    }
                }
            }
        }
        if (!discovered.containsKey("POST /v1/tokens") || !discovered.containsKey("GET /v1/credentials")) {
            throw new IOException("contract does not name createToken and getCredentials: " + discovered.keySet());
        }
        this.routes = Map.copyOf(discovered);
        this.credentialsQueryParameters = Set.copyOf(queryParameters);
        Files.writeString(requestLogPath, "", StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
    }

    /** Binds 127.0.0.1 on an ephemeral port and returns the base URL. */
    public String start() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", this::handle);
        server.setExecutor(null);
        server.start();
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
    }

    /** The durable request log, one parsed record per exchange, in order. */
    public List<Map<String, Object>> requestLog() {
        try {
            List<Map<String, Object>> records = new ArrayList<>();
            for (String line : Files.readAllLines(requestLogPath, StandardCharsets.UTF_8)) {
                if (!line.isBlank()) {
                    records.add(Json.object(Json.parse(line)));
                }
            }
            return records;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        URI uri = exchange.getRequestURI();
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String path = uri.getPath();
        String rawQuery = uri.getRawQuery();
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);

        Map<String, Object> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((name, values) ->
                headers.put(name.toLowerCase(Locale.ROOT), String.join(", ", values)));

        Map<String, List<String>> query;
        Response response;
        try {
            query = parseQuery(rawQuery);
        } catch (IllegalArgumentException e) {
            query = new LinkedHashMap<>();
            response = error(400, "QUERY_STRING_MALFORMED", e.getMessage());
            emit(exchange, null, method, path, rawQuery, query, body, headers, response);
            return;
        }

        String operationId = routes.get(method + " " + path);
        if (operationId == null) {
            response = error(404, "NOT_FOUND", "no operation in the pinned contract serves " + method + " " + path);
        } else {
            response = switch (operationId) {
                case "createToken" -> createToken(headers, body);
                case "getCredentials" -> getCredentials(headers, query);
                default -> error(404, "NOT_FOUND", "unroutable operation " + operationId);
            };
        }
        emit(exchange, operationId, method, path, rawQuery, query, body, headers, response);
    }

    private Response createToken(Map<String, Object> headers, String body) {
        if (headers.containsKey("authorization")) {
            return error(400, "TOKEN_REQUEST_NOT_ANONYMOUS",
                    "createToken establishes the session and must not carry an Authorization header");
        }
        String contentType = (String) headers.get("content-type");
        if (!"application/json".equals(contentType)) {
            return error(400, "CONTENT_TYPE_INVALID",
                    "expected Content-Type application/json, got " + contentType);
        }
        Map<String, Object> spec;
        try {
            spec = Json.object(Json.parse(body));
        } catch (RuntimeException e) {
            return error(400, "TOKEN_SPEC_MALFORMED", "request body is not a JSON object: " + e.getMessage());
        }
        for (Map.Entry<String, Object> entry : spec.entrySet()) {
            if (!Set.of("username", "password", "apiKey", "idToken").contains(entry.getKey())) {
                return error(400, "TOKEN_SPEC_PROPERTY_UNKNOWN",
                        "TokenCreationSpec has no property " + entry.getKey());
            }
            if (!(entry.getValue() instanceof String value) || value.isEmpty()) {
                return error(400, "TOKEN_SPEC_PROPERTY_EMPTY",
                        "property " + entry.getKey() + " was sent without a value; omit unset properties instead");
            }
        }
        boolean passwordGrant = spec.keySet().equals(Set.of("username", "password"))
                && USERNAME.equals(spec.get("username"))
                && PASSWORD.equals(spec.get("password"));
        boolean apiKeyGrant = spec.keySet().equals(Set.of("apiKey")) && API_KEY.equals(spec.get("apiKey"));
        if (!passwordGrant && !apiKeyGrant) {
            return error(400, "TOKEN_SPEC_INVALID", "the supplied TokenCreationSpec is not accepted");
        }

        Map<String, Object> pair = new LinkedHashMap<>();
        pair.put("accessToken", ACCESS_TOKEN);
        pair.put("refreshToken", Map.of("id", REFRESH_TOKEN_ID));
        return new Response(201, Json.write(pair));
    }

    private Response getCredentials(Map<String, Object> headers, Map<String, List<String>> query) {
        if (!("Bearer " + ACCESS_TOKEN).equals(headers.get("authorization"))) {
            return error(401, "UNAUTHORIZED", "a bearer access token from createToken is required");
        }
        for (Map.Entry<String, List<String>> entry : query.entrySet()) {
            if (!credentialsQueryParameters.contains(entry.getKey())) {
                return error(400, "QUERY_PARAMETER_UNKNOWN",
                        "getCredentials has no query parameter " + entry.getKey());
            }
            if (entry.getValue().size() > 1) {
                return error(400, "QUERY_PARAMETER_REPEATED",
                        "query parameter " + entry.getKey() + " was sent " + entry.getValue().size() + " times");
            }
            if (entry.getValue().get(0).isEmpty()) {
                return error(400, "QUERY_PARAMETER_EMPTY",
                        "query parameter " + entry.getKey() + " was sent empty; omit unset filters instead");
            }
        }

        String resourceType = single(query, "resourceType");
        if (resourceType != null && !RESOURCE_TYPES.contains(resourceType)) {
            return error(400, "CREDENTIAL_RESOURCE_TYPE_INVALID",
                    "unsupported resource type " + resourceType);
        }
        String resourceName = single(query, "resourceName");
        String domainName = single(query, "domainName");
        String accountType = single(query, "accountType");

        int pageNumber;
        int pageSize;
        try {
            pageNumber = parsePageParameter(query, "pageNumber");
            pageSize = parsePageParameter(query, "pageSize");
        } catch (IllegalArgumentException e) {
            return error(400, "PAGE_PARAMETER_INVALID", e.getMessage());
        }

        List<Object> matches = new ArrayList<>();
        for (Object element : inventory) {
            Map<String, Object> credential = Json.object(element);
            Map<String, Object> resource = Json.object(credential.get("resource"));
            if (resourceType != null && !resourceType.equals(resource.get("resourceType"))) {
                continue;
            }
            if (resourceName != null && !resourceName.equals(resource.get("resourceName"))) {
                continue;
            }
            if (accountType != null && !accountType.equals(credential.get("accountType"))) {
                continue;
            }
            if (domainName != null) {
                Object domainNames = resource.get("domainNames");
                if (domainNames == null || !Json.array(domainNames).contains(domainName)) {
                    continue;
                }
            }
            matches.add(credential);
        }

        int totalElements = matches.size();
        int totalPages = pageSize == 0 ? (totalElements == 0 ? 0 : 1)
                : (totalElements + pageSize - 1) / pageSize;
        int from = pageSize == 0 ? 0 : Math.min(pageNumber * pageSize, totalElements);
        int to = pageSize == 0 ? totalElements : Math.min(from + pageSize, totalElements);
        List<Object> elements = new ArrayList<>(matches.subList(from, to));

        Map<String, Object> pageMetadata = new LinkedHashMap<>();
        pageMetadata.put("pageNumber", pageNumber);
        pageMetadata.put("pageSize", elements.size());
        pageMetadata.put("totalElements", totalElements);
        pageMetadata.put("totalPages", totalPages);

        Map<String, Object> page = new LinkedHashMap<>();
        page.put("elements", elements);
        page.put("pageMetadata", pageMetadata);
        return new Response(200, Json.write(page));
    }

    private static int parsePageParameter(Map<String, List<String>> query, String name) {
        String raw = single(query, name);
        if (raw == null) {
            return 0;
        }
        int value;
        try {
            value = Integer.parseInt(raw);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException(name + " must be a number, got " + raw);
        }
        if (value < 0) {
            throw new IllegalArgumentException(name + " must be a positive number, got " + raw);
        }
        return value;
    }

    private static String single(Map<String, List<String>> query, String name) {
        List<String> values = query.get(name);
        return values == null ? null : values.get(0);
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> query = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return query;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                throw new IllegalArgumentException("empty query segment in '" + rawQuery + "'");
            }
            int eq = pair.indexOf('=');
            String name = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            query.computeIfAbsent(URLDecoder.decode(name, StandardCharsets.UTF_8), key -> new ArrayList<>())
                    .add(URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return query;
    }

    private static Response error(int status, String errorCode, String message) {
        Map<String, Object> error = new LinkedHashMap<>();
        error.put("errorCode", errorCode);
        error.put("errorType", status >= 500 ? "INTERNAL_SERVER_ERROR" : "REQUEST_ERROR");
        error.put("message", message);
        return new Response(status, Json.write(error));
    }

    private void emit(HttpExchange exchange, String operationId, String method, String path, String rawQuery,
                      Map<String, List<String>> query, String body, Map<String, Object> headers,
                      Response response) throws IOException {
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("seq", sequence.getAndIncrement());
        record.put("operationId", operationId);
        record.put("method", method);
        record.put("path", path);
        record.put("rawQuery", rawQuery);
        Map<String, Object> queryRecord = new LinkedHashMap<>();
        query.forEach(queryRecord::put);
        record.put("query", queryRecord);
        record.put("headers", headers);
        record.put("body", body);
        record.put("status", response.status());
        synchronized (this) {
            Files.writeString(requestLogPath, Json.write(record) + System.lineSeparator(),
                    StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }

        byte[] payload = response.body().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status(), payload.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(payload);
        }
    }

    private record Response(int status, String body) {
    }
}
