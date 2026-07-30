import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.Set;
import java.util.UUID;

public final class TestMain {
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final String COMMIT =
            "c3f3b52c845dd967cabbc21680e893292077d5ba";

    public static void main(String[] args) throws Exception {
        validateProtectedProvenance();
        validateConstructor();
        validateRefreshResumeAndStableOrdering();
        System.out.println("PASS: contract-pinned access-token resume and ordering");
    }

    private static void validateProtectedProvenance() throws Exception {
        String contract = Files.readString(CONTRACT, StandardCharsets.UTF_8);
        String sources = Files.readString(
                Path.of("docs", "official_sources.json"), StandardCharsets.UTF_8);
        require(contract.contains("\"repository_commit_sha\": \"" + COMMIT + "\""),
                "contract is not pinned to the expected repository commit");
        require(sources.contains("\"repository_commit_sha\": \"" + COMMIT + "\""),
                "official sources are not pinned to the expected repository commit");
        require(contract.contains(
                        "\"spec_path\": \"specifications/vsphere/openapi/automation/vcenter.yaml\""),
                "contract has the wrong specification path");
        for (String operation : Set.of(
                MockVcenterServer.TOKEN_ISSUE,
                MockVcenterServer.VM_LIST,
                MockVcenterServer.HOST_LIST)) {
            require(occurrences(contract, "\"operationId\": \"" + operation + "\"") == 1,
                    "contract must contain exactly one " + operation);
            require(occurrences(sources, "\"" + operation + "\"") >= 2,
                    "official sources must record " + operation);
        }
    }

