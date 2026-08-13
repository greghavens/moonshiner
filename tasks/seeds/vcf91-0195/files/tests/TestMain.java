import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;

/** Deterministic acceptance harness; no VMware endpoint is contacted. */
public final class TestMain {
    private static final Duration WAIT = Duration.ofSeconds(5);
    private static final String OPS_TOKEN = "ops-admin-token";
    private static final String OLD_BODY = "{\"secret\":\"old-\\\"s\\\\ecret\"}";
    private static final String NEW_BODY = "{\"secret\":\"new-\\\"s\\\\ecret\"}";
    private static final String NEW_TTL_BODY =
            "{\"secret\":\"new-\\\"s\\\\ecret\",\"ttl\":60000}";

    public static void main(String[] args) throws Exception {
        require(args.length == 1, "usage: TestMain <contract.json>");
        Path contract = Path.of(args[0]);

        try (MockVcfLogServer mock = new MockVcfLogServer(
                     contract, MockVcfLogServer.OldExchangeMode.HOLD);
             VcfLogClient client = new VcfLogClient(
                     mock.baseUri(), OPS_TOKEN, "legacy west/1", "old-\"s\\ecret", mock)) {

            CompletableFuture<VcfLogClient.AgentSession> oldInFlight =
                    client.openAgentSession(null);
            require(mock.awaitOldExchange(WAIT), "old-generation request never reached mock");
            require(!oldInFlight.isDone(), "fixture did not hold the old request in flight");

            CompletableFuture<VcfLogClient.RotationResult> rotation =
                    CompletableFuture.supplyAsync(() -> {
                        try {
                            return client.rotateAgentSecret("next \"blue\"/2", null);
                        } catch (Exception failure) {
                            throw new CompletionException(failure);
                        }
                    });

            MockVcfLogServer.RecordedRequest created = await(mock,
                    request -> request.rawPath().equals("/api/v2/agent/secrets")
                            && request.body().startsWith("{\"name\""),
                    "replacement secret creation");
            MockVcfLogServer.RecordedRequest validation = await(mock,
                    request -> request.rawPath().equals("/api/v2/agent/secrets/exchange")
                            && request.body().equals(NEW_BODY),
                    "replacement secret validation");

            awaitCurrentName(client, "next \"blue\"/2");
            CompletableFuture<VcfLogClient.AgentSession> newInFlight =
                    client.openAgentSession(60_000L);
            require(mock.awaitNewLiveExchange(WAIT),
                    "new-generation request did not progress during the drain");
            require(!newInFlight.isDone(),
                    "fixture did not hold the new-generation request in flight");

            MockVcfLogServer.RecordedRequest revokeTooSoon = mock.awaitRequest(
                    request -> request.rawPath().endsWith("/revoke"), Duration.ofMillis(250));
            try {
                require(revokeTooSoon == null,
                        "retired secret was revoked while its request was still in flight");
                require(!rotation.isDone(), "rotation did not wait for the retired generation");
            } finally {
                mock.releaseOldExchange();
            }

            VcfLogClient.AgentSession old = oldInFlight.get(WAIT.toMillis(), TimeUnit.MILLISECONDS);
            VcfLogClient.RotationResult result;
            try {
                result = rotation.get(WAIT.toMillis(), TimeUnit.MILLISECONDS);
            } catch (java.util.concurrent.TimeoutException timeout) {
                throw new AssertionError(
                        "rotation waited for an active-generation request", timeout);
            } finally {
                mock.releaseNewLiveExchange();
            }
            VcfLogClient.AgentSession fresh =
                    newInFlight.get(WAIT.toMillis(), TimeUnit.MILLISECONDS);
            require("token-old".equals(old.accessToken()), "old request was stranded");
            require("token-new-live".equals(fresh.accessToken()),
                    "new requests did not use the published replacement");
            require("legacy west/1".equals(result.retiredSecretName()), "wrong secret retired");
            require("next \"blue\"/2".equals(result.activeSecretName()), "wrong secret active");
            require("token-new-validation".equals(result.validatedSession().accessToken()),
                    "replacement was not validated before publication");

            MockVcfLogServer.RecordedRequest oldExchange = unique(mock.requestLog(),
                    request -> request.rawPath().equals("/api/v2/agent/secrets/exchange")
                            && request.body().equals(OLD_BODY), "old exchange");
            MockVcfLogServer.RecordedRequest liveExchange = unique(mock.requestLog(),
                    request -> request.rawPath().equals("/api/v2/agent/secrets/exchange")
                            && request.body().equals(NEW_TTL_BODY), "new live exchange");
            MockVcfLogServer.RecordedRequest revoke = unique(mock.requestLog(),
                    request -> request.rawPath().endsWith("/revoke"), "revoke");

            assertJsonRequest(created, "/api/v2/agent/secrets",
                    "{\"name\":\"next \\\"blue\\\"/2\"}");
            assertJsonRequest(oldExchange, "/api/v2/agent/secrets/exchange", OLD_BODY);
            assertJsonRequest(validation, "/api/v2/agent/secrets/exchange", NEW_BODY);
            assertJsonRequest(liveExchange, "/api/v2/agent/secrets/exchange", NEW_TTL_BODY);
            assertNoBodyRequest(revoke,
                    "/api/v2/agent/secrets/legacy%20west%2F1/revoke");

            List<MockVcfLogServer.RecordedRequest> log = mock.requestLog();
            require(log.size() == 5, "unexpected operation contacted: " + log);
            require(log.get(0).equals(oldExchange), "old request was not the initial in-flight call");
            require(log.get(1).equals(created), "replacement was not created after capture");
            require(log.get(2).equals(validation), "replacement was published before validation");
            require(log.get(3).equals(liveExchange),
                    "new request did not progress while the old generation drained");
            require(log.get(4).equals(revoke), "retired secret was not revoked last");
        }

        verifyExceptionalCompletionReleases(contract);
        verifyCancellationReleases(contract);
        verifySynchronousDispatchFailureReleases(contract);

        System.out.println("PASS: contract wire shape and drain-safe rotation verified");
    }

