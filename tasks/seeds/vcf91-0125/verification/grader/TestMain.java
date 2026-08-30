import java.net.URI;
import java.net.http.HttpClient;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Protected integration harness for the credential-generation handoff.
 */
public final class TestMain {
    private static final Duration WAIT = Duration.ofSeconds(5);

    public static void main(String[] arguments) throws Exception {
        if (arguments.length != 1) {
            throw new IllegalArgumentException(
                    "usage: TestMain docs/contract.json");
        }

        List<String> failures = new ArrayList<>();
        Path contractPath = Path.of(arguments[0]);
        try (MockVcenterServer mock = new MockVcenterServer(contractPath)) {
            verifyLocalValidation(mock, failures);
            runRotationScenario(mock, failures);
        }

        if (!failures.isEmpty()) {
            System.err.println("FAIL (" + failures.size() + " assertions)");
            for (String failure : failures) {
                System.err.println(" - " + failure);
            }
            System.exit(1);
        }
        System.out.println(
                "PASS: in-flight session handoff and exact VCF 9.1 wire contract");
    }

    private static void verifyLocalValidation(
            MockVcenterServer mock, List<String> failures) {
        URI base = mock.apiBaseUri();
        expectThrows(
                IllegalArgumentException.class,
                () -> new VcenterCredentialRotationClient(
                        URI.create(base + "?unexpected=true"),
                        mock.username(),
                        mock.oldPassword(),
                        Duration.ofSeconds(2)),
                "query-bearing API URI must fail locally",
                failures);
        expectThrows(
                IllegalArgumentException.class,
                () -> new VcenterCredentialRotationClient(
                        base,
                        " ",
                        mock.oldPassword(),
                        Duration.ofSeconds(2)),
                "blank username must fail locally",
                failures);
        expectThrows(
                NullPointerException.class,
                () -> new VcenterCredentialRotationClient(
                        base,
                        mock.username(),
                        null,
                        Duration.ofSeconds(2)),
                "null password must fail locally",
                failures);
        expectThrows(
                IllegalArgumentException.class,
                () -> new VcenterCredentialRotationClient(
                        base,
                        mock.username(),
                        mock.oldPassword(),
                        Duration.ZERO),
                "zero timeout must fail locally",
                failures);
        checkEquals(
                "invalid construction caused no traffic",
                0,
                mock.requestLogSnapshot().size(),
                failures);
    }

