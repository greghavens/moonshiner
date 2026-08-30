import java.io.IOException;
import java.net.http.HttpClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Acceptance harness for {@link SddcCredentialsClient}.
 *
 * <p>Starts the contract-pinned loopback mock, drives the client against it and
 * then verifies the exact wire shape from the mock's durable request log:
 * operation order, paths, query parameter name sets, header values, request
 * bodies and - specifically - that optional fields the caller left unset are
 * omitted from the wire rather than sent empty. Nothing here contacts a live
 * VMware endpoint.
 *
 * <p>Run with: {@code java TestMain.java}
 */
public final class TestMain {

    private static final String BEARER = "Bearer " + MockSddcManager.ACCESS_TOKEN;

    private static final List<String> EXPECTED_ESXI = List.of(
            "ESXi-Spare-01.vrack.vsphere.local|root|0a4c7f13-1001-4b6e-9a2f-7c1d5e93b201",
            "esxi-01.vrack.vsphere.local|root|2b9c4d1e-1002-4f3a-8c15-3d6e9a0b7c02",
            "esxi-01.vrack.vsphere.local|root|6f1a8e77-1005-4d2c-b0a9-58c3e7d41d05",
            "esxi-01.vrack.vsphere.local|svc-vcf|a1c4d7e9-1009-4e2b-9f45-8b3c6d0a2e09",
            "esxi-02.vrack.vsphere.local|root|3c8e5a90-1003-4c7d-9e11-5b2f8d4a1e03",
            "esxi-03.vrack.vsphere.local|root|b3e8f1a6-1011-4c5d-8e29-4f7a1b9d6c11",
            "esxi-04.vrack.vsphere.local|root|8e5f3c20-1007-4b1d-9c68-2a7d0f5e9b07");

    private static final List<String> EXPECTED_ALL = List.of(
            "ESXi-Spare-01.vrack.vsphere.local|root|0a4c7f13-1001-4b6e-9a2f-7c1d5e93b201",
            "NSX-ALB-01.vrack.vsphere.local|admin|7a2d9b41-1006-4a9f-8d72-1c6e4b8f3a06",
            "esxi-01.vrack.vsphere.local|root|2b9c4d1e-1002-4f3a-8c15-3d6e9a0b7c02",
            "esxi-01.vrack.vsphere.local|root|6f1a8e77-1005-4d2c-b0a9-58c3e7d41d05",
            "esxi-01.vrack.vsphere.local|svc-vcf|a1c4d7e9-1009-4e2b-9f45-8b3c6d0a2e09",
            "esxi-02.vrack.vsphere.local|root|3c8e5a90-1003-4c7d-9e11-5b2f8d4a1e03",
            "esxi-03.vrack.vsphere.local|root|b3e8f1a6-1011-4c5d-8e29-4f7a1b9d6c11",
            "esxi-04.vrack.vsphere.local|root|8e5f3c20-1007-4b1d-9c68-2a7d0f5e9b07",
            "nsxt-01.vrack.vsphere.local|root|9b6a2e58-1008-4f7c-8b30-6d1e3a9c4f08",
            "vcenter-1.vrack.vsphere.local|administrator@vsphere.local|4d7b1c62-1004-4e58-8a3b-9f0c2e6d5a04");

    private static int checks;

    public static void main(String[] args) throws Exception {
        Path contract = Path.of("docs", "contract.json");
        if (!Files.isReadable(contract)) {
            throw new IllegalStateException("run from the repository root; docs/contract.json not found");
        }
        Path logDir = Files.createTempDirectory("sddc-contract-mock-");
        Path requestLog = logDir.resolve("requests.jsonl");
        MockSddcManager mock = new MockSddcManager(contract, requestLog);
        String baseUrl = mock.start();
        try {
            run(mock, baseUrl);
        } catch (Throwable failure) {
            System.out.println();
            System.out.println("FAILED: " + failure);
            if (!(failure instanceof AssertionError)) {
                failure.printStackTrace(System.out);
            }
            System.out.println("request log: " + requestLog);
            mock.stop();
            System.exit(1);
        } finally {
            mock.stop();
        }
        System.out.println();
        System.out.println(checks + " checks passed");
        System.out.println("all checks passed");
    }

