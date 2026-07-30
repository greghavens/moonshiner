import java.net.URI;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;

public final class TestMain {
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final String COMMIT =
            "c3f3b52c845dd967cabbc21680e893292077d5ba";
    private static final String SPEC =
            "specifications/vsphere/openapi/automation/vcenter.yaml";

    public static void main(String[] args) throws Exception {
        validateProtectedProvenance();
        validateConstructorBoundary();
        validateExpiryResumeAndStableOrdering();
        System.out.println(
                "PASS: contract-pinned Supervisor token refresh preserves work");
    }

    private static void validateProtectedProvenance() throws Exception {
        String contract = Files.readString(CONTRACT, StandardCharsets.UTF_8);
        String sources = Files.readString(
                Path.of("docs", "official_sources.json"),
                StandardCharsets.UTF_8);
        require(contract.contains("\"repositoryCommitSha\": \"" + COMMIT + "\""),
                "contract is not pinned to the expected repository commit");
        require(sources.contains("\"repositoryCommitSha\": \"" + COMMIT + "\""),
                "official sources are not pinned to the expected repository commit");
        require(contract.contains("\"specPath\": \"" + SPEC + "\""),
                "contract records the wrong specification path");
        require(sources.contains("\"specPath\": \"" + SPEC + "\""),
                "official sources record the wrong specification path");
        require(occurrences(contract,
                        "\"operationId\": \""
                                + ContractMockServer.VCENTER_OPERATION + "\"") == 1,
                "contract must contain exactly one VMware operationId");
        require(occurrences(sources,
                        "\"" + ContractMockServer.VCENTER_OPERATION + "\"") >= 2,
                "official sources must record every VMware operationId");
        require(occurrences(contract,
                        "\"operationKey\": \""
                                + ContractMockServer.KUBERNETES_OPERATION + "\"") == 1,
                "contract must separately name the Kubernetes resource operation");
        require(!contract.contains(
                        "\"operationId\": \""
                                + ContractMockServer.KUBERNETES_OPERATION + "\""),
                "contract must not invent a VMware operationId for Kubernetes");
    }

