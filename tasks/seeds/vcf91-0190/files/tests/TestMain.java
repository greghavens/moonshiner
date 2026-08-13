import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

public final class TestMain {
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path SOURCES = Path.of("docs", "official_sources.json");
    private static final String COMMIT =
            "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";
    private static final String SPEC_PATH =
            "specifications/vcf-operations/log-management-openapi.json";

    public static void main(String[] args) throws Exception {
        validateProtectedProvenance();
        validateConstructor();

        String suffix = UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        MockVcfLogServer.Fixture fixture = new MockVcfLogServer.Fixture(
                "old-access-" + suffix,
                "new-access-" + suffix,
                suffix);
        try (MockVcfLogServer mock = new MockVcfLogServer(CONTRACT, fixture)) {
            validateBeforeTraffic(mock);
            runExpiryScenario(mock, fixture);
        }
        System.out.println("PASS: contract-pinned VCF token refresh without replay");
    }

    private static void validateProtectedProvenance() throws Exception {
        String contract = Files.readString(CONTRACT, StandardCharsets.UTF_8);
        String sources = Files.readString(SOURCES, StandardCharsets.UTF_8);

        require(contract.contains("\"openapi\": \"3.0.1\""),
                "contract lost the official OpenAPI version");
        require(contract.contains("\"title\": \"Log Management API\\n\""),
                "contract lost the official title");
        require(contract.contains("\"version\": \"9.1.0.0\""),
                "contract lost the VCF 9.1 version");
        require(contract.contains("\"commitSha\": \"" + COMMIT + "\""),
                "contract is not pinned to the repository commit");
        require(contract.contains("\"specPath\": \"" + SPEC_PATH + "\""),
                "contract does not record the OpenAPI source path");
        require(contract.contains("\"license\": \"Apache-2.0\""),
                "contract does not record the source license");
        require(occurrences(contract, "\"operationId\"") == 2,
                "contract must expose exactly two operations");
        require(contract.contains("\"operationId\": \"getAllLogForwarders\""),
                "getAllLogForwarders is missing from the contract");
        require(contract.contains("\"operationId\": \"createLogForwarder\""),
                "createLogForwarder is missing from the contract");
        require(contract.contains("\"name\": \"X-JWT-Token\""),
                "contract lost the API-key header name");

        int schema = contract.indexOf("\"LogForwarder\": {");
        require(schema >= 0, "LogForwarder schema is missing");
        int cursor = schema;
        for (String property : List.of(
                "certificate",
                "connectionRefreshInterval",
                "constraints",
                "enabled",
                "forwardComplementaryFields",
                "host",
                "id",
                "name",
                "port",
                "protocol",
                "sslEnabled",
                "tags",
                "transportProtocol",
                "workerCount")) {
            cursor = contract.indexOf("\"" + property + "\"", cursor + 1);
            require(cursor >= 0, "schema property order is not spec-derived: " + property);
        }

        require(sources.contains("\"repositoryCommitSha\": \"" + COMMIT + "\""),
                "official sources lost the repository commit");
        require(sources.contains("\"specPath\": \"" + SPEC_PATH + "\""),
                "official sources lost the specification path");
        for (String operationId : List.of(
                MockVcfLogServer.LIST, MockVcfLogServer.CREATE)) {
            require(sources.contains("\"operationId\": \"" + operationId + "\""),
                    "official sources must record every operationId");
            require(sources.contains(COMMIT),
                    "official sources must record the commit for every operation");
        }
    }

