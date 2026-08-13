import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
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
import java.util.Collections;
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

/** Contract-pinned loopback fixture for the two focused Log Management routes. */
final class MockVcfLogServer implements AutoCloseable {
    static final String LIST = "getAllLogForwarders";
    static final String CREATE = "createLogForwarder";

    record Fixture(String oldToken, String newToken, String suffix) {
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

    private record Reply(int status, String json) {
    }

    private static final Pattern PATH = Pattern.compile(
            "\\\"paths\\\"\\s*:\\s*\\{\\s*\\\"([^\\\"]+)\\\"\\s*:");
    private static final Pattern OPERATION = Pattern.compile(
            "\\\"(get|post|put|patch|delete)\\\"\\s*:\\s*\\{\\s*"
                    + "\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
    private static final Pattern NAME = Pattern.compile(
            "\\\"name\\\":\\\"([A-Za-z0-9._-]+)\\\"");
    private static final Set<String> EXPECTED_OPERATIONS =
            Set.of(LIST, CREATE);

    private final Fixture fixture;
    private final Map<String, String> routes;
    private final HttpServer server;
    private final ExecutorService executor;
    private final URI origin;
    private final HttpClient client;
    private final List<RequestLog> requests = new ArrayList<>();
    private final List<String> forwarders = new ArrayList<>();
    private final List<String> names = new ArrayList<>();

    private int successfulPosts;
    private boolean oldTokenExpired;

    MockVcfLogServer(Path contract, Fixture fixture) throws IOException {
        this.fixture = fixture;
        this.routes = loadRoutes(contract);
        String existingName = "archive-" + fixture.suffix();
        names.add(existingName);
        forwarders.add("{\"id\":\"existing-" + fixture.suffix()
                + "\",\"name\":\"" + existingName
                + "\",\"host\":\"archive." + fixture.suffix()
                + ".example\",\"enabled\":true,\"serverOnly\":\"preserve-"
                + fixture.suffix() + "\"}");

        HttpServer started = null;
        ExecutorService pool = null;
        URI selectedOrigin;
        HttpClient selectedClient;
        try {
            started = HttpServer.create(
                    new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
            pool = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(runnable, "vcf91-0190-contract-mock");
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

    synchronized List<String> names() {
        return List.copyOf(names);
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
        String rawTarget = exchange.getRequestURI().toASCIIString();
        String key = exchange.getRequestMethod() + " "
                + exchange.getRequestURI().getPath();
        String operationId = exchange.getRequestURI().getRawQuery() == null
                ? routes.get(key)
                : null;
        Reply reply;
        synchronized (this) {
            reply = dispatch(operationId, exchange.getRequestHeaders(), body);
            requests.add(new RequestLog(
                    operationId,
                    exchange.getRequestMethod(),
                    rawTarget,
                    copyHeaders(exchange.getRequestHeaders()),
                    body,
                    reply.status()));
        }
        send(exchange, reply);
    }

    private Reply dispatch(String operationId, Headers headers, byte[] body) {
        if (operationId == null) {
            return error(404, "API_ERROR", "operation is not in the pinned contract");
        }
        String token = oneHeader(headers, "X-JWT-Token");
        if (LIST.equals(operationId)) {
            if (!validToken(token)) {
                return expired();
            }
            return new Reply(200, "[" + String.join(",", forwarders) + "]");
        }
        if (CREATE.equals(operationId)) {
            if (fixture.oldToken().equals(token) && successfulPosts >= 1) {
                oldTokenExpired = true;
                return expired();
            }
            if (!validToken(token)) {
                return expired();
            }
            String json = new String(body, StandardCharsets.UTF_8);
            Matcher nameMatch = NAME.matcher(json);
            if (!json.startsWith("{") || !json.endsWith("}") || !nameMatch.find()) {
                return error(400, "JSON_FORMAT_ERROR", "request body is invalid");
            }
            String name = nameMatch.group(1);
            if (names.contains(name)) {
                return error(400, "FIELD_ERROR", "forwarder name already exists");
            }
            String created = "{\"id\":\"created-" + (successfulPosts + 1)
                    + "-" + fixture.suffix() + "\"," + json.substring(1);
            names.add(name);
            forwarders.add(created);
            successfulPosts++;
            return new Reply(201, created);
        }
        return error(404, "API_ERROR", "operation is not in the pinned contract");
    }

    private boolean validToken(String token) {
        if (fixture.oldToken().equals(token)) {
            return !oldTokenExpired;
        }
        return fixture.newToken().equals(token);
    }

    private static Reply expired() {
        return error(403, "SECURITY_ERROR", "access token expired");
    }

    private static Reply error(int status, String code, String message) {
        return new Reply(status, "{\"errorCode\":\"" + code
                + "\",\"errorMessage\":\"" + message + "\"}");
    }

    private static void send(HttpExchange exchange, Reply reply) throws IOException {
        byte[] bytes = reply.json().getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(reply.status(), bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static String oneHeader(Headers headers, String name) {
        List<String> values = headers.get(name);
        return values != null && values.size() == 1 ? values.get(0) : null;
    }

    private static Map<String, List<String>> copyHeaders(Headers headers) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        headers.forEach((key, values) -> copy.put(
                key.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return copy;
    }

    private static Map<String, String> loadRoutes(Path contract) throws IOException {
        String json = Files.readString(contract, StandardCharsets.UTF_8);
        Matcher pathMatcher = PATH.matcher(json);
        if (!pathMatcher.find()) {
            throw new IOException("focused contract does not contain a path");
        }
        String path = pathMatcher.group(1);
        if (pathMatcher.find()) {
            throw new IOException("focused contract must contain exactly one path");
        }

        Matcher operationMatcher = OPERATION.matcher(json);
        Map<String, String> result = new LinkedHashMap<>();
        Set<String> operationIds = new LinkedHashSet<>();
        while (operationMatcher.find()) {
            String method = operationMatcher.group(1).toUpperCase(Locale.ROOT);
            String operationId = operationMatcher.group(2);
            if (!operationIds.add(operationId)
                    || result.put(method + " " + path, operationId) != null) {
                throw new IOException("duplicate operation in focused contract");
            }
        }
        if (!operationIds.equals(EXPECTED_OPERATIONS)) {
            throw new IOException("focused contract operation set is not pinned");
        }
        return Map.copyOf(result);
    }

    /**
     * Handler-backed boundary used only when the execution sandbox forbids
     * binding to 127.0.0.1. It invokes the same contract-derived route table,
     * state machine, and request logger as the normal loopback listener.
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
            MemoryExchange exchange = new MemoryExchange(
                    request.method(), request.uri(), requestHeaders, requestBody);
            owner.serve(exchange);

            HttpHeaders responseHeaders = HttpHeaders.of(
                    exchange.responseHeaders(), (name, value) -> true);
            HttpResponse.ResponseInfo info = new HttpResponse.ResponseInfo() {
                @Override
                public int statusCode() {
                    return exchange.statusCode();
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
            subscriber.onNext(List.of(ByteBuffer.wrap(exchange.responseBytes())));
            subscriber.onComplete();
            try {
                T responseBody = subscriber.getBody().toCompletableFuture().get();
                return new MemoryResponse<>(
                        request, exchange.statusCode(), responseHeaders, responseBody);
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

    private static final class MemoryExchange extends HttpExchange {
        private final String method;
        private final URI uri;
        private final Headers requestHeaders;
        private final Headers responseHeaders = new Headers();
        private final ByteArrayInputStream requestBody;
        private final ByteArrayOutputStream responseBody = new ByteArrayOutputStream();
        private final Map<String, Object> attributes = new LinkedHashMap<>();
        private int statusCode = -1;

        MemoryExchange(String method, URI uri, Headers requestHeaders, byte[] requestBody) {
            this.method = method;
            String target = uri.getRawPath();
            if (uri.getRawQuery() != null) {
                target += "?" + uri.getRawQuery();
            }
            this.uri = URI.create(target);
            this.requestHeaders = requestHeaders;
            this.requestBody = new ByteArrayInputStream(requestBody);
        }

        int statusCode() {
            return statusCode;
        }

        byte[] responseBytes() {
            return responseBody.toByteArray();
        }

        Headers responseHeaders() {
            return responseHeaders;
        }

        @Override
        public Headers getRequestHeaders() {
            return requestHeaders;
        }

        @Override
        public Headers getResponseHeaders() {
            return responseHeaders;
        }

        @Override
        public URI getRequestURI() {
            return uri;
        }

        @Override
        public String getRequestMethod() {
            return method;
        }

        @Override
        public com.sun.net.httpserver.HttpContext getHttpContext() {
            return null;
        }

        @Override
        public void close() {
        }

        @Override
        public InputStream getRequestBody() {
            return requestBody;
        }

        @Override
        public OutputStream getResponseBody() {
            return responseBody;
        }

        @Override
        public void sendResponseHeaders(int responseCode, long responseLength) {
            statusCode = responseCode;
        }

        @Override
        public InetSocketAddress getRemoteAddress() {
            return new InetSocketAddress("127.0.0.1", 1);
        }

        @Override
        public int getResponseCode() {
            return statusCode;
        }

        @Override
        public InetSocketAddress getLocalAddress() {
            return new InetSocketAddress("127.0.0.1", 0);
        }

        @Override
        public String getProtocol() {
            return "HTTP/1.1";
        }

        @Override
        public Object getAttribute(String name) {
            return attributes.get(name);
        }

        @Override
        public void setAttribute(String name, Object value) {
            attributes.put(name, value);
        }

        @Override
        public void setStreams(InputStream input, OutputStream output) {
            throw new UnsupportedOperationException();
        }

        @Override
        public com.sun.net.httpserver.HttpPrincipal getPrincipal() {
            return null;
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