    private static void verifyExceptionalCompletionReleases(Path contract) throws Exception {
        try (MockVcfLogServer mock = new MockVcfLogServer(
                     contract, MockVcfLogServer.OldExchangeMode.COMPLETE_EXCEPTIONALLY);
             VcfLogClient client = new VcfLogClient(
                     mock.baseUri(), OPS_TOKEN, "legacy west/1", "old-\"s\\ecret", mock)) {
            CompletableFuture<VcfLogClient.AgentSession> failed = client.openAgentSession(null);
            require(mock.awaitOldExchange(WAIT), "exceptional request was not dispatched");
            try {
                failed.join();
                throw new AssertionError("exceptional request unexpectedly succeeded");
            } catch (CompletionException expected) {
                require(expected.getCause() != null, "exceptional completion lost its cause");
            }
            assertRotationCompletes(client, mock, "exceptional completion leaked generation");
        }
    }

    private static void verifyCancellationReleases(Path contract) throws Exception {
        try (MockVcfLogServer mock = new MockVcfLogServer(
                     contract, MockVcfLogServer.OldExchangeMode.CANCELLABLE);
             VcfLogClient client = new VcfLogClient(
                     mock.baseUri(), OPS_TOKEN, "legacy west/1", "old-\"s\\ecret", mock)) {
            CompletableFuture<VcfLogClient.AgentSession> cancelled = client.openAgentSession(null);
            require(mock.awaitOldExchange(WAIT), "cancellable request was not dispatched");
            require(cancelled.cancel(true), "request could not be cancelled");
            require(cancelled.isCancelled(), "request did not report cancellation");
            assertRotationCompletes(client, mock, "cancelled request leaked generation");
        }
    }