    private static void validateConstructor() throws Exception {
        AtomicInteger calls = new AtomicInteger();
        VcfLogManagementClient.AccessTokenProvider provider = refresh -> {
            calls.incrementAndGet();
            return "unused-token";
        };
        new VcfLogManagementClient(
                URI.create("https://vcf.example/"), provider, Duration.ofSeconds(2));
        require(calls.get() == 0, "construction must obtain no token");

        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("ftp://vcf.example"), provider, Duration.ofSeconds(2)));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("https://user@vcf.example"), provider, Duration.ofSeconds(2)));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("https://vcf.example/api"), provider, Duration.ofSeconds(2)));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("https://vcf.example?mode=test"), provider, Duration.ofSeconds(2)));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("https://vcf.example#fragment"), provider, Duration.ofSeconds(2)));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("https://vcf.example"), provider, Duration.ZERO));
        expectIllegalArgument(() -> new VcfLogManagementClient(
                URI.create("https://vcf.example"), null, Duration.ofSeconds(2)));
    }

    private static void validateBeforeTraffic(MockVcfLogServer mock) throws Exception {
        AtomicInteger invalidCalls = new AtomicInteger();
        VcfLogManagementClient invalidClient = new VcfLogManagementClient(
                mock.origin(),
                refresh -> {
                    invalidCalls.incrementAndGet();
                    return "must-not-be-requested";
                },
                Duration.ofSeconds(3),
                mock.client());
        Map<String, Object> valid = new LinkedHashMap<>();
        valid.put("name", "locally-valid");
        expectIllegalArgument(() -> invalidClient.reconcileForwarders(
                List.of(valid, "not-a-forwarder-object")));
        require(invalidCalls.get() == 0,
                "all desired input must be validated before token acquisition");
        require(mock.requests().isEmpty(),
                "all desired input must be validated before HTTP traffic");

        AtomicInteger blankCalls = new AtomicInteger();
        VcfLogManagementClient blankTokenClient = new VcfLogManagementClient(
                mock.origin(),
                refresh -> {
                    blankCalls.incrementAndGet();
                    return "   ";
                },
                Duration.ofSeconds(3),
                mock.client());
        expectTokenProviderError(blankTokenClient::listForwarders);
        require(blankCalls.get() == 1, "initial token must be requested exactly once");
        require(mock.requests().isEmpty(),
                "an invalid provider result must fail before HTTP traffic");
    }

    private static void runExpiryScenario(
            MockVcfLogServer mock, MockVcfLogServer.Fixture fixture) throws Exception {
        List<Boolean> providerCalls = new ArrayList<>();
        VcfLogManagementClient client = new VcfLogManagementClient(
                mock.origin(),
                forceRefresh -> {
                    providerCalls.add(forceRefresh);
                    return forceRefresh ? fixture.newToken() : fixture.oldToken();
                },
                Duration.ofSeconds(5),
                mock.client());

        String existingName = "archive-" + fixture.suffix();
        String firstName = "primary-" + fixture.suffix();
        String secondName = "backup-" + fixture.suffix();

        Map<String, Object> existing = new LinkedHashMap<>();
        existing.put("name", existingName);

        Map<String, Object> tags = new LinkedHashMap<>();
        tags.put("site", "west-" + fixture.suffix());
        Map<String, Object> first = new LinkedHashMap<>();
        first.put("unknownProperty", "must-not-be-sent");
        first.put("workerCount", 0);
        first.put("transportProtocol", "");
        first.put("tags", tags);
        first.put("sslEnabled", false);
        first.put("protocol", "");
        first.put("port", 0);
        first.put("name", firstName);
        first.put("id", "client-supplied-id");
        first.put("host", "relay-a." + fixture.suffix() + ".example");
        first.put("forwardComplementaryFields", false);
        first.put("enabled", false);
        first.put("constraints", Map.of());
        first.put("connectionRefreshInterval", 0);
        first.put("certificate", "");

        Map<String, Object> second = new LinkedHashMap<>();
        second.put("workerCount", null);
        second.put("transportProtocol", "TCP");
        second.put("tags", Map.of());
        second.put("sslEnabled", true);
        second.put("protocol", "SYSLOG");
        second.put("port", 6514);
        second.put("name", secondName);
        second.put("host", "relay-b." + fixture.suffix() + ".example");
        second.put("forwardComplementaryFields", List.of());
        second.put("enabled", true);
        second.put("constraints", Map.of());
        second.put("certificate", null);

        List<Map<String, Object>> result = client.reconcileForwarders(
                List.of(existing, first, first, second));

        require(providerCalls.equals(List.of(false, true)),
                "provider calls must be exactly initial then forced refresh");
        require(result.size() == 3, "reconciliation must return the complete final list");
        require(existingName.equals(result.get(0).get("name")),
                "existing response objects must stay first");
        require(("preserve-" + fixture.suffix()).equals(
                        result.get(0).get("serverOnly")),
                "existing response fields must not be projected away");
        require(firstName.equals(result.get(1).get("name")),
                "first missing forwarder must be appended first");
        require(secondName.equals(result.get(2).get("name")),
                "second missing forwarder must be appended after refresh");
        require(mock.names().equals(List.of(existingName, firstName, secondName)),
                "server state proves completed work was neither lost nor duplicated");

        String firstBody = "{\"connectionRefreshInterval\":0,\"enabled\":false,"
                + "\"forwardComplementaryFields\":false,\"host\":\"relay-a."
                + fixture.suffix() + ".example\",\"name\":\"" + firstName
                + "\",\"port\":0,\"sslEnabled\":false,\"tags\":{\"site\":\"west-"
                + fixture.suffix() + "\"},\"workerCount\":0}";
        String secondBody = "{\"enabled\":true,\"host\":\"relay-b."
                + fixture.suffix() + ".example\",\"name\":\"" + secondName
                + "\",\"port\":6514,\"protocol\":\"SYSLOG\",\"sslEnabled\":true,"
                + "\"transportProtocol\":\"TCP\"}";
        assertRequestLog(mock.requests(), fixture, firstBody, secondBody);
    }

    private static void assertRequestLog(
            List<MockVcfLogServer.RequestLog> log,
            MockVcfLogServer.Fixture fixture,
            String firstBody,
            String secondBody) {
        require(log.size() == 4,
                "expected list, first create, failed second create, retried second create");
        require(List.of(
                        MockVcfLogServer.LIST,
                        MockVcfLogServer.CREATE,
                        MockVcfLogServer.CREATE,
                        MockVcfLogServer.CREATE)
                .equals(log.stream().map(MockVcfLogServer.RequestLog::operationId).toList()),
                "request sequence must contain only the named contract operations");
        require(List.of("GET", "POST", "POST", "POST")
                        .equals(log.stream().map(MockVcfLogServer.RequestLog::method).toList()),
                "request method order is wrong");
        require(List.of(200, 201, 403, 201)
                        .equals(log.stream().map(MockVcfLogServer.RequestLog::status).toList()),
                "mock did not expire the old token at the intended point");

        for (int index = 0; index < log.size(); index++) {
            MockVcfLogServer.RequestLog request = log.get(index);
            require("/api/v2/logs/forwarders".equals(request.rawTarget()),
                    "raw target must have no query, delimiter, or trailing slash");
            require(request.headerValues("Accept").equals(List.of("application/json")),
                    "every request must send exactly one JSON Accept header");
            require(request.headerValues("Authorization").isEmpty(),
                    "Log Management requests must not send Authorization");
            String expectedToken = index == 3
                    ? fixture.newToken()
                    : fixture.oldToken();
            require(request.headerValues("X-JWT-Token").equals(List.of(expectedToken)),
                    "request used the wrong or duplicate X-JWT-Token header");
        }

        require(log.get(0).body().length == 0,
                "getAllLogForwarders must be bodyless");
        require(log.get(0).headerValues("Content-Type").isEmpty(),
                "getAllLogForwarders must omit Content-Type");
        for (int index = 1; index < log.size(); index++) {
            require(log.get(index).headerValues("Content-Type")
                            .equals(List.of("application/json")),
                    "createLogForwarder must send exactly one JSON Content-Type");
        }

        require(firstBody.equals(text(log.get(1).body())),
                "first POST is not compact schema-order JSON with empty fields omitted");
        require(secondBody.equals(text(log.get(2).body())),
                "interrupted POST is not the expected projected JSON");
        require(Arrays.equals(log.get(2).body(), log.get(3).body()),
                "authentication retry must preserve the exact request body bytes");
        require(!text(log.get(1).body()).contains("client-supplied-id")
                        && !text(log.get(1).body()).contains("unknownProperty")
                        && !text(log.get(1).body()).contains("\"certificate\"")
                        && !text(log.get(1).body()).contains("\"constraints\"")
                        && !text(log.get(1).body()).contains("\"protocol\"")
                        && !text(log.get(1).body()).contains("\"transportProtocol\""),
                "unset, empty, read-only, or unknown fields must be omitted");
        require(occurrences(text(log.get(1).body()), "\"name\"") == 1,
                "completed create work was replayed or malformed");
    }

    private static String text(byte[] body) {
        return new String(body, StandardCharsets.UTF_8);
    }

    private static int occurrences(String text, String needle) {
        int count = 0;
        int at = 0;
        while ((at = text.indexOf(needle, at)) >= 0) {
            count++;
            at += needle.length();
        }
        return count;
    }

    private static void expectIllegalArgument(ThrowingRunnable action) {
        try {
            action.run();
            throw new AssertionError("expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            // expected
        } catch (Exception other) {
            throw new AssertionError("wrong exception type", other);
        }
    }

    private static void expectTokenProviderError(ThrowingRunnable action) {
        try {
            action.run();
            throw new AssertionError("expected TokenProviderException");
        } catch (VcfLogManagementClient.TokenProviderException expected) {
            // expected
        } catch (Exception other) {
            throw new AssertionError("wrong exception type", other);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
