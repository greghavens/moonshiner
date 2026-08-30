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
import java.net.URLEncoder;
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
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Flow;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/**
 * Loopback-only vCenter fixture whose complete route table is loaded from the
 * focused OpenAPI projection.
 */
final class MockVcenterServer implements AutoCloseable {
    static final String TOKEN_ISSUE = "Vcenter.Authentication.Token_issue";
    static final String VM_LIST = "Vcenter.VM_list";
    static final String HOST_LIST = "Vcenter.Host_list";
    static final String GRANT_TYPE =
            "urn:ietf:params:oauth:grant-type:token-exchange";

    record Fixture(
            String initialAccessToken,
            String replacementAccessToken,
            String subjectToken,
            String subjectTokenType,
            String suffix) {
    }

    record RequestLog(
            String operationId,
            String method,
            String rawTarget,
            Map<String, List<String>> headers,
            byte[] body) {
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
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue();
                }
            }
            return List.of();
        }

        String oneHeader(String name) {
            List<String> values = headerValues(name);
            return values.size() == 1 ? values.get(0) : null;
        }
    }

    private static final Pattern OPERATION = Pattern.compile(
            "\\{\\s*\"operationId\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"method\"\\s*:\\s*\"([A-Z]+)\"\\s*,"
                    + "\\s*\"path\"\\s*:\\s*\"[^\"]+\"\\s*,"
                    + "\\s*\"wire_path\"\\s*:\\s*\"([^\"]+)\"");
    private static final Set<String> EXPECTED_OPERATIONS =
            Set.of(TOKEN_ISSUE, VM_LIST, HOST_LIST);

    private final Fixture fixture;
    private final Map<String, String> routeToOperation;
    private final HttpServer server;
    private final ExecutorService executor;
    private final URI origin;
    private final HttpClient client;
    private final List<RequestLog> requests = new ArrayList<>();

    private String validAccessToken;
    private boolean expiredInitialToken;
    private int vmSuccesses;
    private int hostSuccesses;

    MockVcenterServer(Path contract, Fixture fixture) throws IOException {
        this.fixture = fixture;
        this.routeToOperation = loadRoutes(contract);
        this.validAccessToken = fixture.initialAccessToken();
        HttpServer started = null;
        ExecutorService pool = null;
        URI selectedOrigin;
        HttpClient selectedClient;
        try {
            started = HttpServer.create(
                    new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
            pool = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(runnable, "mock-vcenter");
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

    synchronized List<RequestLog> requests() {
        return List.copyOf(requests);
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
        String key = exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath();
        String operationId = routeToOperation.get(key);
        synchronized (this) {
            requests.add(new RequestLog(
                    operationId,
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().toASCIIString(),
                    copyHeaders(exchange.getRequestHeaders()),
                    body));
        }

        if (operationId == null) {
            sendJson(exchange, 404, "{\"error_type\":\"NOT_FOUND\"}");
            return;
        }
        switch (operationId) {
            case VM_LIST -> serveVMs(exchange, body);
            case HOST_LIST -> serveHosts(exchange, body);
            case TOKEN_ISSUE -> serveToken(exchange, body);
            default -> sendJson(exchange, 404, "{\"error_type\":\"NOT_FOUND\"}");
        }
    }

    private synchronized void serveVMs(HttpExchange exchange, byte[] body)
            throws IOException {
        if (!validCollectionRequest(exchange, body)) {
            sendUnauthenticated(exchange);
            return;
        }
        List<String> items = new ArrayList<>(vmItems());
        vmSuccesses++;
        if ((vmSuccesses & 1) == 1) {
            Collections.reverse(items);
        }
        if (!expiredInitialToken) {
            validAccessToken = null;
            expiredInitialToken = true;
        }
        sendJson(exchange, 200, "[" + String.join(",", items) + "]");
    }

    private synchronized void serveHosts(HttpExchange exchange, byte[] body)
            throws IOException {
        if (!validCollectionRequest(exchange, body)) {
            sendUnauthenticated(exchange);
            return;
        }
        List<String> items = new ArrayList<>(hostItems());
        hostSuccesses++;
        if ((hostSuccesses & 1) == 1) {
            Collections.reverse(items);
        }
        sendJson(exchange, 200, "[" + String.join(",", items) + "]");
    }

    private synchronized void serveToken(HttpExchange exchange, byte[] body)
            throws IOException {
        String expectedBody = "grant_type=" + formEncode(GRANT_TYPE)
                + "&subject_token=" + formEncode(fixture.subjectToken())
                + "&subject_token_type=" + formEncode(fixture.subjectTokenType());
        boolean valid = noQuery(exchange)
                && oneHeader(exchange, "Accept").equals("application/json")
                && oneHeader(exchange, "Content-Type")
                        .equals("application/x-www-form-urlencoded")
                && oneHeader(exchange, "Authorization")
                        .equals("Bearer " + fixture.subjectToken())
                && exchange.getRequestHeaders().get("vmware-api-session-id") == null
                && new String(body, StandardCharsets.UTF_8).equals(expectedBody);
        if (!valid) {
            sendJson(exchange, 400,
                    "{\"error\":\"invalid_request\","
                            + "\"error_description\":\"request does not match contract\"}");
            return;
        }
        validAccessToken = fixture.replacementAccessToken();
        sendJson(exchange, 200,
                "{\"access_token\":" + quote(fixture.replacementAccessToken())
                        + ",\"token_type\":\"Bearer\",\"expires_in\":300,"
                        + "\"refresh_token\":\"unused-by-client\"}");
    }

    private boolean validCollectionRequest(HttpExchange exchange, byte[] body) {
        return noQuery(exchange)
                && body.length == 0
                && oneHeader(exchange, "Accept").equals("application/json")
                && oneHeader(exchange, "vmware-api-session-id")
                        .equals(validAccessToken)
                && exchange.getRequestHeaders().get("Authorization") == null
                && exchange.getRequestHeaders().get("Content-Type") == null
                && validAccessToken != null;
    }

    private static boolean noQuery(HttpExchange exchange) {
        return exchange.getRequestURI().getRawQuery() == null;
    }

    private static String oneHeader(HttpExchange exchange, String name) {
        List<String> values = exchange.getRequestHeaders().get(name);
        return values != null && values.size() == 1 ? values.get(0) : "";
    }

    private List<String> vmItems() {
        String suffix = fixture.suffix();
        return List.of(
                "{\"vm\":" + quote("vm-a-" + suffix)
                        + ",\"name\":\"alpha\",\"power_state\":\"POWERED_ON\"}",
                "{\"vm\":" + quote("vm-m-" + suffix)
                        + ",\"name\":\"middle \\\"quoted\\\"\","
                        + "\"power_state\":\"SUSPENDED\",\"cpu_count\":4,"
                        + "\"memory_size_mib\":8192}",
                "{\"vm\":" + quote("vm-z-" + suffix)
                        + ",\"name\":\"zeta\",\"power_state\":\"POWERED_OFF\","
                        + "\"cpu_count\":8,\"memory_size_mib\":16384}");
    }

    private List<String> hostItems() {
        String suffix = fixture.suffix();
        return List.of(
                "{\"host\":" + quote("host-a-" + suffix)
                        + ",\"name\":\"esx-a.example.test\","
                        + "\"connection_state\":\"CONNECTED\"}",
                "{\"host\":" + quote("host-m-" + suffix)
                        + ",\"name\":\"esx-m.example.test\","
                        + "\"connection_state\":\"NOT_RESPONDING\","
                        + "\"power_state\":null,\"host_uuid\":null}",
                "{\"host\":" + quote("host-z-" + suffix)
                        + ",\"name\":\"esx-z.example.test\","
                        + "\"connection_state\":\"DISCONNECTED\","
                        + "\"power_state\":\"POWERED_OFF\","
                        + "\"host_uuid\":" + quote("uuid-" + suffix) + "}");
    }

    private static Map<String, String> loadRoutes(Path contract) throws IOException {
        String json = Files.readString(contract, StandardCharsets.UTF_8);
        Matcher matcher = OPERATION.matcher(json);
        Map<String, String> routes = new LinkedHashMap<>();
        Set<String> operations = new LinkedHashSet<>();
        while (matcher.find()) {
            String operationId = matcher.group(1);
            String method = matcher.group(2);
            String wirePath = matcher.group(3);
            if (!operations.add(operationId)
                    || routes.put(method + " " + wirePath, operationId) != null) {
                throw new IOException("duplicate operation in focused contract");
            }
        }
        if (!operations.equals(EXPECTED_OPERATIONS)) {
            throw new IOException("focused contract operation set is not pinned");
        }
        return Map.copyOf(routes);
    }

    private static Map<String, List<String>> copyHeaders(Headers headers) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        headers.forEach((key, values) -> copy.put(
                key.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return copy;
    }

    private static String formEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
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

    private static void sendUnauthenticated(HttpExchange exchange)
            throws IOException {
        sendJson(exchange, 401,
                "{\"error_type\":\"UNAUTHENTICATED\",\"messages\":[{"
                        + "\"id\":\"com.vmware.vapi.endpoint.method.authentication.required\","
                        + "\"default_message\":\"Authentication required.\","
                        + "\"args\":[]}]}");
    }

    private static void sendJson(HttpExchange exchange, int status, String json)
            throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    /**
     * A handler-backed boundary used only when the authoring sandbox forbids
     * binding even to 127.0.0.1. Normal verifier environments use the real
     * loopback listener above.
     */
    private static final class HandlerHttpClient extends HttpClient {
        private final MockVcenterServer owner;
        private final SSLContext sslContext;

        private HandlerHttpClient(MockVcenterServer owner) {
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
            subscriber.onNext(List.of(ByteBuffer.wrap(exchange.responseBytes())));
            subscriber.onComplete();
            try {
                T responseBody = subscriber.getBody().toCompletableFuture().get();
                return new MemoryResponse<>(
                        request,
                        exchange.statusCode(),
                        responseHeaders,
                        responseBody);
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

        private static byte[] publish(
                Optional<HttpRequest.BodyPublisher> publisher)
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

        private MemoryExchange(
                String method, URI uri, Headers requestHeaders, byte[] requestBody) {
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
            this.statusCode = responseCode;
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
