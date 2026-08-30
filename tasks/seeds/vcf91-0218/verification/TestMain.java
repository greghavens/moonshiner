import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.InetSocketAddress;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executor;
import java.util.concurrent.Flow;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Protected loopback and scripted-transport tests for the single-file client. */
public final class TestMain {
    private record OperationContract(String operationId, String method, String path) {
    }

    private record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            List<String> accept,
            List<String> authorization,
            List<String> contentType,
            List<String> contentLength,
            List<String> transferEncoding,
            byte[] body) {
    }

    private record ResponseSpec(int status, String contentType, byte[] body) {
        ResponseSpec(int status, String contentType, String body) {
            this(status, contentType, body.getBytes(StandardCharsets.UTF_8));
        }
    }

    @FunctionalInterface
    private interface Responder {
        ResponseSpec response(int attempt, LoggedRequest request) throws IOException;
    }

    private static final class ContractPinnedMock {
        private final HttpServer server;
        private final OperationContract operation;
        private final Path requestLog;
        private final Responder responder;
        private final List<LoggedRequest> requests = new ArrayList<>();
        private byte[] committedRepresentation;
        private int semanticMutations;

        ContractPinnedMock(OperationContract operation, Path requestLog,
                           Responder responder) throws IOException {
            this.operation = operation;
            this.requestLog = requestLog;
            this.responder = responder;
            if (requestLog != null) {
                Files.writeString(requestLog, "", StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            }
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext(operation.path(), this::handleOperation);
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        }

        void start() { server.start(); }
        void stop() { server.stop(0); }
        synchronized List<LoggedRequest> requests() { return List.copyOf(requests); }
        synchronized int semanticMutations() { return semanticMutations; }

        private synchronized void handleOperation(HttpExchange exchange) throws IOException {
            byte[] body = exchange.getRequestBody().readAllBytes();
            LoggedRequest request = new LoggedRequest(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getRawPath(),
                    exchange.getRequestURI().getRawQuery(),
                    values(exchange, "Accept"),
                    values(exchange, "Authorization"),
                    values(exchange, "Content-Type"),
                    values(exchange, "Content-Length"),
                    values(exchange, "Transfer-Encoding"),
                    body);
            requests.add(request);
            if (requestLog != null) appendLog(request);

            if (!operation.path().equals(request.rawPath())) {
                send(exchange, new ResponseSpec(404, "application/json",
                        "{\"message\":\"not found\"}"));
                return;
            }
            if (!operation.method().equals(request.method())) {
                send(exchange, new ResponseSpec(405, "application/json",
                        "{\"message\":\"method not allowed\"}"));
                return;
            }

            if (committedRepresentation == null
                    || !Arrays.equals(committedRepresentation, body)) {
                committedRepresentation = body.clone();
                semanticMutations++;
            }
            send(exchange, responder.response(requests.size(), request));
        }

        private void appendLog(LoggedRequest request) throws IOException {
            String line = "{"
                    + "\"method\":" + jsonString(request.method()) + ","
                    + "\"rawTarget\":" + jsonString(request.rawPath()
                            + (request.rawQuery() == null ? "" : "?" + request.rawQuery())) + ","
                    + "\"accept\":" + jsonStrings(request.accept()) + ","
                    + "\"authorization\":" + jsonStrings(request.authorization()) + ","
                    + "\"contentType\":" + jsonStrings(request.contentType()) + ","
                    + "\"contentLength\":" + jsonStrings(request.contentLength()) + ","
                    + "\"transferEncoding\":" + jsonStrings(request.transferEncoding()) + ","
                    + "\"bodyBase64\":"
                    + jsonString(Base64.getEncoder().encodeToString(request.body()))
                    + "}\n";
            Files.writeString(requestLog, line, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }

        private static List<String> values(HttpExchange exchange, String name) {
            return List.copyOf(exchange.getRequestHeaders().getOrDefault(name, List.of()));
        }

        private static void send(HttpExchange exchange, ResponseSpec response)
                throws IOException {
            if (response.contentType() != null) {
                exchange.getResponseHeaders().set("Content-Type", response.contentType());
            }
            exchange.sendResponseHeaders(response.status(), response.body().length);
            exchange.getResponseBody().write(response.body());
            exchange.close();
        }
    }

    private record RequestSnapshot(URI uri, String method, HttpHeaders headers,
                                   long contentLength, byte[] body) {
    }

    /** Deterministic transport double used to distinguish IO failures from interruption. */
    private static final class ScriptedHttpClient extends HttpClient {
        private final List<Object> outcomes;
        private final List<RequestSnapshot> requests = new ArrayList<>();

        ScriptedHttpClient(Object... outcomes) {
            this.outcomes = List.of(outcomes);
        }

        List<RequestSnapshot> requests() { return List.copyOf(requests); }

        @Override
        public <T> HttpResponse<T> send(HttpRequest request,
                                        HttpResponse.BodyHandler<T> handler)
                throws IOException, InterruptedException {
            requests.add(snapshot(request));
            int index = requests.size() - 1;
            if (index >= outcomes.size()) {
                throw new AssertionError("unexpected transport attempt " + (index + 1));
            }
            Object outcome = outcomes.get(index);
            if (outcome instanceof IOException error) throw error;
            if (outcome instanceof InterruptedException error) throw error;
            ResponseSpec response = (ResponseSpec) outcome;
            HttpHeaders headers = response.contentType() == null
                    ? HttpHeaders.of(Map.of(), (name, value) -> true)
                    : HttpHeaders.of(Map.of("Content-Type", List.of(response.contentType())),
                            (name, value) -> true);
            HttpResponse.ResponseInfo info = new HttpResponse.ResponseInfo() {
                public int statusCode() { return response.status(); }
                public HttpHeaders headers() { return headers; }
                public Version version() { return Version.HTTP_1_1; }
            };
            HttpResponse.BodySubscriber<T> subscriber = handler.apply(info);
            subscriber.onSubscribe(new Flow.Subscription() {
                public void request(long count) { }
                public void cancel() { }
            });
            subscriber.onNext(List.of(ByteBuffer.wrap(response.body())));
            subscriber.onComplete();
            T body;
            try {
                body = subscriber.getBody().toCompletableFuture().join();
            } catch (CompletionException error) {
                throw new IOException("response body handling failed", error.getCause());
            }
            return new SimpleResponse<>(request, response.status(), headers, body);
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> handler) {
            try {
                return CompletableFuture.completedFuture(send(request, handler));
            } catch (Throwable error) {
                return CompletableFuture.failedFuture(error);
            }
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> handler,
                HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
            return sendAsync(request, handler);
        }

        private static RequestSnapshot snapshot(HttpRequest request) throws IOException {
            HttpRequest.BodyPublisher publisher = request.bodyPublisher()
                    .orElseThrow(() -> new AssertionError("PUT body publisher missing"));
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            CompletableFuture<Void> done = new CompletableFuture<>();
            publisher.subscribe(new Flow.Subscriber<>() {
                public void onSubscribe(Flow.Subscription subscription) {
                    subscription.request(Long.MAX_VALUE);
                }
                public void onNext(ByteBuffer item) {
                    ByteBuffer copy = item.slice();
                    byte[] bytes = new byte[copy.remaining()];
                    copy.get(bytes);
                    output.writeBytes(bytes);
                }
                public void onError(Throwable error) { done.completeExceptionally(error); }
                public void onComplete() { done.complete(null); }
            });
            try {
                done.join();
            } catch (CompletionException error) {
                throw new IOException("request body publication failed", error.getCause());
            }
            return new RequestSnapshot(request.uri(), request.method(), request.headers(),
                    publisher.contentLength(), output.toByteArray());
        }

        public Optional<CookieHandler> cookieHandler() { return Optional.empty(); }
        public Optional<Duration> connectTimeout() { return Optional.empty(); }
        public Redirect followRedirects() { return Redirect.NEVER; }
        public Optional<ProxySelector> proxy() { return Optional.empty(); }
        public SSLContext sslContext() { return null; }
        public SSLParameters sslParameters() { return new SSLParameters(); }
        public Optional<Authenticator> authenticator() { return Optional.empty(); }
        public Version version() { return Version.HTTP_1_1; }
        public Optional<Executor> executor() { return Optional.empty(); }
    }

    private record SimpleResponse<T>(HttpRequest request, int statusCode,
                                     HttpHeaders headers, T body)
            implements HttpResponse<T> {
        public Optional<HttpResponse<T>> previousResponse() { return Optional.empty(); }
        public Optional<SSLSession> sslSession() { return Optional.empty(); }
        public URI uri() { return request.uri(); }
        public HttpClient.Version version() { return HttpClient.Version.HTTP_1_1; }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new AssertionError("workspace root and request-log path required");
        }
        OperationContract operation = readPinnedContract(Path.of(args[0]));
        Path requestLog = Path.of(args[1]);

        testTransportFailures(operation);
        testLocalValidation(operation);
        testActivationAndJsonEscaping(operation);
        testRetryPolicy(operation);
        testAcceptedResponseValidation(operation);
        testCommitBefore500(operation, requestLog);

        System.out.println("PASS: updateDepotSettings contract and exact retry behavior");
    }

    private static void testCommitBefore500(OperationContract operation, Path requestLog)
            throws Exception {
        String accessToken = "access-core-fixed";
        String downloadToken = "dt-core-\"\\\u0001";
        ContractPinnedMock mock = new ContractPinnedMock(operation, requestLog,
                (attempt, request) -> attempt == 1
                        ? new ResponseSpec(500, "application/json",
                                "{\"message\":\"ambiguous server failure\"}")
                        : new ResponseSpec(202, "application/json", request.body()));
        mock.start();
        try {
            VcfInstallerClient client = client(mock.baseUri(), accessToken);
            VcfInstallerClient.DepotSettings result = client.updateDepotSettings(
                    new VcfInstallerClient.DepotUpdate(downloadToken, " \t "), 1);
            assertEquals(downloadToken, result.downloadToken(), "accepted downloadToken");
            assertEquals(null, result.downloadActivationCode(), "blank activation is unset");

            String expectedBody = "{\"vmwareAccount\":{\"downloadToken\":"
                    + jsonString(downloadToken) + "}}";
            assertWire(mock.requests(), accessToken, expectedBody);
            assertEquals(2, mock.requests().size(), "HTTP 500 must be retried once");
            assertSameAttempts(mock.requests().get(0), mock.requests().get(1));
            assertEquals(1, mock.semanticMutations(),
                    "exact PUT retry must have one replacement effect");
            assertPersistedLog(requestLog, mock.requests());
        } finally {
            mock.stop();
        }
    }

    private static void testActivationAndJsonEscaping(OperationContract operation)
            throws Exception {
        String accessToken = "access-activation";
        String downloadToken = "tok-é-\"\\\n" + (char) 0xd800;
        String activationCode = "activation-\t-\"-\\" + (char) 0xdc00;
        String expectedBody = "{\"vmwareAccount\":{\"downloadToken\":"
                + jsonString(downloadToken) + ",\"downloadActivationCode\":"
                + jsonString(activationCode) + "}}";
        ScriptedHttpClient transport = new ScriptedHttpClient(new ResponseSpec(
                202, "application/json", expectedBody));
        VcfInstallerClient.DepotSettings result = new VcfInstallerClient(
                URI.create("https://installer.example"), accessToken, transport)
                .updateDepotSettings(new VcfInstallerClient.DepotUpdate(
                        downloadToken, activationCode), 0);
        assertEquals(downloadToken, result.downloadToken(), "escaped response token");
        assertEquals(activationCode, result.downloadActivationCode(),
                "escaped response activation code");
        assertEquals(1, transport.requests().size(), "202 must not be retried");
        assertSnapshotWire(transport.requests().get(0), operation.path(),
                accessToken, expectedBody);
    }

    private static void testRetryPolicy(OperationContract operation) throws Exception {
        String token = "retry-policy-token";
        String activation = "retry-activation";
        String accepted = "{\"vmwareAccount\":{\"downloadToken\":"
                + jsonString(token) + ",\"downloadActivationCode\":"
                + jsonString(activation) + "}}";
        ResponseSpec success = new ResponseSpec(202, "application/json", accepted);

        for (int status : List.of(400, 401, 503, 200)) {
            ScriptedHttpClient transport = new ScriptedHttpClient(
                    new ResponseSpec(status, "application/json",
                            status == 200 ? accepted
                                    : "{\"message\":"
                                            + jsonString(token + activation) + "}"),
                    success);
            IOException error = expectThrows(IOException.class,
                    () -> new VcfInstallerClient(URI.create("https://installer.example"),
                            "access-status", transport).updateDepotSettings(
                            new VcfInstallerClient.DepotUpdate(token, activation), 5),
                    "HTTP " + status + " must fail");
            assertNoSecrets(error, token, activation);
            assertEquals(1, transport.requests().size(),
                    "HTTP " + status + " must not be retried");
        }

        ScriptedHttpClient exhausted = new ScriptedHttpClient(
                new ResponseSpec(500, "application/json",
                        "{\"message\":\"server failure\"}"), success);
        expectThrows(IOException.class,
                () -> new VcfInstallerClient(URI.create("https://installer.example"),
                        "access-no-retry", exhausted).updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(token, activation), 0),
                "maxRetries zero must allow only the first attempt");
        assertEquals(1, exhausted.requests().size(), "500 retry budget zero");

        ResponseSpec serverError = new ResponseSpec(500, "application/json", "{}");
        String acceptedWithoutActivation = "{\"vmwareAccount\":{\"downloadToken\":"
                + jsonString(token) + "}}";
        ScriptedHttpClient fiveRetries = new ScriptedHttpClient(
                serverError, serverError, serverError, serverError, serverError,
                new ResponseSpec(202, "application/json", acceptedWithoutActivation));
        new VcfInstallerClient(URI.create("https://installer.example"),
                "access-five", fiveRetries).updateDepotSettings(
                new VcfInstallerClient.DepotUpdate(token, null), 5);
        assertEquals(6, fiveRetries.requests().size(),
                "maxRetries five means one initial plus five retries");
        for (int index = 1; index < fiveRetries.requests().size(); index++) {
            assertSameSnapshots(fiveRetries.requests().get(0),
                    fiveRetries.requests().get(index), operation.path());
        }
    }

    private static void testAcceptedResponseValidation(OperationContract operation)
            throws Exception {
        String token = "response-secret-token";
        String activation = "response-secret-activation";
        String valid = "{\"vmwareAccount\":{\"downloadToken\":" + jsonString(token)
                + ",\"downloadActivationCode\":" + jsonString(activation) + "}}";
        List<ResponseSpec> invalid = List.of(
                new ResponseSpec(202, null, valid),
                new ResponseSpec(202, "text/plain", valid),
                new ResponseSpec(202, "application/json", "not-json"),
                new ResponseSpec(202, "application/json", "[]"),
                new ResponseSpec(202, "application/json", "{}"),
                new ResponseSpec(202, "application/json",
                        "{\"vmwareAccount\":{\"downloadToken\":\"wrong\","
                                + "\"downloadActivationCode\":"
                                + jsonString(activation) + "}}"),
                new ResponseSpec(202, "application/json",
                        "{\"vmwareAccount\":{\"downloadToken\":"
                                + jsonString(token) + "}}"));
        int caseNumber = 0;
        for (ResponseSpec response : invalid) {
            caseNumber++;
            ScriptedHttpClient transport = new ScriptedHttpClient(response);
            IOException error = expectThrows(IOException.class,
                    () -> new VcfInstallerClient(URI.create("https://installer.example"),
                            "access-response", transport).updateDepotSettings(
                            new VcfInstallerClient.DepotUpdate(token, activation), 3),
                    "invalid 202 response case " + caseNumber);
            assertNoSecrets(error, token, activation);
            assertEquals(1, transport.requests().size(),
                    "invalid 202 response must not be retried");
        }

        String unexpectedActivation = "{\"vmwareAccount\":{\"downloadToken\":"
                + jsonString(token) + ",\"downloadActivationCode\":\"unexpected\"}}";
        ScriptedHttpClient transport = new ScriptedHttpClient(
                new ResponseSpec(202, "application/json", unexpectedActivation));
        IOException error = expectThrows(IOException.class,
                () -> new VcfInstallerClient(URI.create("https://installer.example"),
                        "access-unset-response", transport).updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(token, null), 2),
                "unset activation must not match a returned activation value");
        assertNoSecrets(error, token);
        assertEquals(1, transport.requests().size(), "mismatched 202 must not retry");

        String replacementToken = "utf8-\ufffd";
        byte[] malformedUtf8 = ("{\"vmwareAccount\":{\"downloadToken\":\"utf8-"
                + '\u0080' + "\"}}")
                .getBytes(StandardCharsets.ISO_8859_1);
        ScriptedHttpClient malformedTransport = new ScriptedHttpClient(
                new ResponseSpec(202, "application/json", malformedUtf8));
        IOException malformedError = expectThrows(IOException.class,
                () -> new VcfInstallerClient(URI.create("https://installer.example"),
                        "access-malformed-utf8", malformedTransport).updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(replacementToken, null), 2),
                "application/json must contain valid UTF-8");
        assertNoSecrets(malformedError, replacementToken);
        assertEquals(1, malformedTransport.requests().size(),
                "malformed UTF-8 response must not retry");
    }

    private static void testTransportFailures(OperationContract operation) throws Exception {
        String token = "transport-secret-token";
        String accepted = "{\"vmwareAccount\":{\"downloadToken\":"
                + jsonString(token) + "}}";
        ResponseSpec success = new ResponseSpec(202, "application/json", accepted);

        ScriptedHttpClient ioThenSuccess = new ScriptedHttpClient(
                new IOException("ambiguous write failure"), success);
        VcfInstallerClient.DepotSettings result = new VcfInstallerClient(
                URI.create("https://installer.example"), "access-io", ioThenSuccess)
                .updateDepotSettings(new VcfInstallerClient.DepotUpdate(token, null), 1);
        assertEquals(token, result.downloadToken(), "IO retry result");
        assertEquals(2, ioThenSuccess.requests().size(), "IOException must be retried");
        assertSameSnapshots(ioThenSuccess.requests().get(0), ioThenSuccess.requests().get(1),
                operation.path());

        ScriptedHttpClient interrupted = new ScriptedHttpClient(
                new InterruptedException("stop"), success);
        expectThrows(Exception.class,
                () -> new VcfInstallerClient(URI.create("https://installer.example"),
                        "access-interrupt", interrupted).updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(token, null), 5),
                "interruption must fail immediately");
        assertEquals(1, interrupted.requests().size(), "interruption must not be retried");

        ScriptedHttpClient exhausted = new ScriptedHttpClient(
                new IOException("ambiguous " + token));
        IOException error = expectThrows(IOException.class,
                () -> new VcfInstallerClient(URI.create("https://installer.example"),
                        "access-io-exhausted", exhausted).updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(token, null), 0),
                "exhausted transport failure");
        assertNoSecrets(error, token);
        assertEquals(1, exhausted.requests().size(), "exhausted IO attempt count");
    }

    private static void testLocalValidation(OperationContract operation) throws Exception {
        String token32 = "x".repeat(32);
        String body32 = "{\"vmwareAccount\":{\"downloadToken\":"
                + jsonString(token32) + "}}";
        ScriptedHttpClient transport = new ScriptedHttpClient(
                new ResponseSpec(202, "application/json", body32));
        VcfInstallerClient client = new VcfInstallerClient(
                URI.create("https://installer.example"), "access-validation", transport);

        expectThrows(IllegalArgumentException.class,
                () -> client.updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(null, null), 0),
                "null downloadToken");
        expectThrows(IllegalArgumentException.class,
                () -> client.updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate(" \t\n", null), 0),
                "blank downloadToken");
        expectThrows(IllegalArgumentException.class,
                () -> client.updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate("x".repeat(33), null), 0),
                "downloadToken maximum");
        expectThrows(IllegalArgumentException.class,
                () -> client.updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate("valid", null), -1),
                "negative retry count");
        expectThrows(IllegalArgumentException.class,
                () -> client.updateDepotSettings(
                        new VcfInstallerClient.DepotUpdate("valid", null), 6),
                "retry count above five");
        assertEquals(0, transport.requests().size(),
                "invalid input must fail before transport");

        VcfInstallerClient.DepotSettings result = client.updateDepotSettings(
                new VcfInstallerClient.DepotUpdate(token32, null), 0);
        assertEquals(token32, result.downloadToken(), "32-character token is valid");
        assertEquals(1, transport.requests().size(), "boundary request count");
        assertSameSnapshots(transport.requests().get(0), transport.requests().get(0),
                operation.path());
    }

    private static VcfInstallerClient client(URI baseUri, String accessToken) {
        HttpClient http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(2))
                .build();
        return new VcfInstallerClient(baseUri, accessToken, http);
    }

    private static OperationContract readPinnedContract(Path root) throws IOException {
        String contract = Files.readString(root.resolve("docs/contract.json"));
        String operationId = extract(contract,
                "\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        String method = extract(contract,
                "\\\"method\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        String path = extract(contract,
                "\\\"path\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        assertEquals("updateDepotSettings", operationId, "contract operationId");
        assertEquals("PUT", method, "contract method");
        assertEquals("/v1/system/settings/depot", path, "contract path");
        assertTrue(contract.contains("c3f3b52c845dd967cabbc21680e893292077d5ba"),
                "contract must be pinned to the researched repository commit");
        return new OperationContract(operationId, method, path);
    }

    private static String extract(String text, String expression) {
        Matcher matcher = Pattern.compile(expression).matcher(text);
        if (!matcher.find()) throw new AssertionError("contract field missing: " + expression);
        return matcher.group(1);
    }

    private static void assertWire(List<LoggedRequest> requests,
                                   String accessToken, String expectedBody) {
        byte[] expectedBytes = expectedBody.getBytes(StandardCharsets.UTF_8);
        assertTrue(!requests.isEmpty(), "at least one request expected");
        for (LoggedRequest request : requests) {
            assertEquals("PUT", request.method(), "request method");
            assertEquals("/v1/system/settings/depot", request.rawPath(), "raw path");
            assertEquals(null, request.rawQuery(), "query string must be absent");
            assertEquals(List.of("application/json"), request.accept(), "Accept header");
            assertEquals(List.of("Bearer " + accessToken), request.authorization(),
                    "Authorization header");
            assertEquals(List.of("application/json"), request.contentType(),
                    "Content-Type header");
            assertEquals(List.of(Integer.toString(expectedBytes.length)),
                    request.contentLength(), "fixed Content-Length");
            assertEquals(List.of(), request.transferEncoding(),
                    "chunked transfer encoding must not be used");
            assertTrue(Arrays.equals(expectedBytes, request.body()),
                    "compact UTF-8 body must match exact contract shape");
            String body = new String(request.body(), StandardCharsets.UTF_8);
            for (String unset : List.of("username", "password", "status", "message",
                    "offlineAccount", "depotConfiguration")) {
                assertTrue(!body.contains("\"" + unset + "\""),
                        "unset optional property leaked: " + unset);
            }
            assertTrue(!body.contains(":null") && !body.contains(":\"\"")
                            && !body.contains(":{}"),
                    "unset optionals must be omitted, not empty");
        }
    }

    private static void assertSameAttempts(LoggedRequest first, LoggedRequest next) {
        assertEquals(first.method(), next.method(), "retry method identical");
        assertEquals(first.rawPath(), next.rawPath(), "retry path identical");
        assertEquals(first.rawQuery(), next.rawQuery(), "retry query identical");
        assertEquals(first.accept(), next.accept(), "retry Accept identical");
        assertEquals(first.authorization(), next.authorization(),
                "retry Authorization identical");
        assertEquals(first.contentType(), next.contentType(),
                "retry Content-Type identical");
        assertEquals(first.contentLength(), next.contentLength(),
                "retry Content-Length identical");
        assertTrue(Arrays.equals(first.body(), next.body()), "retry body bytes identical");
    }

    private static void assertSameSnapshots(RequestSnapshot first, RequestSnapshot next,
                                            String expectedPath) {
        assertEquals(first.uri(), next.uri(), "transport retry URI identical");
        assertEquals(expectedPath, first.uri().getRawPath(), "transport raw path");
        assertEquals(null, first.uri().getRawQuery(), "transport query absent");
        assertEquals("PUT", first.method(), "transport method");
        assertEquals(first.method(), next.method(), "transport retry method identical");
        assertEquals(first.headers().map(), next.headers().map(),
                "transport retry headers identical");
        assertEquals(List.of("application/json"), first.headers().allValues("Accept"),
                "transport Accept header");
        assertEquals(List.of("application/json"), first.headers().allValues("Content-Type"),
                "transport Content-Type header");
        assertEquals(1, first.headers().allValues("Authorization").size(),
                "one Authorization header");
        assertEquals((long) first.body().length, first.contentLength(),
                "fixed body publisher length");
        assertEquals(first.contentLength(), next.contentLength(),
                "retry content length identical");
        assertTrue(Arrays.equals(first.body(), next.body()),
                "transport retry body bytes identical");
    }

    private static void assertSnapshotWire(RequestSnapshot request, String expectedPath,
                                           String accessToken, String expectedBody) {
        byte[] expectedBytes = expectedBody.getBytes(StandardCharsets.UTF_8);
        assertEquals(expectedPath, request.uri().getRawPath(), "scripted raw path");
        assertEquals(null, request.uri().getRawQuery(), "scripted query absent");
        assertEquals("PUT", request.method(), "scripted method");
        assertEquals(List.of("application/json"), request.headers().allValues("Accept"),
                "scripted Accept header");
        assertEquals(List.of("Bearer " + accessToken),
                request.headers().allValues("Authorization"),
                "scripted Authorization header");
        assertEquals(List.of("application/json"),
                request.headers().allValues("Content-Type"),
                "scripted Content-Type header");
        assertEquals((long) expectedBytes.length, request.contentLength(),
                "scripted fixed content length");
        assertTrue(Arrays.equals(expectedBytes, request.body()),
                "scripted compact body bytes");
    }

    private static void assertPersistedLog(Path log, List<LoggedRequest> requests)
            throws IOException {
        List<String> lines = Files.readAllLines(log, StandardCharsets.UTF_8);
        assertEquals(requests.size(), lines.size(), "one flushed JSONL entry per request");
        for (int index = 0; index < lines.size(); index++) {
            String expectedBody64 = Base64.getEncoder().encodeToString(
                    requests.get(index).body());
            assertTrue(lines.get(index).startsWith("{\"method\":\"PUT\","),
                    "request log entry must be JSONL");
            assertTrue(lines.get(index).contains(
                            "\"rawTarget\":\"/v1/system/settings/depot\""),
                    "request log exact raw target");
            assertTrue(lines.get(index).contains(
                            "\"bodyBase64\":" + jsonString(expectedBody64)),
                    "request log exact body bytes");
        }
    }

    private static void assertNoSecrets(Throwable error, String... secrets) {
        String message = String.valueOf(error.getMessage());
        for (String secret : secrets) {
            assertTrue(secret == null || secret.isEmpty() || !message.contains(secret),
                    "exception message leaked a credential");
        }
    }

    private static String jsonStrings(List<String> values) {
        List<String> encoded = new ArrayList<>();
        for (String value : values) encoded.add(jsonString(value));
        return "[" + String.join(",", encoded) + "]";
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            switch (ch) {
                case '\"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (ch < 0x20 || Character.isSurrogate(ch)) {
                        result.append(String.format("\\u%04x", (int) ch));
                    } else {
                        result.append(ch);
                    }
                }
            }
        }
        return result.append('\"').toString();
    }

    @FunctionalInterface
    private interface ThrowingAction { void run() throws Exception; }

    private static <T extends Throwable> T expectThrows(Class<T> type,
                                                        ThrowingAction action,
                                                        String message) throws Exception {
        try {
            action.run();
        } catch (Throwable error) {
            if (type.isInstance(error)) return type.cast(error);
            throw new AssertionError(message + ": wrong exception " + error, error);
        }
        throw new AssertionError(message + ": no exception thrown");
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(message + " (expected=" + expected
                    + ", actual=" + actual + ")");
        }
    }
}
