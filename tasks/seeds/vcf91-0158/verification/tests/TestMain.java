import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        String base = requiredEnvironment("VCF_VKS_MOCK_BASE");
        String session = requiredEnvironment("VCF_VKS_SESSION");
        String token = requiredEnvironment("VCF_VKS_TOKEN");

        HttpClient httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(2))
                .followRedirects(HttpClient.Redirect.NEVER)
                .version(HttpClient.Version.HTTP_1_1)
                .build();

        VcfVksPagedInventoryClient client =
                new VcfVksPagedInventoryClient(
                        URI.create(base + "/api"),
                        session,
                        token,
                        2,
                        Duration.ofSeconds(3),
                        httpClient);

        List<VcfVksPagedInventoryClient.ClusterRecord> first =
                client.listInventory();
        List<VcfVksPagedInventoryClient.ClusterRecord> second =
                client.listInventory();

        check(first.equals(second),
                "stable inventory changed when service order changed");
        check(first.size() == 6, "not every page was returned");
        check(keys(first).equals(List.of(
                "alpha-team/amber",
                "alpha-team/birch",
                "alpha-team/cobalt",
                "zeta-team/cedar",
                "zeta-team/maple",
                "zeta-team/zenith")),
                "inventory is not in stable namespace/name order");

        for (var row : first) {
            check(row.supervisorEndpoint().equals(URI.create(base)),
                    "wrong Supervisor endpoint");
            check(!row.uid().isBlank(), "missing UID");
            check(!row.kubernetesVersion().isBlank(),
                    "missing Kubernetes version");
            check(!row.phase().isBlank(), "missing phase");
        }

        boolean immutable = false;
        try {
            first.add(first.get(0));
        } catch (UnsupportedOperationException expected) {
            immutable = true;
        }
        check(immutable, "returned inventory must be unmodifiable");

        expectIllegalArgument(() -> new VcfVksPagedInventoryClient(
                URI.create(base + "/api?filter="),
                session, token, 2, Duration.ofSeconds(1), httpClient));
        expectIllegalArgument(() -> new VcfVksPagedInventoryClient(
                URI.create(base + "/api"),
                session, token, 0, Duration.ofSeconds(1), httpClient));
        expectIllegalArgument(() -> new VcfVksPagedInventoryClient(
                URI.create(base + "/api"),
                "bad\nsession", token, 2, Duration.ofSeconds(1), httpClient));

        System.out.println("TEST_MAIN_OK");
    }

    private static List<String> keys(
            List<VcfVksPagedInventoryClient.ClusterRecord> rows) {
        return rows.stream()
                .map(row -> row.supervisorNamespace() + "/" + row.name())
                .toList();
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new AssertionError("missing environment: " + name);
        }
        return value;
    }

    private static void expectIllegalArgument(ThrowingRunnable action)
            throws Exception {
        try {
            action.run();
            throw new AssertionError("expected IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