    private static void runRotationScenario(
            MockVcenterServer mock, List<String> failures) throws Exception {
        VcenterCredentialRotationClient client = null;
        ExecutorService workers = Executors.newFixedThreadPool(2);
        Future<List<VcenterCredentialRotationClient.ClusterSummary>> oldCall = null;
        Future<?> rotation = null;
        try {
            client = new VcenterCredentialRotationClient(
                    mock.apiBaseUri(),
                    mock.username(),
                    mock.oldPassword(),
                    Duration.ofSeconds(3),
                    HttpClient.newBuilder()
                            .connectTimeout(Duration.ofSeconds(3))
                            .build());
            VcenterCredentialRotationClient activeClient = client;

            oldCall = workers.submit(activeClient::listClusters);
            check(
                    mock.awaitOldClusterStarted(WAIT),
                    "old-session cluster request did not start",
                    failures);

            rotation = workers.submit(() -> {
                activeClient.rotateCredentials(mock.replacementPassword());
                return null;
            });
            check(
                    mock.awaitReplacementCreated(WAIT),
                    "replacement session was not created",
                    failures);

            boolean deletedWhileInFlight =
                    mock.awaitOldSessionDeleted(Duration.ofMillis(300));
            check(
                    !deletedWhileInFlight,
                    "old session was deleted while its cluster request was in flight",
                    failures);
        } finally {
            mock.releaseOldCluster();
        }

        List<VcenterCredentialRotationClient.ClusterSummary> oldResult =
                await(oldCall, "old-session cluster call", failures);
        await(rotation, "credential rotation", failures);
        verifyClusterResult(
                "old-session in-flight result", oldResult, mock, failures);

        List<VcenterCredentialRotationClient.ClusterSummary> newResult = List.of();
        if (client != null) {
            try {
                newResult = client.listClusters();
            } catch (Exception exception) {
                failures.add("replacement-session cluster call failed: "
                        + safeType(exception));
            }
        }
        verifyClusterResult(
                "replacement-session result", newResult, mock, failures);
        List<VcenterCredentialRotationClient.ClusterSummary> immutableResult =
                newResult;
        expectThrows(
                UnsupportedOperationException.class,
                () -> immutableResult.add(
                        new VcenterCredentialRotationClient.ClusterSummary(
                        "mutated", "mutated", false, false)),
                "cluster result must be immutable",
                failures);

        if (client != null) {
            try {
                client.close();
                client.close();
            } catch (Exception exception) {
                failures.add("close failed: " + safeType(exception));
            }
            VcenterCredentialRotationClient closedClient = client;
            expectThrows(
                    IllegalStateException.class,
                    closedClient::listClusters,
                    "list after close must fail locally",
                    failures);
            expectThrows(
                    IllegalStateException.class,
                    () -> closedClient.rotateCredentials(
                            mock.replacementPassword()),
                    "rotation after close must fail locally",
                    failures);
        }

        workers.shutdownNow();
        workers.awaitTermination(3, TimeUnit.SECONDS);
        verifyWireLog(mock, failures);
    }

    private static void verifyClusterResult(
            String label,
            List<VcenterCredentialRotationClient.ClusterSummary> clusters,
            MockVcenterServer mock,
            List<String> failures) {
        checkEquals(label + " size", 1, clusters.size(), failures);
        if (clusters.size() != 1) {
            return;
        }
        VcenterCredentialRotationClient.ClusterSummary cluster = clusters.get(0);
        checkEquals(label + " cluster", mock.clusterId(), cluster.cluster(), failures);
        checkEquals(label + " name", "Rotation cluster", cluster.name(), failures);
        checkEquals(label + " ha_enabled", true, cluster.haEnabled(), failures);
        checkEquals(label + " drs_enabled", false, cluster.drsEnabled(), failures);
    }

    private static void verifyWireLog(
            MockVcenterServer mock, List<String> failures) {
        List<MockVcenterServer.LoggedRequest> log = mock.requestLogSnapshot();
        checkEquals("request count", 6, log.size(), failures);
        if (log.size() != 6) {
            return;
        }
        for (int index = 0; index < log.size(); index++) {
            checkEquals(
                    "request " + (index + 1) + " sequence",
                    index + 1,
                    log.get(index).sequence(),
                    failures);
        }

        String oldBasic = basic(mock.username(), mock.oldPassword());
        String newBasic = basic(mock.username(), mock.replacementPassword());
        verifyCreate("initial login", log.get(0), oldBasic, failures);
        verifyList(
                "old in-flight list",
                log.get(1),
                mock.oldSessionId(),
                failures);
        verifyCreate("replacement login", log.get(2), newBasic, failures);
        verifyDelete(
                "old retirement",
                log.get(3),
                mock.oldSessionId(),
                failures);
        verifyList(
                "replacement list",
                log.get(4),
                mock.replacementSessionId(),
                failures);
        verifyDelete(
                "replacement close",
                log.get(5),
                mock.replacementSessionId(),
                failures);
    }

    private static void verifyCreate(
            String label,
            MockVcenterServer.LoggedRequest request,
            String expectedAuthorization,
            List<String> failures) {
        verifyCommon(
                label,
                request,
                "Cis.Session_create",
                "POST",
                "/api/session",
                failures);
        checkEquals(label + " query", null, request.rawQuery(), failures);
        checkEquals(
                label + " Authorization",
                expectedAuthorization,
                request.authorization(),
                failures);
        checkEquals(label + " session header", null, request.sessionId(), failures);
        verifyNoBody(label, request, failures);
    }