    private static void validateConstructorBoundary() {
        HttpClient client = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
        VcfVksInventoryClient.Credentials credentials =
                new VcfVksInventoryClient.Credentials("session", "token");
        VcfVksInventoryClient.CredentialRefresher refresher =
                expired -> credentials;
        Duration timeout = Duration.ofSeconds(2);

        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("ftp://127.0.0.1/api"),
                credentials, refresher, timeout, client));
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://127.0.0.1/"),
                credentials, refresher, timeout, client));
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://user@127.0.0.1/api"),
                credentials, refresher, timeout, client));
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://127.0.0.1/api?filter="),
                credentials, refresher, timeout, client));
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://127.0.0.1/api"),
                new VcfVksInventoryClient.Credentials(
                        "bad\nsession", "token"),
                refresher, timeout, client));
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://127.0.0.1/api"),
                new VcfVksInventoryClient.Credentials("session", " "),
                refresher, timeout, client));
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://127.0.0.1/api"),
                credentials, refresher, Duration.ZERO, client));
        HttpClient redirecting = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.ALWAYS)
                .build();
        expectIllegalArgument(() -> new VcfVksInventoryClient(
                URI.create("http://127.0.0.1/api"),
                credentials, refresher, timeout, redirecting));
    }

    private static void validateExpiryResumeAndStableOrdering()
            throws Exception {
        String suffix = UUID.randomUUID().toString().replace("-", "");
        ContractMockServer.Fixture fixture = new ContractMockServer.Fixture(
                "vc-session-old-" + UUID.randomUUID(),
                "vc-session-new-" + UUID.randomUUID(),
                "k8s-old-" + UUID.randomUUID(),
                "k8s-new-" + UUID.randomUUID(),
                "team-a-" + suffix.substring(0, 10),
                "team-z-" + suffix.substring(10, 20),
                suffix.substring(20));

        try (ContractMockServer server =
                     new ContractMockServer(CONTRACT, fixture)) {
            AtomicInteger refreshes = new AtomicInteger();
            VcfVksInventoryClient.Credentials initial =
                    new VcfVksInventoryClient.Credentials(
                            fixture.oldVcenterSession(),
                            fixture.oldAccessToken());
            VcfVksInventoryClient.Credentials replacement =
                    new VcfVksInventoryClient.Credentials(
                            fixture.newVcenterSession(),
                            fixture.newAccessToken());
            VcfVksInventoryClient client = new VcfVksInventoryClient(
                    server.vcenterApiBase(),
                    initial,
                    expired -> {
                        require(expired.equals(initial),
                                "refresher did not receive the expired generation");
                        refreshes.incrementAndGet();
                        return replacement;
                    },
                    Duration.ofSeconds(3),
                    server.client());

            List<VcfVksInventoryClient.ClusterRecord> expected =
                    expectedInventory(server, fixture);
            List<VcfVksInventoryClient.ClusterRecord> first =
                    client.listInventory();
            require(first.equals(expected),
                    "first inventory is incomplete or not stably sorted");
            assertUnmodifiable(first);
            require(refreshes.get() == 1,
                    "the expired access token must be refreshed exactly once");
            assertFirstRunLog(server, fixture);

            // The mock reverses namespace and Cluster response order between
            // successful calls. Replacement credentials must be retained.
            List<VcfVksInventoryClient.ClusterRecord> second =
                    client.listInventory();
            require(second.equals(expected),
                    "second inventory changed with opposite service order");
            require(refreshes.get() == 1,
                    "replacement credentials were not retained");
            assertCompleteLog(server, fixture);
        }
    }

    private static List<VcfVksInventoryClient.ClusterRecord> expectedInventory(
            ContractMockServer server,
            ContractMockServer.Fixture fixture) {
        List<VcfVksInventoryClient.ClusterRecord> result = new ArrayList<>();
        for (String namespace : fixture.namespaces()) {
            String prefix = namespace.equals(fixture.namespaceA())
                    ? "alpha" : "zeta";
            for (String letter : List.of("a", "z")) {
                String name = prefix + "-" + letter + "-" + fixture.suffix();
                result.add(new VcfVksInventoryClient.ClusterRecord(
                        namespace,
                        server.origin(),
                        name,
                        "uid-" + name,
                        letter.equals("a")
                                ? "v1.32.4+vmware.1"
                                : "v1.33.1+vmware.2",
                        letter.equals("a") ? "Provisioned" : "Running"));
            }
        }
        return List.copyOf(result);
    }

    private static void assertUnmodifiable(
            List<VcfVksInventoryClient.ClusterRecord> inventory) {
        boolean immutable = false;
        try {
            inventory.add(inventory.get(0));
        } catch (UnsupportedOperationException expected) {
            immutable = true;
        }
        require(immutable, "inventory must be unmodifiable");
    }

    private static void assertFirstRunLog(
            ContractMockServer server,
            ContractMockServer.Fixture fixture) {
        List<ContractMockServer.RequestLog> log = server.requests();
        require(log.size() == 4,
                "first inventory must make exactly four requests");
        require(log.get(0).operation().equals(
                        ContractMockServer.VCENTER_OPERATION),
                "vCenter namespace discovery was not first");
        require(log.get(1).operation().equals(
                        ContractMockServer.KUBERNETES_OPERATION)
                        && log.get(2).operation().equals(
                                ContractMockServer.KUBERNETES_OPERATION)
                        && log.get(3).operation().equals(
                                ContractMockServer.KUBERNETES_OPERATION),
                "an out-of-contract operation was invoked");

        assertVcenterRequest(
                log.get(0), server.vcenterPath(), fixture.oldVcenterSession());
        assertKubernetesRequest(
                log.get(1),
                server.kubernetesPath(fixture.namespaceA()),
                fixture.oldAccessToken());
        assertKubernetesRequest(
                log.get(2),
                server.kubernetesPath(fixture.namespaceZ()),
                fixture.oldAccessToken());
        assertKubernetesRequest(
                log.get(3),
                server.kubernetesPath(fixture.namespaceZ()),
                fixture.newAccessToken());
        require(log.get(0).responseStatus() == 200
                        && log.get(1).responseStatus() == 200
                        && log.get(2).responseStatus() == 401
                        && log.get(3).responseStatus() == 200,
                "fixture did not exercise the intended expiry transition");
        require(log.get(2).rawTarget().equals(log.get(3).rawTarget()),
                "retry did not preserve the interrupted raw target");
        long firstNamespaceCalls = log.stream()
                .filter(request -> request.rawTarget().equals(
                        server.kubernetesPath(fixture.namespaceA())))
                .count();
        require(firstNamespaceCalls == 1,
                "completed first-namespace work was repeated after expiry");
    }

    private static void assertCompleteLog(
            ContractMockServer server,
            ContractMockServer.Fixture fixture) {
        List<ContractMockServer.RequestLog> log = server.requests();
        require(log.size() == 7,
                "two inventory calls must make seven total requests");
        assertFirstRunLogView(server, fixture, log.subList(0, 4));
        assertVcenterRequest(
                log.get(4), server.vcenterPath(), fixture.newVcenterSession());
        assertKubernetesRequest(
                log.get(5),
                server.kubernetesPath(fixture.namespaceA()),
                fixture.newAccessToken());
        assertKubernetesRequest(
                log.get(6),
                server.kubernetesPath(fixture.namespaceZ()),
                fixture.newAccessToken());
        require(log.stream().allMatch(request -> request.operation() != null),
                "mock request log contains an unnamed route");
        long vcenterCalls = log.stream()
                .filter(request -> ContractMockServer.VCENTER_OPERATION.equals(
                        request.operation()))
                .count();
        require(vcenterCalls == 2,
                "vCenter operation count proves completed work was restarted");
    }

    private static void assertFirstRunLogView(
            ContractMockServer server,
            ContractMockServer.Fixture fixture,
            List<ContractMockServer.RequestLog> log) {
        require(log.size() == 4, "invalid first-run log view");
        assertVcenterRequest(
                log.get(0), server.vcenterPath(), fixture.oldVcenterSession());
        assertKubernetesRequest(
                log.get(1),
                server.kubernetesPath(fixture.namespaceA()),
                fixture.oldAccessToken());
        assertKubernetesRequest(
                log.get(2),
                server.kubernetesPath(fixture.namespaceZ()),
                fixture.oldAccessToken());
        assertKubernetesRequest(
                log.get(3),
                server.kubernetesPath(fixture.namespaceZ()),
                fixture.newAccessToken());
    }

    private static void assertVcenterRequest(
            ContractMockServer.RequestLog request,
            String expectedPath,
            String session) {
        assertBodylessGet(request, expectedPath);
        require(List.of(session).equals(
                        request.headerValues("vmware-api-session-id")),
                "vCenter request used the wrong session generation");
        require(request.headerValues("Authorization").isEmpty(),
                "vCenter request leaked the Kubernetes bearer token");
    }

    private static void assertKubernetesRequest(
            ContractMockServer.RequestLog request,
            String expectedPath,
            String token) {
        assertBodylessGet(request, expectedPath);
        require(List.of("Bearer " + token).equals(
                        request.headerValues("Authorization")),
                "Kubernetes request used the wrong bearer generation");
        require(request.headerValues("vmware-api-session-id").isEmpty(),
                "Kubernetes request leaked the vCenter session id");
    }

    private static void assertBodylessGet(
            ContractMockServer.RequestLog request,
            String expectedPath) {
        require(request.method().equals("GET"), "request method is not GET");
        require(request.rawTarget().equals(expectedPath),
                "raw target differs from the contract-derived target");
        require(!request.rawTarget().contains("?"),
                "unset optional fields or a bare query delimiter were sent");
        require(request.body().length == 0, "GET request body is not empty");
        require(List.of("application/json").equals(
                        request.headerValues("Accept")),
                "request must send exactly one JSON Accept header");
        require(request.headerValues("Content-Type").isEmpty(),
                "bodyless request must omit Content-Type");
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

    static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
