/*
 * Protected acceptance harness for the VCF Automation 9.1 Project Service
 * client. The loopback mock serves only the operation pinned in
 * docs/contract.json and records every request for assertions below.
 * No live VMware endpoint or credential is used, and there are no sleeps.
 *
 * Run: java TestMain.java
 */
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

public class TestMain {
    private static final String PROJECTS = "/project-service/api/projects";
    private static final String QUERY = "apiVersion=2019-01-15";
    private static final String V1 = "Bearer fixture-credential-generation-1";
    private static final String V2 = "Bearer fixture-credential-generation-2";
    private static final String V3 = "Bearer fixture-credential-generation-3";
    private static final String REJECTED = "Bearer fixture-rejected-current-credential";
    private static int checks;

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
        checks++;
    }

    private static void equal(Object actual, Object expected, String what) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(what + ": got " + actual + ", expected " + expected);
        }
        checks++;
    }

    private record RequestLog(String method, String path, String query,
                              String authorization, String accept, int status) {}

    private static final class RotationGate {
        private final String oldCredential;
        private final AtomicInteger claims;
        private final CountDownLatch arrived;
        private final CountDownLatch release = new CountDownLatch(1);

        RotationGate(String oldCredential, int requestCount) {
            this.oldCredential = oldCredential;
            this.claims = new AtomicInteger(requestCount);
            this.arrived = new CountDownLatch(requestCount);
        }

        boolean claim(String authorization) {
            if (!oldCredential.equals(authorization)) return false;
            while (true) {
                int value = claims.get();
                if (value <= 0) return false;
                if (claims.compareAndSet(value, value - 1)) return true;
            }
        }
    }

    private static final class LoopbackVcf implements AutoCloseable {
        private final HttpServer server;
        private final ExecutorService handlers = Executors.newCachedThreadPool();
        private final CopyOnWriteArrayList<RequestLog> requests = new CopyOnWriteArrayList<>();
        private final AtomicLong successfulResponses = new AtomicLong();
        private final AtomicInteger forcedStatus = new AtomicInteger();
        private final AtomicReference<String> nextSuccessfulBody = new AtomicReference<>();
        private volatile String acceptedCredential = V1;
        private volatile RotationGate rotationGate;

        LoopbackVcf() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.setExecutor(handlers);
            server.start();
            check(server.getAddress().getAddress().isLoopbackAddress(),
                    "mock must bind only to a loopback address");
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        }

        List<RequestLog> requestLog() {
            return List.copyOf(requests);
        }

        void acceptCredential(String value) {
            acceptedCredential = value;
        }

        RotationGate blockOldRequests(String value, int count) {
            RotationGate gate = new RotationGate(value, count);
            rotationGate = gate;
            return gate;
        }

        void awaitBlockedRequests(RotationGate gate) throws InterruptedException {
            check(gate.arrived.await(5, TimeUnit.SECONDS),
                    "all old-credential requests must reach the mock");
        }

        void releaseBlockedRequests(RotationGate gate) {
            gate.release.countDown();
        }

        void forceNextStatus(int status) {
            forcedStatus.set(status);
        }

        void respondNextWith(String body) {
            nextSuccessfulBody.set(body);
        }

        private void handle(HttpExchange exchange) throws IOException {
            String method = exchange.getRequestMethod();
            String path = exchange.getRequestURI().getRawPath();
            String query = exchange.getRequestURI().getRawQuery();
            String authorization = exchange.getRequestHeaders().getFirst("Authorization");
            String accept = exchange.getRequestHeaders().getFirst("Accept");

            int status;
            String body;
            if (!PROJECTS.equals(path)) {
                status = 404;
                body = "{\"error\":\"operation not in contract\"}";
            } else if (!"GET".equals(method)) {
                status = 405;
                body = "{\"error\":\"method not in contract\"}";
            } else if (!QUERY.equals(query)) {
                status = 400;
                body = "{\"error\":\"contract query mismatch\"}";
            } else {
                RotationGate gate = rotationGate;
                if (gate != null && gate.claim(authorization)) {
                    gate.arrived.countDown();
                    try {
                        if (!gate.release.await(5, TimeUnit.SECONDS)) {
                            status = 500;
                            body = "{\"error\":\"rotation gate timeout\"}";
                            requests.add(new RequestLog(method, path, query,
                                    authorization, accept, status));
                            send(exchange, status, body);
                            return;
                        }
                    } catch (InterruptedException interrupted) {
                        Thread.currentThread().interrupt();
                        status = 500;
                        body = "{\"error\":\"mock handler interrupted\"}";
                        requests.add(new RequestLog(method, path, query,
                                authorization, accept, status));
                        send(exchange, status, body);
                        return;
                    }
                }

                int forced = forcedStatus.getAndSet(0);
                if (forced != 0) {
                    status = forced;
                    body = "{\"error\":\"forced contract error\"}";
                } else if (!acceptedCredential.equals(authorization)) {
                    status = 401;
                    body = "";
                } else if (!"application/json".equals(accept)) {
                    status = 406;
                    body = "{\"error\":\"Accept header mismatch\"}";
                } else {
                    status = 200;
                    String configured = nextSuccessfulBody.getAndSet(null);
                    body = configured != null ? configured
                            : projectPage((successfulResponses.getAndIncrement() & 1L) == 0L);
                }
            }

            requests.add(new RequestLog(method, path, query, authorization, accept, status));
            send(exchange, status, body);
        }

        private static String projectPage(boolean reverse) {
            List<String> projects = new ArrayList<>(List.of(
                    "{\"id\":\"project-alpha\",\"name\":\"Alpha\",\"description\":\"alpha\\nline\",\"ignored\":{\"nested\":true}}",
                    "{\"id\":\"project-bravo-2\",\"name\":\"Bravo\",\"description\":\"second\"}",
                    "{\"id\":\"project-bravo-1\",\"name\":\"Bravo\",\"description\":\"first\"}",
                    "{\"id\":\"project-zulu\",\"name\":\"Zulu\"}"
            ));
            if (reverse) Collections.reverse(projects);
            return "{\"totalElements\":4,\"totalPages\":1,\"size\":500,"
                    + "\"content\":[" + String.join(",", projects) + "],"
                    + "\"number\":0,\"numberOfElements\":4,\"first\":true,"
                    + "\"last\":true,\"empty\":false}";
        }

        private static void send(HttpExchange exchange, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        }

        @Override
        public void close() {
            RotationGate gate = rotationGate;
            if (gate != null) gate.release.countDown();
            server.stop(0);
            handlers.shutdownNow();
        }
    }

    private static List<String> ids(List<VcfAutomationClient.Project> projects) {
        return projects.stream().map(VcfAutomationClient.Project::id).toList();
    }

    private static void checkSortedProjects(List<VcfAutomationClient.Project> projects,
                                            String context) {
        equal(ids(projects), List.of(
                "project-alpha", "project-bravo-1", "project-bravo-2", "project-zulu"),
                context + " sorted ids");
        equal(projects.get(0).description(), "alpha\nline",
                context + " JSON string decoding");
        equal(projects.get(3).description(), null,
                context + " absent optional description");
    }

    private static HttpResponse<String> raw(HttpClient http, URI uri, String method)
            throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(uri)
                .method(method, HttpRequest.BodyPublishers.noBody())
                .build();
        return http.send(request, HttpResponse.BodyHandlers.ofString());
    }

    private static void checkProtectedContract() throws IOException {
        String contract = Files.readString(Path.of("docs/contract.json"));
        check(contract.contains("\"kind\": \"reference-documentation\""),
                "contract must plainly identify its reference-documentation source");
        check(contract.contains("It is not a published specification"),
                "contract must disclaim published-specification status");
        check(contract.contains("\"name\": \"Get All Projects\"")
                        && contract.contains("\"path\": \"" + PROJECTS + "\""),
                "contract must name the served operation and path");

        String sources = Files.readString(Path.of("docs/official_sources.json"));
        check(sources.contains("developer.broadcom.com/xapis/all-apps-org-projects/latest/"
                        + "project-service/api/projects/get/"),
                "official source URL must be recorded");
        check(sources.contains("\"operation\": \"Get All Projects\"")
                        && sources.contains("\"fetched_on\": \"2026-08-16\""),
                "official source operation and fetch date must be recorded");
    }

    private static void checkRequestShape(List<RequestLog> log, String authorization) {
        for (RequestLog request : log) {
            if (!PROJECTS.equals(request.path())) continue;
            equal(request.method(), "GET", "contract request method");
            equal(request.query(), QUERY, "contract query");
            equal(request.authorization(), authorization, "Authorization generation");
            equal(request.accept(), "application/json", "Accept header");
        }
    }

    private static void testOrderingAndContract(LoopbackVcf mock) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V1);
        int before = mock.requestLog().size();
        checkSortedProjects(client.listProjects(), "reversed response");
        checkSortedProjects(client.listProjects(), "forward response");
        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 2, "ordinary request count");
        checkRequestShape(calls, V1);
    }

    private static void testDecodesLiveResponseValues(LoopbackVcf mock) throws Exception {
        mock.respondNextWith("{\"totalElements\":3,\"content\":["
                + "{\"description\":\"snowman \\u2603\",\"name\":\"Echo\","
                + "\"id\":\"live-echo\",\"ignored\":[1,false,null]},"
                + "{\"id\":\"live-alpha-2\",\"name\":\"Alpha\","
                + "\"description\":\"second\"},"
                + "{\"name\":\"Alpha\",\"id\":\"live-alpha-1\"}],"
                + "\"number\":0,\"last\":true}");
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V1);
        List<VcfAutomationClient.Project> projects = client.listProjects();
        equal(projects, List.of(
                new VcfAutomationClient.Project("live-alpha-1", "Alpha", null),
                new VcfAutomationClient.Project("live-alpha-2", "Alpha", "second"),
                new VcfAutomationClient.Project("live-echo", "Echo", "snowman ☃")),
                "projects decoded from the live response body");
    }

    private static void testInterruptedCallPreservesInterruptedException(LoopbackVcf mock)
            throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V1);
        RotationGate gate = mock.blockOldRequests(V1, 1);
        AtomicReference<Throwable> outcome = new AtomicReference<>();
        Thread caller = new Thread(() -> {
            try {
                client.listProjects();
                outcome.set(new AssertionError("interrupted request unexpectedly completed"));
            } catch (Throwable result) {
                outcome.set(result);
            }
        }, "interrupted-list-projects");

        try {
            caller.start();
            mock.awaitBlockedRequests(gate);
            caller.interrupt();
            caller.join(5_000);
            check(!caller.isAlive(), "interrupted request must terminate");
            check(outcome.get() instanceof InterruptedException,
                    "JDK HTTP interruption must remain InterruptedException, got "
                            + outcome.get());
        } finally {
            mock.releaseBlockedRequests(gate);
            caller.interrupt();
            caller.join(5_000);
        }
    }

    private static void testConcurrentRotation(LoopbackVcf mock) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V1);
        RotationGate gate = mock.blockOldRequests(V1, 2);
        ExecutorService callers = Executors.newFixedThreadPool(2);
        int before = mock.requestLog().size();
        try {
            Future<List<VcfAutomationClient.Project>> first = callers.submit(client::listProjects);
            Future<List<VcfAutomationClient.Project>> second = callers.submit(client::listProjects);
            mock.awaitBlockedRequests(gate);
            client.rotateCredential(V2);
            mock.acceptCredential(V2);
            mock.releaseBlockedRequests(gate);

            checkSortedProjects(first.get(5, TimeUnit.SECONDS), "first rotated request");
            checkSortedProjects(second.get(5, TimeUnit.SECONDS), "second rotated request");
        } finally {
            mock.releaseBlockedRequests(gate);
            callers.shutdownNow();
        }

        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        long old401 = calls.stream().filter(r -> V1.equals(r.authorization()) && r.status() == 401).count();
        long new200 = calls.stream().filter(r -> V2.equals(r.authorization()) && r.status() == 200).count();
        equal(old401, 2L, "superseded in-flight attempts");
        equal(new200, 2L, "retried current-generation attempts");
        equal(calls.size(), 4, "rotation request count");
    }

    private static void testRepeatedRotation(LoopbackVcf mock) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V1);
        RotationGate firstGate = mock.blockOldRequests(V1, 1);
        ExecutorService caller = Executors.newSingleThreadExecutor();
        int before = mock.requestLog().size();
        try {
            Future<List<VcfAutomationClient.Project>> result = caller.submit(client::listProjects);
            mock.awaitBlockedRequests(firstGate);

            client.rotateCredential(V2);
            mock.acceptCredential(V2);
            RotationGate secondGate = mock.blockOldRequests(V2, 1);
            mock.releaseBlockedRequests(firstGate);
            mock.awaitBlockedRequests(secondGate);

            client.rotateCredential(V3);
            mock.acceptCredential(V3);
            mock.releaseBlockedRequests(secondGate);
            checkSortedProjects(result.get(5, TimeUnit.SECONDS), "twice-rotated request");
        } finally {
            firstGate.release.countDown();
            RotationGate current = mock.rotationGate;
            if (current != null) current.release.countDown();
            caller.shutdownNow();
        }

        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 3, "twice-rotated request count");
        equal(calls.stream().map(RequestLog::authorization).toList(),
                List.of(V1, V2, V3), "credential generations across repeated rotation");
        equal(calls.stream().map(RequestLog::status).toList(),
                List.of(401, 401, 200), "statuses across repeated rotation");
    }

    private static void testSuccessfulOldGenerationIsNotRetried(LoopbackVcf mock)
            throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V3);
        RotationGate gate = mock.blockOldRequests(V3, 1);
        ExecutorService caller = Executors.newSingleThreadExecutor();
        int before = mock.requestLog().size();
        try {
            Future<List<VcfAutomationClient.Project>> result = caller.submit(client::listProjects);
            mock.awaitBlockedRequests(gate);
            client.rotateCredential(V1);
            mock.releaseBlockedRequests(gate);
            checkSortedProjects(result.get(5, TimeUnit.SECONDS),
                    "successful superseded-generation request");
        } finally {
            mock.releaseBlockedRequests(gate);
            caller.shutdownNow();
        }

        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 1, "successful superseded-generation request count");
        equal(calls.get(0).authorization(), V3,
                "successful superseded-generation credential");
        equal(calls.get(0).status(), 200,
                "successful superseded-generation status");
        mock.acceptCredential(V1);
    }

    private static void testSupersededGenerationNon401Fails(LoopbackVcf mock)
            throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V1);
        RotationGate gate = mock.blockOldRequests(V1, 1);
        ExecutorService caller = Executors.newSingleThreadExecutor();
        int before = mock.requestLog().size();
        try {
            Future<List<VcfAutomationClient.Project>> result = caller.submit(client::listProjects);
            mock.awaitBlockedRequests(gate);
            client.rotateCredential(V2);
            mock.acceptCredential(V2);
            mock.forceNextStatus(403);
            mock.releaseBlockedRequests(gate);
            try {
                result.get(5, TimeUnit.SECONDS);
                throw new AssertionError("non-401 from an old generation must fail");
            } catch (ExecutionException expected) {
                check(expected.getCause() instanceof IOException,
                        "non-401 failure must remain IOException");
            }
        } finally {
            mock.releaseBlockedRequests(gate);
            caller.shutdownNow();
        }

        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 1, "superseded-generation non-401 request count");
        equal(calls.get(0).authorization(), V1,
                "superseded-generation non-401 credential");
        equal(calls.get(0).status(), 403,
                "superseded-generation non-401 status");
    }

    private static void testCredentialValueReuseStillAdvancesGeneration(LoopbackVcf mock)
            throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V2);
        RotationGate gate = mock.blockOldRequests(V2, 1);
        ExecutorService caller = Executors.newSingleThreadExecutor();
        int before = mock.requestLog().size();
        try {
            Future<List<VcfAutomationClient.Project>> result = caller.submit(client::listProjects);
            mock.awaitBlockedRequests(gate);
            client.rotateCredential(V3);
            client.rotateCredential(V2);
            mock.acceptCredential(V2);
            mock.forceNextStatus(401);
            mock.releaseBlockedRequests(gate);
            checkSortedProjects(result.get(5, TimeUnit.SECONDS),
                    "reused credential value request");
        } finally {
            mock.releaseBlockedRequests(gate);
            caller.shutdownNow();
        }

        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 2, "reused credential value request count");
        equal(calls.stream().map(RequestLog::authorization).toList(),
                List.of(V2, V2), "reused credential value across distinct generations");
        equal(calls.stream().map(RequestLog::status).toList(),
                List.of(401, 200), "reused credential value statuses");
    }

    private static void testCurrentCredential401Fails(LoopbackVcf mock) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V3);
        client.rotateCredential(REJECTED);
        int before = mock.requestLog().size();
        try {
            client.listProjects();
            throw new AssertionError("401 for the current credential must throw IOException");
        } catch (IOException expected) {
            checks++;
        }
        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 1, "current-generation 401 must not be retried");
        equal(calls.get(0).authorization(), REJECTED, "rejected current credential");
        equal(calls.get(0).status(), 401, "rejected current credential status");
    }

    private static void testOtherErrorFails(LoopbackVcf mock) throws Exception {
        VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), V3);
        mock.forceNextStatus(403);
        int before = mock.requestLog().size();
        try {
            client.listProjects();
            throw new AssertionError("non-2xx response must throw IOException");
        } catch (IOException expected) {
            checks++;
        }
        List<RequestLog> calls = mock.requestLog().subList(before, mock.requestLog().size());
        equal(calls.size(), 1, "non-401 error request count");
        equal(calls.get(0).status(), 403, "non-401 error status");
    }

    public static void main(String[] args) throws Exception {
        checkProtectedContract();
        try (LoopbackVcf mock = new LoopbackVcf()) {
            HttpClient raw = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
            equal(raw(raw, mock.baseUri().resolve("/not-in-contract"), "GET").statusCode(),
                    404, "unknown operation status");
            equal(raw(raw, mock.baseUri().resolve(PROJECTS + "?" + QUERY), "POST").statusCode(),
                    405, "wrong method status");

            testOrderingAndContract(mock);
            testDecodesLiveResponseValues(mock);
            testConcurrentRotation(mock);
            testRepeatedRotation(mock);
            testSuccessfulOldGenerationIsNotRetried(mock);
            testSupersededGenerationNon401Fails(mock);
            testCredentialValueReuseStillAdvancesGeneration(mock);
            testCurrentCredential401Fails(mock);
            testOtherErrorFails(mock);
            testInterruptedCallPreservesInterruptedException(mock);

            check(mock.requestLog().size() >= 18, "test must read the mock request log");
        }
        System.out.println("OK " + checks + " checks");
    }
}
