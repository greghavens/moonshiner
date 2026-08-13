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
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Flow;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/** Contract-derived loopback mock for the focused VCF Log Management workflow. */
final class MockVcfLogServer implements AutoCloseable {
    record Fixture(
            String token,
            String name,
            String host,
            int port,
            String protocol,
            boolean sslEnabled,
            String transportProtocol,
            boolean enabled,
            int precheckStatus,
            String errorCode,
            String errorMessage,
            String createdId) {
    }

    record RequestLog(
            String operationId,
            String method,
            String rawTarget,
            Map<String, List<String>> headers,
            byte[] body,
            int responseStatus) {
        RequestLog {
            headers = Map.copyOf(headers);
            body = body.clone();
        }

        @Override
        public byte[] body() {
            return body.clone();
        }

        List<String> headerValues(String lowerCaseName) {
            return headers.getOrDefault(
                    lowerCaseName.toLowerCase(Locale.ROOT), List.of());
        }
    }

    private record Route(String operationId, String method, String path) {
        boolean matches(String actualMethod, String rawTarget) {
            return method.equals(actualMethod) && path.equals(rawTarget);
        }
    }

    private record Reply(int status, String contentType, byte[] body) {
        Reply {
            body = body.clone();
        }

        @Override
        public byte[] body() {
            return body.clone();
        }
    }

    private static final Pattern CONTRACT_OPERATION = Pattern.compile(
            "\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"\\s*,"
                    + "\\s*\\\"method\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"\\s*,"
                    + "\\s*\\\"path\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");

    private final Fixture fixture;
    private final List<Route> routes;
    private final List<RequestLog> requestLog = new ArrayList<>();
    private final HttpServer server;
    private final ExecutorService executor;
    private final URI origin;
    private final HttpClient client;
    private boolean precheckPassed;
    private int creationCount;

