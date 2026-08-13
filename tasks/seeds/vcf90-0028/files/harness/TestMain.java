import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Drives {@link VcfNetworkPoolClient} against four loopback mock appliances and records every
 * result, request and final appliance state under {@code out/}.
 *
 * <ol>
 *   <li>The ambiguous scenario commits a create and answers 502, then invokes the reconciliation
 *       again against the same state. The client must recover and never duplicate the pool.</li>
 *   <li>The retryable scenario answers the first create with 503 without committing it. The client
 *       must re-read state before making the second create attempt.</li>
 *   <li>The exhaustion scenario always answers create with 503. The client must make only a small
 *       bounded number of read-preceded attempts and then throw.</li>
 *   <li>The permanent scenario rejects a valid create with 400. The client must throw and must not
 *       retry the permanent failure.</li>
 * </ol>
 *
 * <p>Harness file. Do not modify.
 */
public final class TestMain {

    private static final long WATCHDOG_SECONDS = 60L;
    private static final AtomicBoolean finished = new AtomicBoolean();

    public static void main(String[] args) throws Exception {
        Path outDir = Paths.get("out");
        Files.createDirectories(outDir);
        startWatchdog();

        runScenario(outDir, "ambiguous commit", MockSddcManager.CreateBehavior.COMMIT_THEN_502,
                true);
        runScenario(outDir.resolve("retryable"), "uncommitted retryable failure",
                MockSddcManager.CreateBehavior.REJECT_503_ONCE, false);
        runScenario(outDir.resolve("exhausted"), "exhausted retryable failure",
                MockSddcManager.CreateBehavior.REJECT_503_ALWAYS, false);
        runScenario(outDir.resolve("permanent"), "permanent failure",
                MockSddcManager.CreateBehavior.REJECT_400, false);

        finished.set(true);
        System.out.flush();
        // HttpClient implementations may own non-daemon workers; leave nothing for the JVM to wait
        // on after all mock services have been stopped and all artifacts have been written.
        Runtime.getRuntime().halt(0);
    }

    private static void runScenario(Path scenarioDir, String name,
                                    MockSddcManager.CreateBehavior behavior, boolean callTwice)
            throws Exception {
        Files.createDirectories(scenarioDir);
        MockSddcManager mock = new MockSddcManager(
                Paths.get("docs", "contract.json"), scenarioDir, behavior);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("scenario", name);
        result.put("poolName", Fixture.POOL_NAME);
        try {
            int port = mock.start();
            String baseUrl = "http://127.0.0.1:" + port;
            result.put("baseUrl", baseUrl);
            System.out.println("[harness] " + name + " mock listening on " + baseUrl);

            VcfNetworkPoolClient client =
                    new VcfNetworkPoolClient(baseUrl, Fixture.USERNAME, Fixture.PASSWORD);
            List<VcfNetworkPoolClient.NetworkSpec> networks = Fixture.desiredNetworks();
            String firstId = client.ensureNetworkPool(Fixture.POOL_NAME, networks);
            result.put("status", "ok");
            result.put("firstId", firstId);
            System.out.println("[harness] " + name + " returned " + firstId);
            if (callTwice) {
                String secondId = client.ensureNetworkPool(Fixture.POOL_NAME, networks);
                result.put("secondId", secondId);
                System.out.println("[harness] repeated reconciliation returned " + secondId);
            }
        } catch (Throwable t) {
            result.put("status", "error");
            result.put("errorClass", t.getClass().getName());
            result.put("error", String.valueOf(t.getMessage()));
            System.out.println("[harness] " + name + " client threw " + t);
        } finally {
            mock.stopAndDump();
            Files.writeString(scenarioDir.resolve("result.json"), MiniJson.write(result, true),
                    StandardCharsets.UTF_8);
            printLog(name, scenarioDir.resolve("requests.json"));
        }
    }

    private static void printLog(String name, Path path) throws Exception {
        List<?> log = (List<?>) MiniJson.parse(Files.readString(path, StandardCharsets.UTF_8));
        System.out.println("[harness] " + name + ": " + log.size()
                + " request(s) reached the appliance:");
        for (Object o : log) {
            Map<?, ?> entry = (Map<?, ?>) o;
            System.out.printf("  #%s %s %s -> %s%n", entry.get("seq"), entry.get("method"),
                    entry.get("path"), entry.get("responseStatus"));
        }
    }

    /** Makes an infinite retry loop fail the verification command deterministically. */
    private static void startWatchdog() {
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(WATCHDOG_SECONDS * 1000L);
            } catch (InterruptedException ignored) {
                return;
            }
            if (!finished.get()) {
                System.out.println("[harness] watchdog fired after " + WATCHDOG_SECONDS + "s");
                System.out.flush();
                Runtime.getRuntime().halt(2);
            }
        }, "harness-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    private TestMain() {
    }
}
