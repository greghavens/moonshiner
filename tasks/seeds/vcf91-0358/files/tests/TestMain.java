import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;

/** Fixed integration harness supplied with runtime-generated mock values. */
public final class TestMain {
    private static final int INVALID_DELETE_CASES = 18;
    private static final int INVALID_GET_CASES = 20;

    public static void main(String[] args) throws Exception {
        if (args.length != 6) {
            throw new IllegalArgumentException(
                    "usage: TestMain <base-uri> <token> <deployment-id>"
                            + " <request-id> <log> <mode>");
        }

        VcfAutomationClient client = new VcfAutomationClient(
                URI.create(args[0]), args[1], Duration.ofSeconds(3));
        String deploymentId = args[2];
        String requestId = args[3];
        Path log = Path.of(args[4]);

        switch (args[5]) {
            case "retry" -> testRetry(
                    client, args[0], deploymentId, requestId, args[1], log);
            case "all-statuses" -> testSuccess(client, deploymentId, requestId, 9, Duration.ZERO);
            case "success-no-completed-at" -> {
                VcfAutomationClient.DeleteResult result = testSuccess(
                        client,
                        deploymentId,
                        requestId,
                        1,
                        Duration.ofSeconds(Long.MAX_VALUE));
                require(result.request().completedAt() == null,
                        "optional completedAt should remain absent");
            }
            case "sleep-check" -> testSleepAfterNonterminal(client, deploymentId, log);
            case "terminal-failures" -> {
                for (int index = 0; index < 3; index++) {
                    expectIOException(
                            () -> client.deleteDeploymentAndWait(
                                    deploymentId, Duration.ofSeconds(Long.MAX_VALUE)),
                            args[1],
                            "terminal failure was reported as success");
                }
            }
            case "invalid-delete" -> {
                for (int index = 0; index < INVALID_DELETE_CASES; index++) {
                    expectIOException(
                            () -> client.deleteDeploymentAndWait(deploymentId, Duration.ZERO),
                            args[1],
                            "invalid DELETE response was accepted at case " + index);
                }
            }
            case "invalid-get" -> {
                for (int index = 0; index < INVALID_GET_CASES; index++) {
                    expectIOException(
                            () -> client.deleteDeploymentAndWait(deploymentId, Duration.ZERO),
                            args[1],
                            "invalid GET response was accepted at case " + index);
                }
            }
            case "non-200" -> {
                expectApiStatus(
                        () -> client.deleteDeploymentAndWait(deploymentId, Duration.ZERO),
                        201,
                        args[1],
                        "non-contract DELETE status was accepted");
                expectApiStatus(
                        () -> client.deleteDeploymentAndWait(deploymentId, Duration.ZERO),
                        202,
                        args[1],
                        "non-contract GET status was accepted");
            }
            default -> throw new AssertionError("unknown test mode " + args[5]);
        }

        System.out.println("SUCCESSFUL");
    }

    private static void testRetry(
            VcfAutomationClient client,
            String baseUri,
            String deploymentId,
            String requestId,
            String token,
            Path log) throws Exception {
        expectApiStatus(
                () -> client.deleteDeploymentAndWait(deploymentId, Duration.ZERO),
                503,
                token,
                "ambiguous first response was reported as success");

        testSuccess(client, deploymentId, requestId, 4, Duration.ZERO);

        String beforeValidation = Files.readString(log);
        expectIllegalArgument(
                () -> client.deleteDeploymentAndWait(" ", Duration.ZERO),
                "blank deployment ID was not rejected");
        expectIllegalArgument(
                () -> client.deleteDeploymentAndWait(deploymentId, Duration.ofMillis(-1)),
                "negative pollInterval was not rejected");
        require(beforeValidation.equals(Files.readString(log)),
                "invalid arguments caused network traffic");

        String invalidToken = token + "\nsecret-suffix";
        VcfAutomationClient invalidTokenClient = new VcfAutomationClient(
                URI.create(baseUri), invalidToken, Duration.ofSeconds(3));
        try {
            invalidTokenClient.deleteDeploymentAndWait(deploymentId, Duration.ZERO);
            throw new AssertionError("invalid bearer token was accepted");
        } catch (IllegalArgumentException expected) {
            require(expected.getMessage() == null || !expected.getMessage().contains(token),
                    "invalid header exception disclosed bearer token");
        }
        require(beforeValidation.equals(Files.readString(log)),
                "invalid bearer token caused network traffic");
    }