    private static void verifySynchronousDispatchFailureReleases(Path contract)
            throws Exception {
        try (MockVcfLogServer mock = new MockVcfLogServer(
                     contract, MockVcfLogServer.OldExchangeMode.THROW_SYNCHRONOUSLY);
             VcfLogClient client = new VcfLogClient(
                     mock.baseUri(), OPS_TOKEN, "legacy west/1", "old-\"s\\ecret", mock)) {
            try {
                client.openAgentSession(null);
                throw new AssertionError("synchronous dispatch failure was not propagated");
            } catch (IllegalArgumentException expected) {
                require(expected.getMessage().contains("synchronous dispatch failure"),
                        "wrong synchronous failure propagated");
            }
            assertRotationCompletes(client, mock,
                    "synchronous dispatch failure leaked generation");
        }
    }

    private static void assertRotationCompletes(
            VcfLogClient client, MockVcfLogServer mock, String leakMessage) throws Exception {
        CompletableFuture<VcfLogClient.RotationResult> rotation =
                CompletableFuture.supplyAsync(() -> {
                    try {
                        return client.rotateAgentSecret("next \"blue\"/2", null);
                    } catch (Exception failure) {
                        throw new CompletionException(failure);
                    }
                });
        VcfLogClient.RotationResult result;
        try {
            result = rotation.get(WAIT.toMillis(), TimeUnit.MILLISECONDS);
        } catch (java.util.concurrent.TimeoutException timeout) {
            throw new AssertionError(leakMessage, timeout);
        }
        require("legacy west/1".equals(result.retiredSecretName()),
                "edge-path rotation retired the wrong generation");
        unique(mock.requestLog(), request -> request.rawPath().endsWith("/revoke"),
                "edge-path revoke");
    }

    private static MockVcfLogServer.RecordedRequest await(
            MockVcfLogServer mock,
            Predicate<MockVcfLogServer.RecordedRequest> predicate,
            String description) throws InterruptedException {
        MockVcfLogServer.RecordedRequest request = mock.awaitRequest(predicate, WAIT);
        require(request != null, "timed out awaiting " + description);
        return request;
    }

    private static void awaitCurrentName(VcfLogClient client, String expected)
            throws InterruptedException {
        long deadline = System.nanoTime() + WAIT.toNanos();
        while (!expected.equals(client.currentSecretName())) {
            require(System.nanoTime() < deadline, "replacement was not published");
            Thread.sleep(2);
        }
    }

    private static MockVcfLogServer.RecordedRequest unique(
            List<MockVcfLogServer.RecordedRequest> requests,
            Predicate<MockVcfLogServer.RecordedRequest> predicate,
            String description) {
        List<MockVcfLogServer.RecordedRequest> matches = requests.stream()
                .filter(predicate)
                .toList();
        require(matches.size() == 1,
                "expected exactly one " + description + " request, got " + matches.size());
        return matches.get(0);
    }

    private static void assertJsonRequest(MockVcfLogServer.RecordedRequest request,
                                          String rawPath, String exactBody) {
        assertCommon(request, rawPath);
        require("application/json".equals(request.header("content-type")),
                "wrong Content-Type for " + rawPath);
        require(exactBody.equals(request.body()),
                "wrong JSON wire body for " + rawPath + ": " + request.body());
        require(!request.body().contains("null"), "unset optional field was sent as null");
        require(!request.body().contains("\"ttl\":\"\""),
                "unset optional ttl was sent empty");
    }

    private static void assertNoBodyRequest(MockVcfLogServer.RecordedRequest request,
                                            String rawPath) {
        assertCommon(request, rawPath);
        require(request.body().isEmpty(), "revoke operation must not have a request body");
        require(request.header("content-type") == null,
                "revoke operation must not invent a body content type");
    }

    private static void assertCommon(MockVcfLogServer.RecordedRequest request, String rawPath) {
        require("POST".equals(request.method()), "wrong HTTP method for " + rawPath);
        require(rawPath.equals(request.rawPath()),
                "wrong or incorrectly encoded request path: " + request.rawPath());
        require(request.rawQuery() == null, "unexpected query string for " + rawPath);
        require(OPS_TOKEN.equals(request.header("x-jwt-token")),
                "missing or wrong X-JWT-Token for " + rawPath);
        require(request.header("authorization") == null,
                "client invented an Authorization header outside the contract");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
