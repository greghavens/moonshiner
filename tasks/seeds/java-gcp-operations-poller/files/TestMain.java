import com.sun.net.httpserver.HttpServer;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.TimeZone;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Acceptance tests for the long-running-operation poller. Everything runs
 * against a loopback com.sun.net.httpserver mock speaking the
 * google.longrunning wire contract pinned in docs/contract.json — no real
 * project, no real credentials, no sleeps (backoff is injected and recorded).
 */
public class TestMain {
    static final String TOKEN = "dummy-docai-token-5150";
    static final String OP_NAME = "projects/demo/locations/eu/operations/op-77";
    static final String META_TYPE = "type.googleapis.com/google.cloud.documentai.v1.BatchProcessMetadata";
    static final String RESP_TYPE = "type.googleapis.com/google.cloud.documentai.v1.BatchProcessResponse";

    static final String START_OK =
            "{\"name\":\"" + OP_NAME + "\",\"metadata\":{\"@type\":\"" + META_TYPE + "\",\"state\":\"RUNNING\"}}";
    static final String START_BAD =
            "{\"error\":{\"code\":400,\"message\":\"Invalid processor path\",\"status\":\"INVALID_ARGUMENT\","
                    + "\"details\":[{\"@type\":\"type.googleapis.com/google.rpc.ErrorInfo\","
                    + "\"reason\":\"INVALID_PROCESSOR\",\"domain\":\"documentai.googleapis.com\"}]}}";
    static final String OP_RUNNING_NO_DONE =
            "{\"name\":\"" + OP_NAME + "\",\"metadata\":{\"@type\":\"" + META_TYPE + "\",\"state\":\"RUNNING\"}}";
    static final String OP_RUNNING_DONE_FALSE =
            "{\"name\":\"" + OP_NAME + "\",\"done\":false,"
                    + "\"metadata\":{\"@type\":\"" + META_TYPE + "\",\"state\":\"RUNNING\"}}";
    static final String OP_SUCCEEDED =
            "{\"name\":\"" + OP_NAME + "\",\"done\":true,"
                    + "\"metadata\":{\"@type\":\"" + META_TYPE + "\",\"state\":\"SUCCEEDED\"},"
                    + "\"response\":{\"@type\":\"" + RESP_TYPE + "\"}}";
    static final String OP_FAILED =
            "{\"name\":\"" + OP_NAME + "\",\"done\":true,"
                    + "\"error\":{\"code\":3,\"message\":\"Invalid GCS input path\",\"details\":["
                    + "{\"@type\":\"type.googleapis.com/google.rpc.ErrorInfo\",\"reason\":\"INVALID_INPUT\","
                    + "\"domain\":\"documentai.googleapis.com\",\"metadata\":{\"uri\":\"gs://broken/path\"}},"
                    + "{\"@type\":\"type.googleapis.com/google.rpc.LocalizedMessage\",\"locale\":\"en-US\","
                    + "\"message\":\"The input path is invalid.\"}]}}";
    static final String OP_CANCELLED =
            "{\"name\":\"" + OP_NAME + "\",\"done\":true,"
                    + "\"error\":{\"code\":1,\"message\":\"Operation was cancelled\",\"details\":[]}}";
    static final String OP_DONE_EMPTY = "{\"name\":\"" + OP_NAME + "\",\"done\":true}";
    static final String ERR_UNAVAILABLE =
            "{\"error\":{\"code\":503,\"message\":\"The service is currently unavailable.\",\"status\":\"UNAVAILABLE\"}}";
    static final String ERR_NOT_FOUND =
            "{\"error\":{\"code\":404,\"message\":\"Operation not found: op-99\",\"status\":\"NOT_FOUND\"}}";
    static final String ERR_UNIMPLEMENTED =
            "{\"error\":{\"code\":501,\"message\":\"Operation cannot be cancelled\",\"status\":\"UNIMPLEMENTED\"}}";

    static int checks = 0;
    static int tests = 0;

    record Recorded(String method, String path, String auth, String body) {}