    private static void verifyList(
            String label,
            MockVcenterServer.LoggedRequest request,
            String expectedSession,
            List<String> failures) {
        verifyCommon(
                label,
                request,
                "Vcenter.Cluster_list",
                "GET",
                "/api/vcenter/cluster",
                failures);
        checkEquals(
                label + " raw query and empty delimiter",
                null,
                request.rawQuery(),
                failures);
        checkEquals(label + " Authorization", null, request.authorization(), failures);
        checkEquals(
                label + " session header",
                expectedSession,
                request.sessionId(),
                failures);
        verifyNoBody(label, request, failures);
    }

    private static void verifyDelete(
            String label,
            MockVcenterServer.LoggedRequest request,
            String expectedSession,
            List<String> failures) {
        verifyCommon(
                label,
                request,
                "Cis.Session_delete",
                "DELETE",
                "/api/session",
                failures);
        checkEquals(label + " query", null, request.rawQuery(), failures);
        checkEquals(label + " Authorization", null, request.authorization(), failures);
        checkEquals(
                label + " session header",
                expectedSession,
                request.sessionId(),
                failures);
        verifyNoBody(label, request, failures);
    }

    private static void verifyCommon(
            String label,
            MockVcenterServer.LoggedRequest request,
            String operationId,
            String method,
            String path,
            List<String> failures) {
        checkEquals(label + " operationId", operationId,
                request.operationId(), failures);
        checkEquals(label + " method", method, request.method(), failures);
        checkEquals(label + " path", path, request.rawPath(), failures);
        checkEquals(label + " Accept", "application/json",
                request.accept(), failures);
    }

    private static void verifyNoBody(
            String label,
            MockVcenterServer.LoggedRequest request,
            List<String> failures) {
        checkEquals(label + " body size", 0, request.bodyBytes(), failures);
        checkEquals(label + " Content-Type", null,
                request.contentType(), failures);
        checkEquals(label + " transfer encoding", null,
                request.transferEncoding(), failures);
        if (request.contentLength() != null) {
            checkEquals(
                    label + " Content-Length",
                    "0",
                    request.contentLength(),
                    failures);
        }
    }

    private static String basic(String username, String password) {
        String value = username + ":" + password;
        return "Basic " + Base64.getEncoder().encodeToString(
                value.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }

    private static <T> T await(
            Future<T> future, String label, List<String> failures) {
        if (future == null) {
            failures.add(label + " was not started");
            return null;
        }
        try {
            return future.get(5, TimeUnit.SECONDS);
        } catch (ExecutionException exception) {
            failures.add(label + " failed: " + safeType(exception.getCause()));
        } catch (Exception exception) {
            failures.add(label + " failed: " + safeType(exception));
        }
        return null;
    }

    private static void check(
            boolean condition, String message, List<String> failures) {
        if (!condition) {
            failures.add(message);
        }
    }

    private static void checkEquals(
            String label, Object expected, Object actual, List<String> failures) {
        if (!Objects.equals(expected, actual)) {
            failures.add(label + ": expected <" + expected
                    + "> but was <" + actual + ">");
        }
    }

    private static void expectThrows(
            Class<? extends Throwable> expected,
            ThrowingAction action,
            String label,
            List<String> failures) {
        try {
            action.run();
            failures.add(label + ": expected " + expected.getSimpleName());
        } catch (Throwable actual) {
            if (!expected.isInstance(actual)) {
                failures.add(label + ": expected " + expected.getSimpleName()
                        + " but got " + safeType(actual));
            }
        }
    }

    private static String safeType(Throwable throwable) {
        return throwable == null
                ? "unknown failure"
                : throwable.getClass().getSimpleName();
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
