import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ProxySelector;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Flow;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/** Contract-derived loopback mock for the focused VCF Log Management workflow. */
final class MockVcfLogServer implements AutoCloseable {
    enum Scenario {
        PARTIAL_TEST_FAILURE,
        FIRST_STEP_FAILURE,
        SECOND_STEP_FAILURE,
        ALL_SUCCESS,
        AGENT_ID_MISMATCH,
        AGENT_VALUE_MISMATCH,
        AGENT_INVALID_SUCCESS,
        FORWARDER_ID_MISMATCH,
        FORWARDER_VALUE_MISMATCH
    }

    record Fixture(
            String token,
            String agentGroupId,
            boolean agentAutoUpdate,
            String forwarderId,
            boolean forwarderEnabled,
            String host,
            int port,
            String protocol,
            boolean sslEnabled,
            String transportProtocol,
            String errorCode,
            String errorMessage) {
    }

    record RequestLog(
            String operationId,
            String method,
            String rawTarget,
            Map<String, List<String>> headers,
            byte[] body,
            int status) {
        RequestLog {
            Map<String, List<String>> copied = new LinkedHashMap<>();
            headers.forEach((key, value) -> copied.put(key, List.copyOf(value)));
            headers = Collections.unmodifiableMap(copied);
            body = body.clone();
        }

        @Override
        public byte[] body() {
            return body.clone();
        }

        List<String> headerValues(String name) {
            return headers.getOrDefault(name.toLowerCase(Locale.ROOT), List.of());
        }
    }

    private record Route(String operationId, String method, String template) {
        boolean matches(String actualMethod, String rawPath) {
            if (!method.equals(actualMethod)) {
                return false;
            }
            String[] wanted = template.substring(1).split("/", -1);
            String[] actual = rawPath.substring(1).split("/", -1);
            if (wanted.length != actual.length) {
                return false;
            }
            for (int index = 0; index < wanted.length; index++) {
                String part = wanted[index];
                if (part.startsWith("{") && part.endsWith("}")) {
                    if (actual[index].isEmpty()) {
                        return false;
                    }
                } else if (!part.equals(actual[index])) {
                    return false;
                }
            }
            return true;
        }
    }

    private record Reply(int status, Object body) {
    }

    private final Fixture fixture;
    private final Scenario scenario;
    private final List<Route> routes;
    private final HttpServer server;
    private final ExecutorService executor;
    private final URI origin;
    private final HttpClient client;
    private final List<RequestLog> requests = new ArrayList<>();
    private boolean agentPatchAccepted;
    private boolean forwarderPatchAccepted;

    MockVcfLogServer(Path contract, Fixture fixture) throws IOException {
        this(contract, fixture, Scenario.PARTIAL_TEST_FAILURE);
    }