    private static void validateConstructor() {
        String token = "token";
        Duration timeout = Duration.ofSeconds(2);
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("ftp://example.test"), token, token, token, timeout));
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("http://example.test/api"), token, token, token, timeout));
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("http://user@example.test"), token, token, token, timeout));
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("http://example.test?query=1"), token, token, token, timeout));
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("http://example.test"), "bad\nheader", token, token, timeout));
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("http://example.test"), token, " ", token, timeout));
        expectIllegalArgument(() -> new VcenterInventoryClient(
                URI.create("http://example.test"), token, token, token, Duration.ZERO));
    }

    private static void validateRefreshResumeAndStableOrdering() throws Exception {
        String suffix = UUID.randomUUID().toString().replace("-", "");
        MockVcenterServer.Fixture fixture = new MockVcenterServer.Fixture(
                "access-old-" + UUID.randomUUID(),
                "access-new-" + UUID.randomUUID(),
                "subject+" + UUID.randomUUID() + "/=",
                "urn:ietf:params:oauth:token-type:jwt",
                suffix);

        try (MockVcenterServer server = new MockVcenterServer(CONTRACT, fixture)) {
            VcenterInventoryClient client = new VcenterInventoryClient(
                    server.origin(),
                    fixture.initialAccessToken(),
                    fixture.subjectToken(),
                    fixture.subjectTokenType(),
                    Duration.ofSeconds(3),
                    server.client());

            VcenterInventoryClient.Inventory expected = expectedInventory(suffix);
            VcenterInventoryClient.Inventory first = client.collect();
            require(first.equals(expected),
                    "first inventory is incomplete or not sorted by identifier");
            assertImmutable(first);
            assertFirstRunLog(server.requests(), fixture);

            // The mock returns the opposite element order for the second
            // successful response from each collection operation.
            VcenterInventoryClient.Inventory second = client.collect();
            require(second.equals(expected),
                    "second inventory changed when the service flipped element order");
            assertCompleteLog(server.requests(), fixture);
        }
    }

    private static VcenterInventoryClient.Inventory expectedInventory(String suffix) {
        return new VcenterInventoryClient.Inventory(
                List.of(
                        new VcenterInventoryClient.VM(
                                "vm-a-" + suffix,
                                "alpha",
                                "POWERED_ON",
                                null,
                                null),
                        new VcenterInventoryClient.VM(
                                "vm-m-" + suffix,
                                "middle \"quoted\"",
                                "SUSPENDED",
                                4L,
                                8192L),
                        new VcenterInventoryClient.VM(
                                "vm-z-" + suffix,
                                "zeta",
                                "POWERED_OFF",
                                8L,
                                16384L)),
                List.of(
                        new VcenterInventoryClient.Host(
                                "host-a-" + suffix,
                                "esx-a.example.test",
                                "CONNECTED",
                                null,
                                null),
                        new VcenterInventoryClient.Host(
                                "host-m-" + suffix,
                                "esx-m.example.test",
                                "NOT_RESPONDING",
                                null,
                                null),
                        new VcenterInventoryClient.Host(
                                "host-z-" + suffix,
                                "esx-z.example.test",
                                "DISCONNECTED",
                                "POWERED_OFF",
                                "uuid-" + suffix)));
    }

    private static void assertImmutable(VcenterInventoryClient.Inventory inventory) {
        boolean vmImmutable = false;
        try {
            inventory.vms().add(inventory.vms().get(0));
        } catch (UnsupportedOperationException expected) {
            vmImmutable = true;
        }
        boolean hostImmutable = false;
        try {
            inventory.hosts().clear();
        } catch (UnsupportedOperationException expected) {
            hostImmutable = true;
        }
        require(vmImmutable && hostImmutable, "inventory collections must be immutable");
    }

    private static void assertFirstRunLog(
            List<MockVcenterServer.RequestLog> log,
            MockVcenterServer.Fixture fixture) {
        require(log.size() == 4,
                "first collect must make exactly four requests");
        String[] operations = {
                MockVcenterServer.VM_LIST,
                MockVcenterServer.HOST_LIST,
                MockVcenterServer.TOKEN_ISSUE,
                MockVcenterServer.HOST_LIST
        };
        String[] methods = {"GET", "GET", "POST", "GET"};
        String[] targets = {
                "/api/vcenter/vm",
                "/api/vcenter/host",
                "/api/vcenter/authentication/token",
                "/api/vcenter/host"
        };
        for (int i = 0; i < operations.length; i++) {
            MockVcenterServer.RequestLog request = log.get(i);
            require(operations[i].equals(request.operationId()),
                    "request " + i + " used an out-of-order or out-of-contract operation");
            require(methods[i].equals(request.method()),
                    "request " + i + " used the wrong method");
            require(targets[i].equals(request.rawTarget()),
                    "request " + i + " used the wrong raw target");
        }

        assertCollectionRequest(log.get(0), fixture.initialAccessToken());
        assertCollectionRequest(log.get(1), fixture.initialAccessToken());
        assertTokenRequest(log.get(2), fixture);
        assertCollectionRequest(log.get(3), fixture.replacementAccessToken());

        long vmCalls = log.stream()
                .filter(request -> MockVcenterServer.VM_LIST.equals(request.operationId()))
                .count();
        require(vmCalls == 1,
                "the completed VM collection was fetched again after token expiry");
    }

    private static void assertCompleteLog(
            List<MockVcenterServer.RequestLog> log,
            MockVcenterServer.Fixture fixture) {
        require(log.size() == 6,
                "two collect calls must make six total requests");
        assertFirstRunLog(log.subList(0, 4), fixture);
        require(MockVcenterServer.VM_LIST.equals(log.get(4).operationId()),
                "second collect must retrieve VMs first");
        require(MockVcenterServer.HOST_LIST.equals(log.get(5).operationId()),
                "second collect must retrieve hosts second");
        assertCollectionRequest(log.get(4), fixture.replacementAccessToken());
        assertCollectionRequest(log.get(5), fixture.replacementAccessToken());
        long refreshes = log.stream()
                .filter(request -> MockVcenterServer.TOKEN_ISSUE.equals(request.operationId()))
                .count();
        require(refreshes == 1, "the access token must be exchanged exactly once");
        require(log.stream().allMatch(request -> request.operationId() != null),
                "the client contacted a route outside docs/contract.json");
    }

    private static void assertCollectionRequest(
            MockVcenterServer.RequestLog request, String accessToken) {
        require(request.body().length == 0, "collection request body must be empty");
        require(List.of("application/json").equals(request.headerValues("Accept")),
                "collection request must send exactly one JSON Accept header");
        require(List.of(accessToken).equals(
                        request.headerValues("vmware-api-session-id")),
                "collection request used the wrong access token header");
        require(request.headerValues("Authorization").isEmpty(),
                "collection request must not send Authorization");
        require(request.headerValues("Content-Type").isEmpty(),
                "collection request must not send Content-Type");
        require(!request.rawTarget().contains("?"),
                "collection request must omit all optional query fields");
    }

    private static void assertTokenRequest(
            MockVcenterServer.RequestLog request,
            MockVcenterServer.Fixture fixture) {
        require(request.headerValues("vmware-api-session-id").isEmpty(),
                "token request must not send an API session header");
        require(List.of("Bearer " + fixture.subjectToken()).equals(
                        request.headerValues("Authorization")),
                "token request used the wrong federated Bearer credential");
        require(List.of("application/json").equals(request.headerValues("Accept")),
                "token request must send exactly one JSON Accept header");
        require(List.of("application/x-www-form-urlencoded").equals(
                        request.headerValues("Content-Type")),
                "token request must send the contract form media type");
        String expectedBody = "grant_type="
                + formEncode(MockVcenterServer.GRANT_TYPE)
                + "&subject_token=" + formEncode(fixture.subjectToken())
                + "&subject_token_type=" + formEncode(fixture.subjectTokenType());
        require(new String(request.body(), StandardCharsets.UTF_8).equals(expectedBody),
                "token exchange form fields, order, or encoding are wrong");
    }

    private static String formEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
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
