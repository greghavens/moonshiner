import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;

/**
 * Protected integration harness for the single-file client.
 */
public final class TestMain {
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(3);

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain BASE_URL REQUEST_LOG RETIRED_MARKER");
        }
        Path requestLog = Path.of(args[1]);
        Path retiredMarker = Path.of(args[2]);

        NsxPolicyClient.Credentials oldCredential = new NsxPolicyClient.Credentials(
                environment("NSX_OLD_USERNAME"),
                environment("NSX_OLD_PASSWORD"));
        NsxPolicyClient.Credentials newCredential = new NsxPolicyClient.Credentials(
                environment("NSX_NEW_USERNAME"),
                environment("NSX_NEW_PASSWORD"));
        String centralCursor = environment("NSX_CENTRAL_CURSOR");

        validateConstruction(args[0], oldCredential);
        NsxPolicyClient client = new NsxPolicyClient(
                args[0],
                oldCredential,
                REQUEST_TIMEOUT);
        require(client.credentialGeneration() == 1, "generation must start at 1");

        expectIllegalArgument(() -> client.listTier1s("   ", null));
        expectIllegalArgument(() -> client.listTier1s(null, -1L));
        expectIllegalArgument(() -> client.listTier1s(null, 1001L));
        AtomicInteger invalidRetirement = new AtomicInteger();
        expectIllegalArgument(() -> client.rotateCredentials(
                newCredential,
                Duration.ZERO,
                invalidRetirement::incrementAndGet));
        expectNullPointer(() -> client.rotateCredentials(
                newCredential,
                Duration.ofSeconds(1),
                null));
        expectIllegalArgument(() -> client.rotateCredentials(
                oldCredential,
                Duration.ofSeconds(1),
                invalidRetirement::incrementAndGet));
        require(invalidRetirement.get() == 0, "invalid rotation ran callback");
        require(client.credentialGeneration() == 1, "invalid rotation changed state");
        require(requestCount(requestLog) == 0, "validation produced network traffic");

        ExecutorService executor = Executors.newFixedThreadPool(2);
        try {
            Future<String> oldRequest = executor.submit(
                    () -> client.listTier1s(null, null));
            waitFor(
                    () -> requestCount(requestLog) >= 1,
                    Duration.ofSeconds(4),
                    "old request never reached the loopback mock");

            Future<NsxPolicyClient.RotationResult> rotation = executor.submit(
                    () -> client.rotateCredentials(
                            newCredential,
                            Duration.ofSeconds(5),
                            () -> appendRetiredEvent(requestLog, retiredMarker)));
            waitFor(
                    () -> client.credentialGeneration() == 2,
                    Duration.ofSeconds(3),
                    "replacement generation was not published");
            require(!rotation.isDone(), "rotation retired an in-use credential");
            require(!Files.exists(retiredMarker), "old credential retired too early");

            String newBody = client.listTier1s(centralCursor, 0L);
            require(newBody.contains("\"central-new\""), "wrong new response");
            String oldBody = oldRequest.get(6, TimeUnit.SECONDS);
            require(oldBody.contains("\"central-old\""), "old request was stranded");
            NsxPolicyClient.RotationResult result = rotation.get(
                    6, TimeUnit.SECONDS);
            require(result.oldGeneration() == 1, "wrong old generation");
            require(result.newGeneration() == 2, "wrong new generation");
            require(result.retired(), "successful rotation was not retired");
            require(Files.exists(retiredMarker), "retirement callback did not run");

            inspectCentralRequests(
                    requestLog, oldCredential, newCredential);
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(3, TimeUnit.SECONDS);
        }

        exerciseTimeoutAndHttpError(args[0], requestLog);
        System.out.println("TEST_MAIN_OK");
    }

    private static void exerciseTimeoutAndHttpError(
            String baseUrl,
            Path requestLog) throws Exception {
        NsxPolicyClient.Credentials oldCredential = new NsxPolicyClient.Credentials(
                environment("NSX_TIMEOUT_OLD_USERNAME"),
                environment("NSX_TIMEOUT_OLD_PASSWORD"));
        NsxPolicyClient.Credentials newCredential = new NsxPolicyClient.Credentials(
                environment("NSX_TIMEOUT_NEW_USERNAME"),
                environment("NSX_TIMEOUT_NEW_PASSWORD"));
        String timeoutCursor = environment("NSX_TIMEOUT_CURSOR");
        String releaseCursor = environment("NSX_RELEASE_CURSOR");
        String errorCursor = environment("NSX_ERROR_CURSOR");

        NsxPolicyClient client = new NsxPolicyClient(
                baseUrl, oldCredential, REQUEST_TIMEOUT);
        ExecutorService executor = Executors.newSingleThreadExecutor();
        AtomicInteger retirementCount = new AtomicInteger();
        try {
            int before = requestCount(requestLog);
            Future<String> held = executor.submit(
                    () -> client.listTier1s(timeoutCursor, null));
            waitFor(
                    () -> requestCount(requestLog) >= before + 1,
                    Duration.ofSeconds(4),
                    "timeout request never reached the loopback mock");

            NsxPolicyClient.RotationTimeoutException timeout = null;
            try {
                client.rotateCredentials(
                        newCredential,
                        Duration.ofMillis(180),
                        retirementCount::incrementAndGet);
            } catch (NsxPolicyClient.RotationTimeoutException expected) {
                timeout = expected;
            }
            require(timeout != null, "rotation did not time out");
            require(timeout.oldGeneration() == 1, "timeout old generation");
            require(timeout.newGeneration() == 2, "timeout new generation");
            require(timeout.pendingRequests() == 1, "timeout pending count");
            require(retirementCount.get() == 0, "timed-out rotation retired old");
            require(client.credentialGeneration() == 2, "timeout rolled back new");

            String replacementBody = client.listTier1s(releaseCursor, null);
            require(
                    replacementBody.contains("\"timeout-new\""),
                    "new generation was not usable after timeout");
            String heldBody = held.get(6, TimeUnit.SECONDS);
            require(
                    heldBody.contains("\"timeout-old\""),
                    "timed-out old request did not finish");

            int requestsBeforeError = requestCount(requestLog);
            try {
                client.listTier1s(errorCursor, null);
                throw new AssertionError("503 response was accepted");
            } catch (NsxPolicyClient.NsxPolicyException expected) {
                require(expected.statusCode() == 503, "wrong HTTP error status");
                require(
                        expected.responseBody().contains("\"error_code\":50301"),
                        "HTTP error body was not preserved");
            }
            require(
                    requestCount(requestLog) == requestsBeforeError + 1,
                    "ListTier1 HTTP error was retried");

            NsxPolicyClient.Credentials callbackFailureCredential =
                    new NsxPolicyClient.Credentials(
                            "callback-" + newCredential.username(),
                            "callback-" + newCredential.password());
            RuntimeException callbackFailure = new RuntimeException(
                    "runtime retirement failure");
            try {
                client.rotateCredentials(
                        callbackFailureCredential,
                        Duration.ofSeconds(1),
                        () -> {
                            throw callbackFailure;
                        });
                throw new AssertionError("callback failure was swallowed");
            } catch (RuntimeException expected) {
                require(expected == callbackFailure, "callback failure changed");
            }
            require(
                    client.credentialGeneration() == 3,
                    "callback failure rolled back replacement");
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(3, TimeUnit.SECONDS);
        }
    }

    private static void validateConstruction(
            String baseUrl,
            NsxPolicyClient.Credentials credentials) throws Exception {
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl + "/policy/api/v1",
                credentials,
                REQUEST_TIMEOUT));
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl + "?query",
                credentials,
                REQUEST_TIMEOUT));
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl.replace("http://", "http://embedded@"),
                credentials,
                REQUEST_TIMEOUT));
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl,
                new NsxPolicyClient.Credentials(" ", "password"),
                REQUEST_TIMEOUT));
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl,
                new NsxPolicyClient.Credentials("bad:user", "password"),
                REQUEST_TIMEOUT));
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl,
                new NsxPolicyClient.Credentials("user", " "),
                REQUEST_TIMEOUT));
        expectIllegalArgument(() -> new NsxPolicyClient(
                baseUrl,
                credentials,
                Duration.ZERO));
    }

    private static void inspectCentralRequests(
            Path requestLog,
            NsxPolicyClient.Credentials oldCredential,
            NsxPolicyClient.Credentials newCredential) throws Exception {
        List<String> requests = Files.readAllLines(
                        requestLog, StandardCharsets.UTF_8)
                .stream()
                .filter(line -> line.contains("\"event\":\"request\""))
                .toList();
        require(requests.size() == 2, "central scenario request count");
        require(
                requests.get(0).contains("\"operationId\":\"ListTier1\""),
                "old request operationId");
        require(
                requests.get(0).contains(
                        "\"raw_target\":\"/policy/api/v1/infra/tier-1s\""),
                "unset query was not omitted");
        require(
                requests.get(0).contains(authorization(oldCredential)),
                "old request did not use old generation");
        require(
                requests.get(1).contains(authorization(newCredential)),
                "new request did not use new generation");
    }

    private static String authorization(NsxPolicyClient.Credentials credential) {
        String value = credential.username() + ":" + credential.password();
        return "Basic " + Base64.getEncoder().encodeToString(
                value.getBytes(StandardCharsets.UTF_8));
    }

    private static void appendRetiredEvent(Path log, Path marker) {
        try {
            Files.writeString(
                    marker,
                    "retired\n",
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE_NEW,
                    StandardOpenOption.WRITE);
            byte[] line = "{\"event\":\"retired\"}\n".getBytes(
                    StandardCharsets.UTF_8);
            try (FileChannel channel = FileChannel.open(
                    log,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.APPEND)) {
                channel.write(ByteBuffer.wrap(line));
                channel.force(true);
            }
        } catch (java.io.IOException exception) {
            throw new UncheckedIOException(exception);
        }
    }

    private static int requestCount(Path log) {
        try {
            if (!Files.exists(log)) {
                return 0;
            }
            return (int) Files.readAllLines(log, StandardCharsets.UTF_8)
                    .stream()
                    .filter(line -> line.contains("\"event\":\"request\""))
                    .count();
        } catch (java.io.IOException exception) {
            throw new UncheckedIOException(exception);
        }
    }

    private static void waitFor(
            BooleanSupplier condition,
            Duration timeout,
            String failure) throws InterruptedException {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (!condition.getAsBoolean()) {
            if (System.nanoTime() - deadline >= 0) {
                throw new AssertionError(failure);
            }
            Thread.sleep(10);
        }
    }

    private static void expectIllegalArgument(ThrowingRunnable action)
            throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("expected IllegalArgumentException");
    }

    private static void expectNullPointer(ThrowingRunnable action)
            throws Exception {
        try {
            action.run();
        } catch (NullPointerException expected) {
            return;
        }
        throw new AssertionError("expected NullPointerException");
    }

    private static String environment(String name) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) {
            throw new IllegalStateException("missing environment value " + name);
        }
        return value;
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