    private static VcfAutomationClient.DeleteResult testSuccess(
            VcfAutomationClient client,
            String deploymentId,
            String requestId,
            int expectedPolls,
            Duration pollInterval) throws Exception {
        VcfAutomationClient.DeleteResult result = client.deleteDeploymentAndWait(
                deploymentId, pollInterval);
        require(requestId.equals(result.request().id()), "wrong request id");
        require(deploymentId.equals(result.request().deploymentId()), "wrong deployment id");
        require("Delete Deployment".equals(result.request().name()), "wrong request name");
        require("retry-harness".equals(result.request().requestedBy()), "wrong requester");
        require("SUCCESSFUL".equals(result.request().status()),
                "client returned a non-success state: " + result.request().status());
        require(result.request().completedTasks() == 1, "wrong completed task count");
        require(result.request().totalTasks() == 1, "wrong total task count");
        require(result.polls() == expectedPolls,
                "expected " + expectedPolls + " request polls, got " + result.polls());
        return result;
    }

    private static void testSleepAfterNonterminal(
            VcfAutomationClient client,
            String deploymentId,
            Path log) throws Exception {
        AtomicReference<Object> outcome = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try {
                outcome.set(client.deleteDeploymentAndWait(
                        deploymentId, Duration.ofSeconds(Long.MAX_VALUE)));
            } catch (Throwable error) {
                outcome.set(error);
            }
        }, "poll-interval-check");
        worker.start();

        while (true) {
            int requests = Files.readAllLines(log).size();
            require(requests <= 2, "client polled again without honoring pollInterval");
            if (!worker.isAlive()) {
                throw new AssertionError(
                        "client did not wait after a nonterminal response: " + outcome.get());
            }
            Thread.State state = worker.getState();
            if (requests == 2
                    && (state == Thread.State.WAITING
                            || state == Thread.State.TIMED_WAITING)) {
                boolean inClient = false;
                boolean inTransport = false;
                for (StackTraceElement frame : worker.getStackTrace()) {
                    inClient |= frame.getClassName().equals("VcfAutomationClient")
                            && frame.getMethodName().equals("deleteDeploymentAndWait");
                    inTransport |= frame.getClassName().startsWith("java.net.http.")
                            || frame.getClassName().startsWith("jdk.internal.net.http.");
                }
                if (inClient && !inTransport) {
                    worker.interrupt();
                    break;
                }
            }
            Thread.onSpinWait();
        }

        worker.join();
        require(outcome.get() instanceof InterruptedException,
                "poll wait did not propagate interruption: " + outcome.get());
    }

    private static void expectApiStatus(
            ThrowingAction action,
            int status,
            String token,
            String message) throws Exception {
        try {
            action.run();
        } catch (VcfAutomationClient.VcfApiException expected) {
            require(expected.statusCode() == status,
                    "wrong API status: " + expected.statusCode());
            require(expected.getMessage() != null && !expected.getMessage().contains(token),
                    "exception disclosed bearer token");
            return;
        }
        throw new AssertionError(message);
    }

    private static void expectIOException(
            ThrowingAction action,
            String token,
            String message) throws Exception {
        try {
            action.run();
        } catch (IOException expected) {
            require(expected.getMessage() == null || !expected.getMessage().contains(token),
                    "exception disclosed bearer token");
            return;
        }
        throw new AssertionError(message);
    }

    private static void expectIllegalArgument(ThrowingAction action, String message)
            throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
