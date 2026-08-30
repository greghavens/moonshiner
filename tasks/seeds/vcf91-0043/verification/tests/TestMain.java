import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Fixed integration harness. The protected verifier supplies a loopback origin,
 * runtime-generated credentials and a fixture marker for deterministic cutover.
 */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 6) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-origin> <old-access-token>"
                            + " <refresh-token-id> <new-access-token>"
                            + " <resource-name> <first-request-marker>");
        }

        VcfSessionClient client = new VcfSessionClient(
                URI.create(args[0]),
                args[1],
                args[2],
                Duration.ofSeconds(3));

        expectIllegalArgument(
                () -> client.getCredentials(new VcfSessionClient.CredentialQuery(
                        null, null, "", null, null, null, null)),
                "an explicitly empty optional must be rejected before traffic");

        ExecutorService executor = Executors.newSingleThreadExecutor();
        try {
            Future<String> inFlight = executor.submit(() -> client.getCredentials(
                    new VcfSessionClient.CredentialQuery(
                            null, null, null, null, null, null, null)));

            waitForMarker(Path.of(args[5]), Duration.ofSeconds(4));
            String refreshed = client.refreshAccessToken();
            require(args[3].equals(refreshed), "refresh returned the wrong bearer");

            String firstBody = get(inFlight);
            require(
                    firstBody.contains("credential-after-cutover"),
                    "the in-flight GET was stranded on the old bearer");

            String filteredBody = client.getCredentials(
                    new VcfSessionClient.CredentialQuery(
                            args[4], null, null, null, null, null, null));
            require(
                    filteredBody.contains("credential-after-cutover"),
                    "the post-cutover filtered GET failed");
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(2, TimeUnit.SECONDS);
        }

        System.out.println("SUCCESSFUL");
    }

    private static String get(Future<String> future) throws Exception {
        try {
            return future.get(6, TimeUnit.SECONDS);
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof Exception exception) {
                throw exception;
            }
            throw error;
        }
    }

    private static void waitForMarker(Path marker, Duration timeout)
            throws Exception {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (System.nanoTime() < deadline) {
            if (Files.exists(marker)) {
                return;
            }
            Thread.sleep(10);
        }
        throw new AssertionError("the first credentials request did not start");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void expectIllegalArgument(
            ThrowingAction action,
            String message) throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