    MockVcfLogServer(Path contract, Fixture fixture) throws IOException {
        this.fixture = fixture;
        this.routes = loadRoutes(contract);
        HttpServer started = null;
        ExecutorService pool = null;
        URI selectedOrigin;
        HttpClient selectedClient;
        try {
            started = HttpServer.create(new InetSocketAddress(
                    InetAddress.getByName("127.0.0.1"), 0), 0);
            pool = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(runnable, "vcf91-contract-mock");
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
        this.server = started;
        this.executor = pool;
        this.origin = selectedOrigin;
        this.client = selectedClient;
    }

    URI origin() {
        return origin;
    }

    HttpClient client() {
        return client;
    }

    Set<String> operationIds() {
        LinkedHashSet<String> ids = new LinkedHashSet<>();
        for (Route route : routes) {
            ids.add(route.operationId());
        }
        return Set.copyOf(ids);
    }

    synchronized int creationCount() {
        return creationCount;
    }

    synchronized List<RequestLog> requests() {
        return List.copyOf(requestLog);
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
        String rawTarget = uri.toASCIIString();
        Route route = uri.getRawQuery() == null
                ? findRoute(method, rawTarget)
                : null;
        Reply reply = route == null
                ? new Reply(404, null, new byte[0])
                : dispatch(route, headers, body);
        requestLog.add(new RequestLog(
                route == null ? null : route.operationId(),
                method,
                rawTarget,
                copyHeaders(headers),
                body,
                reply.status()));
        return reply;
    }

    private synchronized Reply dispatch(
            Route route, Headers headers, byte[] body) {
        if (!exactHeader(headers, "X-JWT-Token", fixture.token())
                || !exactHeader(headers, "Accept", "application/json")
                || !exactHeader(headers, "Content-Type", "application/json")) {
            return error(400, "VALIDATION_ERROR", "required headers are invalid");
        }
        return switch (route.operationId()) {
            case "testLogForwarderConnection" -> precheck(body);
            case "createLogForwarder" -> create(body);
            default -> new Reply(404, null, new byte[0]);
        };
    }

    private Reply precheck(byte[] body) {
        if (!Arrays.equals(body, expectedPrecheckBody(fixture))) {
            return error(400, "VALIDATION_ERROR", "precheck body is invalid");
        }
        if (fixture.precheckStatus() != 200) {
            return error(
                    fixture.precheckStatus(),
                    fixture.errorCode(),
                    fixture.errorMessage());
        }
        precheckPassed = true;
        return new Reply(200, null, new byte[0]);
    }

    private Reply create(byte[] body) {
        if (!precheckPassed) {
            return error(409, "VALIDATION_ERROR", "precheck has not succeeded");
        }
        if (!Arrays.equals(body, expectedCreateBody(fixture))) {
            return error(400, "VALIDATION_ERROR", "create body is invalid");
        }
        creationCount++;
        String response = "{\"id\":" + quote(fixture.createdId())
                + ",\"name\":" + quote(fixture.name()) + "}";
        return new Reply(
                201,
                "application/json",
                response.getBytes(StandardCharsets.UTF_8));
    }

    static byte[] expectedPrecheckBody(Fixture value) {
        String json = "{\"host\":" + quote(value.host())
                + ",\"port\":" + value.port()
                + ",\"protocol\":" + quote(value.protocol())
                + ",\"sslEnabled\":" + value.sslEnabled()
                + ",\"transportProtocol\":"
                + quote(value.transportProtocol()) + "}";
        return json.getBytes(StandardCharsets.UTF_8);
    }

    static byte[] expectedCreateBody(Fixture value) {
        String json = "{\"enabled\":" + value.enabled()
                + ",\"host\":" + quote(value.host())
                + ",\"name\":" + quote(value.name())
                + ",\"port\":" + value.port()
                + ",\"protocol\":" + quote(value.protocol())
                + ",\"sslEnabled\":" + value.sslEnabled()
                + ",\"transportProtocol\":"
                + quote(value.transportProtocol()) + "}";
        return json.getBytes(StandardCharsets.UTF_8);
    }

    private Route findRoute(String method, String rawTarget) {
        for (Route route : routes) {
            if (route.matches(method, rawTarget)) {
                return route;
            }
        }
        return null;
    }

    private static List<Route> loadRoutes(Path contract) throws IOException {
        String source = Files.readString(contract, StandardCharsets.UTF_8);
        Matcher matcher = CONTRACT_OPERATION.matcher(source);
        ArrayList<Route> result = new ArrayList<>();
        LinkedHashSet<String> ids = new LinkedHashSet<>();
        while (matcher.find()) {
            Route route = new Route(
                    matcher.group(1), matcher.group(2), matcher.group(3));
            if (!ids.add(route.operationId())) {
                throw new IOException("duplicate operationId in focused contract");
            }
            result.add(route);
        }
        if (result.isEmpty()) {
            throw new IOException("focused contract contains no operations");
        }
        return List.copyOf(result);
    }

    private static Reply error(int status, String code, String message) {
        String response = "{\"errorCode\":" + quote(code)
                + ",\"errorMessage\":" + quote(message) + "}";
        return new Reply(
                status,
                "application/json",
                response.getBytes(StandardCharsets.UTF_8));
    }

    private static boolean exactHeader(
            Headers headers, String name, String expected) {
        return List.of(expected).equals(headers.get(name));
    }

    private static Map<String, List<String>> copyHeaders(Headers headers) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        headers.forEach((key, values) -> copy.put(
                key.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return copy;
    }

    private static void send(HttpExchange exchange, Reply reply)
            throws IOException {
        byte[] body = reply.body();
        if (reply.contentType() != null) {
            exchange.getResponseHeaders().set(
                    "Content-Type", reply.contentType());
        }
        if (body.length == 0) {
            exchange.sendResponseHeaders(reply.status(), -1);
        } else {
            exchange.sendResponseHeaders(reply.status(), body.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(body);
            }
        }
        exchange.close();
    }

    private static String quote(String value) {
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char c = value.charAt(index);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    /**
     * Uses the identical contract-derived dispatcher and request log only when
     * the execution sandbox prohibits binding the normal loopback listener.
     */
    private static final class HandlerHttpClient extends HttpClient {
        private final MockVcfLogServer owner;
        private final SSLContext sslContext;

        HandlerHttpClient(MockVcfLogServer owner) {
            this.owner = owner;
            try {
                this.sslContext = SSLContext.getDefault();
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
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler)
                throws IOException, InterruptedException {
            byte[] requestBody = publish(request.bodyPublisher());
            Headers requestHeaders = new Headers();
            request.headers().map().forEach((name, values) ->
                    requestHeaders.put(name, new ArrayList<>(values)));
            requestHeaders.put(
                    "Content-Length",
                    List.of(Integer.toString(requestBody.length)));
            URI target = URI.create(request.uri().getRawPath()
                    + (request.uri().getRawQuery() == null
                            ? ""
                            : "?" + request.uri().getRawQuery()));
            Reply reply = owner.handle(
                    request.method(), target, requestHeaders, requestBody);

            Map<String, List<String>> headerMap = reply.contentType() == null
                    ? Map.of()
                    : Map.of("content-type", List.of(reply.contentType()));
            HttpHeaders responseHeaders = HttpHeaders.of(
                    headerMap, (name, value) -> true);
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
            HttpResponse.BodySubscriber<T> subscriber =
                    responseBodyHandler.apply(info);
            subscriber.onSubscribe(new Flow.Subscription() {
                @Override
                public void request(long count) {
                }

                @Override
                public void cancel() {
                }
            });
            byte[] responseBody = reply.body();
            if (responseBody.length != 0) {
                subscriber.onNext(List.of(ByteBuffer.wrap(responseBody)));
            }
            subscriber.onComplete();
            try {
                T converted = subscriber.getBody().toCompletableFuture().get();
                return new MemoryResponse<>(
                        request,
                        reply.status(),
                        responseHeaders,
                        converted);
            } catch (ExecutionException failed) {
                throw new IOException(
                        "response handler failed", failed.getCause());
            }
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler) {
            try {
                return CompletableFuture.completedFuture(
                        send(request, responseBodyHandler));
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

        private static byte[] publish(
                Optional<HttpRequest.BodyPublisher> publisher)
                throws IOException, InterruptedException {
            if (publisher.isEmpty()) {
                return new byte[0];
            }
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            CompletableFuture<byte[]> completed = new CompletableFuture<>();
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
                    completed.completeExceptionally(throwable);
                }

                @Override
                public void onComplete() {
                    completed.complete(bytes.toByteArray());
                }
            });
            try {
                return completed.get();
            } catch (ExecutionException failed) {
                throw new IOException(
                        "request publisher failed", failed.getCause());
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

    @Override
    public void close() {
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
    }
}