    private static void run(MockSddcManager mock, String baseUrl) throws Exception {
        HttpClient http = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build();

        // 1. Nothing may go on the wire before createToken has run.
        SddcCredentialsClient unauthenticated = new SddcCredentialsClient(baseUrl, http);
        expect(IllegalStateException.class, () -> unauthenticated.listCredentials("ESXI", 3),
                "listCredentials before authentication");
        assertEquals("requests issued before authentication", 0, mock.requestLog().size());
        pass("listCredentials before authentication fails without touching the wire");

        // 2. Password grant: createToken carries username and password only.
        SddcCredentialsClient client = new SddcCredentialsClient(baseUrl, http);
        client.authenticateWithPassword(MockSddcManager.USERNAME, MockSddcManager.PASSWORD);
        List<Map<String, Object>> log = mock.requestLog();
        assertEquals("requests after password authentication", 1, log.size());
        Map<String, Object> token = log.get(0);
        assertEquals("token operationId", "createToken", token.get("operationId"));
        assertEquals("token method", "POST", token.get("method"));
        assertEquals("token path", "/v1/tokens", token.get("path"));
        assertEquals("token query string", null, token.get("rawQuery"));
        assertEquals("token status", 201, Json.integer(token.get("status")));
        assertEquals("token Content-Type", "application/json", header(token, "content-type"));
        assertEquals("token Accept", "application/json", header(token, "accept"));
        assertEquals("token Authorization", null, header(token, "authorization"));
        Map<String, Object> spec = Json.object(Json.parse(body(token)));
        assertEquals("TokenCreationSpec properties", Set.of("username", "password"), spec.keySet());
        assertEquals("TokenCreationSpec.username", MockSddcManager.USERNAME, spec.get("username"));
        assertEquals("TokenCreationSpec.password", MockSddcManager.PASSWORD, spec.get("password"));
        assertAbsent("password grant body", body(token), "apiKey", "idToken");
        pass("createToken sends only the set TokenCreationSpec properties");

        // 3. Page size is validated before any request is issued.
        expect(IllegalArgumentException.class, () -> client.listCredentials("ESXI", 0), "pageSize 0");
        expect(IllegalArgumentException.class, () -> client.listCredentials("ESXI", -1), "pageSize -1");
        assertEquals("requests issued for invalid page sizes", 1, mock.requestLog().size());
        pass("an unusable pageSize is rejected before the wire");

        // 4. Filtered collection: every page fetched, complete result, stable order.
        List<SddcCredentialsClient.Credential> esxi = client.listCredentials("ESXI", 3);
        assertEquals("ESXI credentials", EXPECTED_ESXI, lines(esxi));
        SddcCredentialsClient.Credential first = esxi.get(0);
        assertEquals("first id", "0a4c7f13-1001-4b6e-9a2f-7c1d5e93b201", first.id());
        assertEquals("first credentialType", "SSH", first.credentialType());
        assertEquals("first accountType", "USER", first.accountType());
        assertEquals("first username", "root", first.username());
        assertEquals("first resourceId", "6c5b4a39-3001-4e77-8d10-3b6f2a1c9e01", first.resourceId());
        assertEquals("first resourceName", "ESXi-Spare-01.vrack.vsphere.local", first.resourceName());
        assertEquals("first resourceType", "ESXI", first.resourceType());
        assertEquals("first domainNames", List.of(), first.domainNames());
        SddcCredentialsClient.Credential multiDomain = esxi.get(3);
        assertEquals("svc-vcf domainNames", List.of("sfo-m01"), multiDomain.domainNames());
        expect(UnsupportedOperationException.class, () -> esxi.add(first), "mutable result list");
        pass("the filtered collection is complete, projected, stably ordered and unmodifiable");

        log = mock.requestLog();
        assertEquals("requests after the filtered listing", 4, log.size());
        assertPagedRequests(log.subList(1, 4), Set.of("resourceType", "pageNumber", "pageSize"), "ESXI", "3");
        pass("the filtered listing walked pages 0, 1 and 2 with the documented parameters");

        // 5. API key grant plus an unfiltered listing: the unset filter is not on the wire.
        SddcCredentialsClient keyClient = new SddcCredentialsClient(baseUrl, http);
        keyClient.authenticateWithApiKey(MockSddcManager.API_KEY);
        log = mock.requestLog();
        assertEquals("requests after api key authentication", 5, log.size());
        Map<String, Object> keyToken = log.get(4);
        assertEquals("api key operationId", "createToken", keyToken.get("operationId"));
        Map<String, Object> keySpec = Json.object(Json.parse(body(keyToken)));
        assertEquals("api key TokenCreationSpec properties", Set.of("apiKey"), keySpec.keySet());
        assertEquals("TokenCreationSpec.apiKey", MockSddcManager.API_KEY, keySpec.get("apiKey"));
        assertAbsent("api key grant body", body(keyToken), "username", "password", "idToken");
        pass("the api key grant omits username, password and idToken entirely");

        List<SddcCredentialsClient.Credential> all = keyClient.listCredentials(null, 4);
        assertEquals("unfiltered credentials", EXPECTED_ALL, lines(all));
        log = mock.requestLog();
        assertEquals("requests after the unfiltered listing", 8, log.size());
        assertPagedRequests(log.subList(5, 8), Set.of("pageNumber", "pageSize"), null, "4");
        for (Map<String, Object> record : log.subList(5, 8)) {
            String rawQuery = (String) record.get("rawQuery");
            if (rawQuery.contains("resourceType")) {
                throw new AssertionError("an unset resourceType filter reached the wire: " + rawQuery);
            }
        }
        pass("an unset resourceType filter is omitted, not sent empty");

        // 6. A single-page collection must not provoke a second request.
        List<SddcCredentialsClient.Credential> vcenter = keyClient.listCredentials("VCENTER", 5);
        assertEquals("VCENTER credentials", EXPECTED_ALL.subList(9, 10), lines(vcenter));
        log = mock.requestLog();
        assertEquals("requests after the single-page listing", 9, log.size());
        assertEquals("single page pageNumber", "0", queryValue(log.get(8), "pageNumber"));
        pass("a one-page collection costs exactly one request");

        // 7. A rejected page stops the walk and surfaces the Error body.
        String invalidType = "BOGUS TYPE/+%✓";
        SddcCredentialsClient.ApiException failure = expect(SddcCredentialsClient.ApiException.class,
                () -> keyClient.listCredentials(invalidType, 3), "an unsupported resourceType");
        assertEquals("error status", 400, failure.statusCode());
        assertEquals("error code", "CREDENTIAL_RESOURCE_TYPE_INVALID", failure.errorCode());
        assertEquals("error message", "unsupported resource type " + invalidType, failure.getMessage());
        log = mock.requestLog();
        assertEquals("requests after the rejected listing", 10, log.size());
        assertEquals("rejected request status", 400, Json.integer(log.get(9).get("status")));
        assertEquals("decoded rejected resourceType", invalidType, queryValue(log.get(9), "resourceType"));
        assertRawParameterEncoding(log.get(9), "resourceType", "BOGUS%20TYPE%2F%2B%25%E2%9C%93");
        pass("a 400 stops the page walk, preserves the Error body and uses percent-encoded parameters");

        // 8. Only contract operations were ever addressed.
        Set<String> operations = new LinkedHashSet<>();
        for (Map<String, Object> record : log) {
            Object operationId = record.get("operationId");
            if (operationId == null) {
                throw new AssertionError("a request addressed no contract operation: " + Json.write(record));
            }
            operations.add((String) operationId);
        }
        assertEquals("operations exercised", Set.of("createToken", "getCredentials"), operations);
        pass("only createToken and getCredentials were addressed");
    }