    MockVcfLogServer(Path contract, Fixture fixture, Scenario scenario) throws IOException {
        this.fixture = fixture;
        this.scenario = scenario;
        routes = loadRoutes(contract);
        HttpServer started = null;
        ExecutorService pool = null;
        URI selectedOrigin;
        HttpClient selectedClient;
        try {
            started = HttpServer.create(
                    new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
            pool = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(runnable, "vcf91-0193-contract-mock");
                thread.setDaemon(true);
                return thread;
            });
            started.setExecutor(pool);
            started.createContext("/", this::serve);
            started.start();
            selectedOrigin = URI.create(
                    "http://127.0.0.1:" + started.getAddress().getPort());
            selectedClient = HttpClient.newBuilder()
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .proxy(ProxySelector.of(null))
                    .build();
        } catch (IOException | RuntimeException loopbackUnavailable) {
            if (started != null) {
                started.stop(0);
            }
            if (pool != null) {
                pool.shutdownNow();
            }
            started = null;
            pool = null;
            selectedOrigin = URI.create("http://127.0.0.1");
            selectedClient = new HandlerHttpClient(this);
        }
        server = started;
        executor = pool;
        origin = selectedOrigin;
        client = selectedClient;
    }

    URI origin() {
        return origin;
    }

    HttpClient client() {
        return client;
    }

    synchronized List<RequestLog> requests() {
        return List.copyOf(requests);
    }

    Set<String> operationIds() {
        LinkedHashSet<String> result = new LinkedHashSet<>();
        routes.forEach(route -> result.add(route.operationId()));
        return Collections.unmodifiableSet(result);
    }

    @Override
    public void close() {
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
    }

    private void serve(HttpExchange exchange) throws IOException {
        byte[] body = exchange.getRequestBody().readAllBytes();
        Reply reply = handle(
                exchange.getRequestMethod(),
                exchange.getRequestURI(),
                exchange.getRequestHeaders(),
                body);
        send(exchange, reply);
    }

    private synchronized Reply handle(
            String method, URI uri, Headers headers, byte[] body) {
        String rawPath = uri.getRawPath();
        Route route = uri.getRawQuery() == null ? findRoute(method, rawPath) : null;
        Reply reply = dispatch(route, rawPath, headers, body);
        requests.add(new RequestLog(
                route == null ? null : route.operationId(),
                method,
                uri.toASCIIString(),
                copyHeaders(headers),
                body,
                reply.status()));
        return reply;
    }

    private Reply dispatch(Route route, String rawPath, Headers headers, byte[] body) {
        if (route == null) {
            return error(404, "API_ERROR", "operation is not in the pinned contract");
        }
        if (!List.of(fixture.token()).equals(headers.get("X-JWT-Token"))) {
            return error(403, "SECURITY_ERROR", "token rejected");
        }
        if (!List.of("application/json").equals(headers.get("Accept"))
                || !List.of("application/json").equals(headers.get("Content-Type"))) {
            return error(400, "VALIDATION_ERROR", "media headers are invalid");
        }

        Map<String, Object> json;
        try {
            json = TestJson.object(TestJson.parse(
                    new String(body, StandardCharsets.UTF_8)));
        } catch (IOException malformed) {
            return error(400, "JSON_FORMAT_ERROR", "request JSON is invalid");
        }

        return switch (route.operationId()) {
            case "patchUpdateAgentGroupConfig" -> patchAgentGroup(rawPath, json);
            case "patchLogForwarder" -> patchForwarder(rawPath, json);
            case "testLogForwarderConnection" -> testForwarder(json);
            default -> error(404, "API_ERROR", "operation is not in the pinned contract");
        };
    }

    private Reply patchAgentGroup(String rawPath, Map<String, Object> body) {
        if (agentPatchAccepted
                || !decodeLastSegment(rawPath).equals(fixture.agentGroupId())
                || !body.keySet().equals(Set.of("autoUpdate"))
                || !Boolean.valueOf(fixture.agentAutoUpdate()).equals(body.get("autoUpdate"))) {
            return error(400, "VALIDATION_ERROR", "agent-group patch is invalid");
        }
        if (scenario == Scenario.FIRST_STEP_FAILURE) {
            return error(500, fixture.errorCode(), fixture.errorMessage());
        }
        agentPatchAccepted = true;
        if (scenario == Scenario.AGENT_INVALID_SUCCESS) {
            return new Reply(200, List.of("not", "an", "object"));
        }
        LinkedHashMap<String, Object> response = new LinkedHashMap<>();
        response.put("id", scenario == Scenario.AGENT_ID_MISMATCH
                ? fixture.agentGroupId() + "-other"
                : fixture.agentGroupId());
        response.put("autoUpdate", scenario == Scenario.AGENT_VALUE_MISMATCH
                ? !fixture.agentAutoUpdate()
                : fixture.agentAutoUpdate());
        return new Reply(200, response);
    }

    private Reply patchForwarder(String rawPath, Map<String, Object> body) {
        if (!agentPatchAccepted
                || forwarderPatchAccepted
                || !decodeLastSegment(rawPath).equals(fixture.forwarderId())
                || !body.keySet().equals(Set.of("enabled"))
                || !Boolean.valueOf(fixture.forwarderEnabled()).equals(body.get("enabled"))) {
            return error(400, "VALIDATION_ERROR", "log-forwarder patch is invalid");
        }
        if (scenario == Scenario.SECOND_STEP_FAILURE) {
            return error(502, fixture.errorCode(), fixture.errorMessage());
        }
        forwarderPatchAccepted = true;
        LinkedHashMap<String, Object> response = new LinkedHashMap<>();
        response.put("id", scenario == Scenario.FORWARDER_ID_MISMATCH
                ? fixture.forwarderId() + "-other"
                : fixture.forwarderId());
        response.put("enabled", scenario == Scenario.FORWARDER_VALUE_MISMATCH
                ? !fixture.forwarderEnabled()
                : fixture.forwarderEnabled());
        return new Reply(200, response);
    }

    private Reply testForwarder(Map<String, Object> body) {
        Set<String> expectedKeys = new LinkedHashSet<>(List.of(
                "host", "port", "protocol", "sslEnabled", "transportProtocol"));
        if (!agentPatchAccepted
                || !forwarderPatchAccepted
                || !body.keySet().equals(expectedKeys)
                || !fixture.host().equals(body.get("host"))
                || !Long.valueOf(fixture.port()).equals(body.get("port"))
                || !fixture.protocol().equals(body.get("protocol"))
                || !Boolean.valueOf(fixture.sslEnabled()).equals(body.get("sslEnabled"))
                || !fixture.transportProtocol().equals(body.get("transportProtocol"))) {
            return error(400, "VALIDATION_ERROR", "connection probe is invalid");
        }
        return scenario == Scenario.ALL_SUCCESS
                ? new Reply(200, null)
                : error(502, fixture.errorCode(), fixture.errorMessage());
    }

    private Route findRoute(String method, String rawPath) {
        for (Route route : routes) {
            if (route.matches(method, rawPath)) {
                return route;
            }
        }
        return null;
    }

    private static String decodeLastSegment(String rawPath) {
        String segment = rawPath.substring(rawPath.lastIndexOf('/') + 1);
        return URLDecoder.decode(segment.replace("+", "%2B"), StandardCharsets.UTF_8);
    }

    private static Reply error(int status, String code, String message) {
        LinkedHashMap<String, Object> body = new LinkedHashMap<>();
        body.put("errorCode", code);
        body.put("errorMessage", message);
        return new Reply(status, body);
    }

    private static void send(HttpExchange exchange, Reply reply) throws IOException {
        byte[] payload = TestJson.write(reply.body()).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(reply.status(), payload.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(payload);
        }
    }

    private static Map<String, List<String>> copyHeaders(Headers headers) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        headers.forEach((key, values) -> copy.put(
                key.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return copy;
    }

    private static List<Route> loadRoutes(Path contract) throws IOException {
        Map<String, Object> root = TestJson.object(TestJson.parse(
                Files.readString(contract, StandardCharsets.UTF_8)));
        List<Object> operations = TestJson.array(root.get("operations"));
        ArrayList<Route> result = new ArrayList<>();
        LinkedHashSet<String> ids = new LinkedHashSet<>();
        for (Object value : operations) {
            Map<String, Object> operation = TestJson.object(value);
            String operationId = requiredString(operation, "operationId");
            String method = requiredString(operation, "method");
            String path = requiredString(operation, "path");
            if (!ids.add(operationId)) {
                throw new IOException("duplicate operationId in focused contract");
            }
            result.add(new Route(operationId, method, path));
        }
        if (result.isEmpty()) {
            throw new IOException("focused contract contains no operations");
        }
        return List.copyOf(result);
    }

    private static String requiredString(Map<String, Object> object, String key)
            throws IOException {
        Object value = object.get(key);
        if (!(value instanceof String text) || text.isBlank()) {
            throw new IOException("focused contract has invalid " + key);
        }
        return text;
    }

    /**
     * Uses the same contract-derived dispatcher and request log only when the
     * execution sandbox prohibits binding the normal loopback listener.
     */
    private static final class HandlerHttpClient extends HttpClient {
        private final MockVcfLogServer owner;
        private final SSLContext sslContext;

        HandlerHttpClient(MockVcfLogServer owner) {
            this.owner = owner;
            try {
                sslContext = SSLContext.getDefault();
            } catch (NoSuchAlgorithmException unavailable) {
                throw new IllegalStateException(unavailable);
            }
        }

        @Override
        public Optional<CookieHandler> cookieHandler() {
            return Optional.empty();
        }

        @Override
        public Optional<Duration> connectTimeout() {
            return Optional.empty();
        }

        @Override
        public Redirect followRedirects() {
            return Redirect.NEVER;
        }

        @Override
        public Optional<ProxySelector> proxy() {
            return Optional.of(ProxySelector.of(null));
        }

        @Override
        public SSLContext sslContext() {
            return sslContext;
        }

        @Override
        public SSLParameters sslParameters() {
            return new SSLParameters();
        }

        @Override
        public Optional<Authenticator> authenticator() {
            return Optional.empty();
        }

        @Override
        public Version version() {
            return Version.HTTP_1_1;
        }

        @Override
        public Optional<java.util.concurrent.Executor> executor() {
            return Optional.empty();
        }

        @Override
        public <T> HttpResponse<T> send(
                HttpRequest request, HttpResponse.BodyHandler<T> responseBodyHandler)
                throws IOException, InterruptedException {
            byte[] requestBody = publish(request.bodyPublisher());
            Headers requestHeaders = new Headers();
            request.headers().map().forEach(
                    (name, values) -> requestHeaders.put(name, new ArrayList<>(values)));
            URI target = URI.create(request.uri().getRawPath()
                    + (request.uri().getRawQuery() == null
                            ? ""
                            : "?" + request.uri().getRawQuery()));
            Reply reply = owner.handle(
                    request.method(), target, requestHeaders, requestBody);
            byte[] responseBytes = TestJson.write(reply.body())
                    .getBytes(StandardCharsets.UTF_8);
            HttpHeaders responseHeaders = HttpHeaders.of(
                    Map.of("content-type", List.of("application/json")),
                    (name, value) -> true);
            HttpResponse.ResponseInfo info = new HttpResponse.ResponseInfo() {
                @Override
                public int statusCode() {
                    return reply.status();
                }

                @Override
                public HttpHeaders headers() {
                    return responseHeaders;
                }

                @Override
                public Version version() {
                    return Version.HTTP_1_1;
                }
            };
            HttpResponse.BodySubscriber<T> subscriber = responseBodyHandler.apply(info);
            subscriber.onSubscribe(new Flow.Subscription() {
                @Override
                public void request(long count) {
                }

                @Override
                public void cancel() {
                }
            });
            subscriber.onNext(List.of(ByteBuffer.wrap(responseBytes)));
            subscriber.onComplete();
            try {
                T responseBody = subscriber.getBody().toCompletableFuture().get();
                return new MemoryResponse<>(
                        request, reply.status(), responseHeaders, responseBody);
            } catch (ExecutionException failed) {
                throw new IOException("response handler failed", failed.getCause());
            }
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> responseBodyHandler) {
            try {
                return CompletableFuture.completedFuture(send(request, responseBodyHandler));
            } catch (IOException | InterruptedException failed) {
                return CompletableFuture.failedFuture(failed);
            }
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler,
                HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
            return sendAsync(request, responseBodyHandler);
        }

        private static byte[] publish(Optional<HttpRequest.BodyPublisher> publisher)
                throws IOException, InterruptedException {
            if (publisher.isEmpty()) {
                return new byte[0];
            }
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            CompletableFuture<byte[]> complete = new CompletableFuture<>();
            publisher.get().subscribe(new Flow.Subscriber<>() {
                @Override
                public void onSubscribe(Flow.Subscription subscription) {
                    subscription.request(Long.MAX_VALUE);
                }

                @Override
                public void onNext(ByteBuffer item) {
                    byte[] chunk = new byte[item.remaining()];
                    item.get(chunk);
                    bytes.writeBytes(chunk);
                }

                @Override
                public void onError(Throwable throwable) {
                    complete.completeExceptionally(throwable);
                }

                @Override
                public void onComplete() {
                    complete.complete(bytes.toByteArray());
                }
            });
            try {
                return complete.get();
            } catch (ExecutionException failed) {
                throw new IOException("request publisher failed", failed.getCause());
            }
        }
    }

    private record MemoryResponse<T>(
            HttpRequest request,
            int statusCode,
            HttpHeaders headers,
            T body) implements HttpResponse<T> {
        @Override
        public Optional<HttpResponse<T>> previousResponse() {
            return Optional.empty();
        }

        @Override
        public Optional<SSLSession> sslSession() {
            return Optional.empty();
        }

        @Override
        public URI uri() {
            return request.uri();
        }

        @Override
        public HttpClient.Version version() {
            return HttpClient.Version.HTTP_1_1;
        }
    }
}