    record Scripted(int status, String body) {}

    static final class MockServer implements AutoCloseable {
        final HttpServer server;
        final List<Recorded> requests = new CopyOnWriteArrayList<>();
        final Deque<Scripted> responses = new ArrayDeque<>();
        final boolean repeatLast;

        MockServer(boolean repeatLast, Scripted... scripted) throws Exception {
            this.repeatLast = repeatLast;
            responses.addAll(Arrays.asList(scripted));
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                byte[] reqBody = exchange.getRequestBody().readAllBytes();
                requests.add(new Recorded(
                        exchange.getRequestMethod(),
                        exchange.getRequestURI().getPath(),
                        exchange.getRequestHeaders().getFirst("Authorization"),
                        new String(reqBody, StandardCharsets.UTF_8)));
                Scripted next;
                synchronized (responses) {
                    if (responses.isEmpty()) {
                        next = new Scripted(500,
                                "{\"error\":{\"code\":500,\"message\":\"mock script exhausted\",\"status\":\"INTERNAL\"}}");
                    } else if (repeatLast && responses.size() == 1) {
                        next = responses.peek();
                    } else {
                        next = responses.poll();
                    }
                }
                byte[] out = next.body().getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
                exchange.sendResponseHeaders(next.status(), out.length);
                try (OutputStream os = exchange.getResponseBody()) {
                    os.write(out);
                }
            });
            server.start();
        }

        MockServer(Scripted... scripted) throws Exception {
            this(false, scripted);
        }

        String base() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    static final class RecordingBackoff implements OperationPoller.Backoff {
        final List<Integer> pauses = new ArrayList<>();

        @Override
        public void pause(int attempt) {
            pauses.add(attempt);
        }
    }

    static void check(boolean cond, String what) {
        checks++;
        if (!cond) {
            throw new AssertionError(what);
        }
    }

    static void eq(Object got, Object want, String what) {
        checks++;
        if (!Objects.equals(got, want)) {
            throw new AssertionError(what + ": got " + got + ", want " + want);
        }
    }

    static ApiHttp http() {
        return new ApiHttp(HttpClient.newHttpClient());
    }

    static OperationPoller poller(MockServer srv, int maxPolls, int maxTransient, RecordingBackoff backoff) {
        return new OperationPoller(http(), srv.base(), TOKEN,
                new OperationPoller.PollPolicy(maxPolls, maxTransient, backoff));
    }

    static void allRequestsAuthorized(MockServer srv) {
        for (Recorded r : srv.requests) {
            eq(r.auth(), "Bearer " + TOKEN, "bearer auth on " + r.method() + " " + r.path());
        }
    }

    // ---- existing behavior: starting the batch operation ----

    static void testStartBatchSendsDocumentedRequest() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(new Scripted(200, START_OK))) {
            DocAiClient client = new DocAiClient(http(), srv.base(), TOKEN);
            String name = client.startBatch(
                    "projects/demo/locations/eu/processors/proc-1", "gs://in/batch/", "gs://out/results/");
            eq(name, OP_NAME, "operation name from batchProcess");
            eq(srv.requests.size(), 1, "batchProcess request count");
            Recorded r = srv.requests.get(0);
            eq(r.method(), "POST", "batchProcess method");
            eq(r.path(), "/v1/projects/demo/locations/eu/processors/proc-1:batchProcess", "batchProcess path");
            check(r.body().contains("\"gcsUriPrefix\":\"gs://in/batch/\""), "request body carries the input prefix");
            check(r.body().contains("\"gcsUri\":\"gs://out/results/\""), "request body carries the output uri");
            allRequestsAuthorized(srv);
        }
    }

    static void testStartBatchSurfacesApiError() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(new Scripted(400, START_BAD))) {
            DocAiClient client = new DocAiClient(http(), srv.base(), TOKEN);
            try {
                client.startBatch("projects/demo/locations/eu/processors/nope", "gs://in/", "gs://out/");
                throw new AssertionError("a 400 from batchProcess must raise ApiException");
            } catch (ApiException e) {
                eq(e.httpCode(), 400, "http code");
                eq(e.status(), "INVALID_ARGUMENT", "google.rpc status name");
                eq(e.details().size(), 1, "error details retained");
            }
        }
    }

    // ---- new behavior: polling the operation ----

    static void testPollSucceedsAfterRunningStates() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(
                new Scripted(200, OP_RUNNING_NO_DONE),
                new Scripted(200, OP_RUNNING_DONE_FALSE),
                new Scripted(200, OP_SUCCEEDED))) {
            RecordingBackoff backoff = new RecordingBackoff();
            OperationOutcome outcome = poller(srv, 5, 3, backoff).poll(OP_NAME);
            eq(outcome.state(), OperationOutcome.State.SUCCEEDED, "terminal state");
            eq(outcome.responseType(), RESP_TYPE, "response Any @type");
            eq(outcome.response().get("@type"), RESP_TYPE, "response payload retained");
            eq(outcome.metadataType(), META_TYPE, "metadata Any @type retained");
            eq(outcome.pollCount(), 3, "poll count");
            eq(outcome.transientRetries(), 0, "no transient retries");
            eq(backoff.pauses, List.of(1, 2), "injected backoff between polls");
            eq(srv.requests.size(), 3, "GET count");
            for (Recorded r : srv.requests) {
                eq(r.method(), "GET", "poll method");
                eq(r.path(), "/v1/" + OP_NAME, "service-specific operations path");
            }
            allRequestsAuthorized(srv);
        }
    }

    static void testPollFailureRetainsErrorDetails() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(new Scripted(200, OP_FAILED))) {
            RecordingBackoff backoff = new RecordingBackoff();
            OperationOutcome outcome = poller(srv, 5, 3, backoff).poll(OP_NAME);
            eq(outcome.state(), OperationOutcome.State.FAILED, "terminal state");
            eq(outcome.errorCode(), 3L, "google.rpc.Status code");
            check(outcome.errorMessage().contains("Invalid GCS input path"), "error message retained");
            eq(outcome.errorDetails().size(), 2, "all error details retained");
            Map<String, Object> info = Json.object(outcome.errorDetails().get(0));
            eq(info.get("@type"), "type.googleapis.com/google.rpc.ErrorInfo", "ErrorInfo @type");
            eq(info.get("reason"), "INVALID_INPUT", "ErrorInfo reason");
            eq(info.get("domain"), "documentai.googleapis.com", "ErrorInfo domain");
            eq(outcome.pollCount(), 1, "poll count");
            eq(backoff.pauses, List.of(), "no backoff on an immediately-done operation");
        }
    }

    static void testCancelThenObserveCancelledState() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(
                new Scripted(200, "{}"),
                new Scripted(200, OP_CANCELLED))) {
            RecordingBackoff backoff = new RecordingBackoff();
            OperationPoller poller = poller(srv, 5, 3, backoff);
            poller.cancel(OP_NAME);
            OperationOutcome outcome = poller.poll(OP_NAME);
            eq(srv.requests.size(), 2, "request count");
            Recorded cancel = srv.requests.get(0);
            eq(cancel.method(), "POST", "cancel method");
            eq(cancel.path(), "/v1/" + OP_NAME + ":cancel", "cancel path");
            eq(cancel.body(), "", "cancel request body is empty");
            eq(outcome.state(), OperationOutcome.State.CANCELLED, "code 1 maps to CANCELLED");
            eq(outcome.errorCode(), 1L, "google.rpc.Code.CANCELLED value retained");
            check(outcome.errorMessage().contains("cancelled"), "cancellation message retained");
            allRequestsAuthorized(srv);
        }
    }

    static void testCancelUnimplementedSurfaces() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(new Scripted(501, ERR_UNIMPLEMENTED))) {
            try {
                poller(srv, 5, 3, new RecordingBackoff()).cancel(OP_NAME);
                throw new AssertionError("UNIMPLEMENTED cancel must raise ApiException");
            } catch (ApiException e) {
                eq(e.httpCode(), 501, "http code");
                eq(e.status(), "UNIMPLEMENTED", "status name");
            }
        }
    }

    static void testTransientUnavailableIsRetriedThenSucceeds() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(
                new Scripted(503, ERR_UNAVAILABLE),
                new Scripted(503, ERR_UNAVAILABLE),
                new Scripted(200, OP_SUCCEEDED))) {
            RecordingBackoff backoff = new RecordingBackoff();
            OperationOutcome outcome = poller(srv, 5, 3, backoff).poll(OP_NAME);
            eq(outcome.state(), OperationOutcome.State.SUCCEEDED, "terminal state");
            eq(outcome.transientRetries(), 2, "transient retries counted");
            eq(outcome.pollCount(), 1, "only one successful poll");
            eq(backoff.pauses, List.of(1, 2), "backoff before each transient retry");
            eq(srv.requests.size(), 3, "GET count");
        }
    }

    static void testTransientRetriesAreBounded() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(true, new Scripted(503, ERR_UNAVAILABLE))) {
            try {
                poller(srv, 5, 2, new RecordingBackoff()).poll(OP_NAME);
                throw new AssertionError("persistent UNAVAILABLE must eventually raise ApiException");
            } catch (ApiException e) {
                eq(e.status(), "UNAVAILABLE", "status name");
                eq(e.httpCode(), 503, "http code");
                check(!e.getMessage().contains(TOKEN), "error text must not leak the bearer token");
            }
            eq(srv.requests.size(), 3, "initial attempt plus exactly two retries");
        }
    }

    static void testNonTransientStatusIsNotRetried() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(new Scripted(404, ERR_NOT_FOUND))) {
            RecordingBackoff backoff = new RecordingBackoff();
            try {
                poller(srv, 5, 3, backoff).poll("projects/demo/locations/eu/operations/op-99");
                throw new AssertionError("NOT_FOUND must raise ApiException");
            } catch (ApiException e) {
                eq(e.status(), "NOT_FOUND", "status name");
                eq(e.httpCode(), 404, "http code");
            }
            eq(srv.requests.size(), 1, "NOT_FOUND is terminal, never retried");
            eq(backoff.pauses, List.of(), "no backoff for a terminal status");
        }
    }

    static void testStuckOperationIsBoundedByMaxPolls() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(true, new Scripted(200, OP_RUNNING_DONE_FALSE))) {
            RecordingBackoff backoff = new RecordingBackoff();
            try {
                poller(srv, 4, 3, backoff).poll(OP_NAME);
                throw new AssertionError("an operation that never finishes must stop at maxPolls");
            } catch (IllegalStateException e) {
                check(e.getMessage().contains("still running"), "explains the operation is still running");
            }
            eq(srv.requests.size(), 4, "exactly maxPolls GETs");
            eq(backoff.pauses, List.of(1, 2, 3), "backoff between polls only");
        }
    }

    static void testDoneWithoutErrorOrResponseIsRejected() throws Exception {
        tests++;
        try (MockServer srv = new MockServer(new Scripted(200, OP_DONE_EMPTY))) {
            try {
                poller(srv, 5, 3, new RecordingBackoff()).poll(OP_NAME);
                throw new AssertionError("done without error or response violates the Operation contract");
            } catch (IllegalStateException e) {
                check(e.getMessage().contains("neither"), "explains the missing union field");
            }
        }
    }

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));

        testStartBatchSendsDocumentedRequest();
        testStartBatchSurfacesApiError();
        testPollSucceedsAfterRunningStates();
        testPollFailureRetainsErrorDetails();
        testCancelThenObserveCancelledState();
        testCancelUnimplementedSurfaces();
        testTransientUnavailableIsRetriedThenSucceeds();
        testTransientRetriesAreBounded();
        testNonTransientStatusIsNotRetried();
        testStuckOperationIsBoundedByMaxPolls();
        testDoneWithoutErrorOrResponseIsRejected();

        System.out.println("OK: " + checks + " checks across " + tests + " tests");
    }
}