    private static void assertPagedRequests(List<Map<String, Object>> records, Set<String> expectedParameters,
                                            String resourceType, String pageSize) {
        for (int i = 0; i < records.size(); i++) {
            Map<String, Object> record = records.get(i);
            String where = "page request " + i;
            assertEquals(where + " operationId", "getCredentials", record.get("operationId"));
            assertEquals(where + " method", "GET", record.get("method"));
            assertEquals(where + " path", "/v1/credentials", record.get("path"));
            assertEquals(where + " status", 200, Json.integer(record.get("status")));
            assertEquals(where + " Authorization", BEARER, header(record, "authorization"));
            assertEquals(where + " Accept", "application/json", header(record, "accept"));
            assertEquals(where + " Content-Type", null, header(record, "content-type"));
            assertEquals(where + " body", "", body(record));

            Map<String, Object> query = Json.object(record.get("query"));
            assertEquals(where + " query parameters", new TreeSet<>(expectedParameters),
                    new TreeSet<>(query.keySet()));
            for (Map.Entry<String, Object> entry : query.entrySet()) {
                List<Object> values = Json.array(entry.getValue());
                if (values.size() != 1) {
                    throw new AssertionError(where + " sent " + entry.getKey() + " " + values.size() + " times");
                }
                if (String.valueOf(values.get(0)).isEmpty()) {
                    throw new AssertionError(where + " sent " + entry.getKey() + " with an empty value");
                }
            }
            assertEquals(where + " pageNumber", String.valueOf(i), queryValue(record, "pageNumber"));
            assertEquals(where + " pageSize", pageSize, queryValue(record, "pageSize"));
            if (resourceType != null) {
                assertEquals(where + " resourceType", resourceType, queryValue(record, "resourceType"));
            }
        }
    }

    private static List<String> lines(List<SddcCredentialsClient.Credential> credentials) {
        List<String> out = new ArrayList<>();
        for (SddcCredentialsClient.Credential credential : credentials) {
            out.add(credential.resourceName() + "|" + credential.username() + "|" + credential.id());
        }
        return out;
    }

    private static String queryValue(Map<String, Object> record, String name) {
        Object values = Json.object(record.get("query")).get(name);
        return values == null ? null : String.valueOf(Json.array(values).get(0));
    }

    private static void assertRawParameterEncoding(Map<String, Object> record, String name, String expectedValue) {
        String rawQuery = (String) record.get("rawQuery");
        String prefix = name + "=";
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.startsWith(prefix)) {
                String actualValue = pair.substring(prefix.length());
                if (!expectedValue.equalsIgnoreCase(actualValue)) {
                    throw new AssertionError(name + " encoding: expected <" + expectedValue
                            + ">, got <" + actualValue + ">");
                }
                return;
            }
        }
        throw new AssertionError("raw query has no " + name + " parameter: " + rawQuery);
    }

    private static String header(Map<String, Object> record, String name) {
        return (String) Json.object(record.get("headers")).get(name);
    }

    private static String body(Map<String, Object> record) {
        return (String) record.get("body");
    }

    private static void assertAbsent(String where, String body, String... forbidden) {
        for (String needle : forbidden) {
            if (body.contains(needle)) {
                throw new AssertionError(where + " mentions the unset property " + needle + ": " + body);
            }
        }
    }

    private static void assertEquals(String what, Object expected, Object actual) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + ">, got <" + actual + ">");
        }
    }

    private static <T extends Throwable> T expect(Class<T> type, Body body, String what) throws Exception {
        try {
            body.run();
        } catch (Throwable thrown) {
            if (type.isInstance(thrown)) {
                return type.cast(thrown);
            }
            if (thrown instanceof Exception checked) {
                throw new AssertionError(what + ": expected " + type.getSimpleName() + ", got " + thrown, checked);
            }
            throw thrown;
        }
        throw new AssertionError(what + ": expected " + type.getSimpleName() + ", nothing was thrown");
    }

    private static void pass(String description) {
        checks++;
        System.out.println("ok " + checks + " - " + description);
    }

    @FunctionalInterface
    private interface Body {
        void run() throws IOException, InterruptedException;
    }
}
