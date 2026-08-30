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
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.Flow;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/**
 * Loopback-only fixture whose complete route allow-list is loaded from the
 * protected focused contract.
 */
final class ContractMockServer implements AutoCloseable {
    static final String NAMESPACE_OPERATION =
            "Vcenter.Namespaces.Instances_update";
    static final String CLUSTER_OPERATION =
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch";

    enum FailurePoint {
        NONE,
        NAMESPACE,
        LABEL,
        VERSION,
        REDIRECT
    }

    record Fixture(
            String namespace,
            String clusterName,
            String namespaceDescription,
            String labelKey,
            String labelValue,
            String targetVersion,
            String vcenterSession,
            String kubernetesToken,
            String sensitiveMarker,
            FailurePoint failurePoint) {
    }

    record RequestLog(
            String operation,
            String method,
            String rawTarget,
            Map<String, List<String>> headers,
            byte[] body,
            int responseStatus) {
        RequestLog {
            Map<String, List<String>> copied = new LinkedHashMap<>();
            headers.forEach((name, values) ->
                    copied.put(name, List.copyOf(values)));
            headers = Collections.unmodifiableMap(copied);
            body = body.clone();
        }

        @Override
        public byte[] body() {
            return body.clone();
        }

        List<String> headerValues(String wanted) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(wanted)) {
                    return entry.getValue();
                }
            }
            return List.of();
        }
    }

    private record Routes(
            String vcenterOperation,
            String vcenterMethod,
            String vcenterTemplate,
            String kubernetesOperation,
            String kubernetesMethod,
            String kubernetesTemplate) {
    }

    private record Response(
            int status,
            byte[] body,
            Map<String, String> headers) {
        Response(int status, byte[] body) {
            this(status, body, Map.of());
        }
    }

    private static final Pattern VCENTER_CONTRACT = Pattern.compile(
            "\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                    + ".*?\\\"method\\\"\\s*:\\s*\\\"([A-Z]+)\\\""
                    + ".*?\\\"pathTemplate\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"",
            Pattern.DOTALL);
    private static final Pattern KUBERNETES_CONTRACT = Pattern.compile(
            "\\\"operationKey\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                    + ".*?\\\"method\\\"\\s*:\\s*\\\"([A-Z]+)\\\""
                    + ".*?\\\"pathTemplate\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"",
            Pattern.DOTALL);

    private final Fixture fixture;
    private final Routes routes;
    private final HttpServer server;
    private final ExecutorService executor;
    private final URI origin;
    private final HttpClient client;
    private final List<RequestLog> requests = new ArrayList<>();

    private int kubernetesAttempts;
    private boolean namespaceCommitted;
    private boolean labelCommitted;
    private boolean versionCommitted;
    private volatile boolean closed;

    ContractMockServer(Path contractPath, Fixture fixture) throws IOException {
        this.fixture = fixture;
        this.routes = loadRoutes(contractPath);
        HttpServer started = null;
        ExecutorService pool = null;
        URI selectedOrigin;
        HttpClient selectedClient;
        try {
            started = HttpServer.create(
                    new InetSocketAddress(
                            InetAddress.getByName("127.0.0.1"), 0),
                    0);
            pool = Executors.newSingleThreadExecutor(runnable -> {
                Thread thread = new Thread(
                        runnable, "vcf91-0160-contract-mock");
                thread.setDaemon(true);
                return thread;
            });
            started.setExecutor(pool);
            started.createContext("/", this::serve);
            started.start();
            selectedOrigin = URI.create(
                    "http://127.0.0.1:"
                            + started.getAddress().getPort());
            selectedClient = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(2))
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .version(HttpClient.Version.HTTP_1_1)
                    .build();
        } catch (IOException | RuntimeException listenerUnavailable) {
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

    URI vcenterApiBase() {
        return URI.create(origin + "/api");
    }

    URI kubernetesOrigin() {
        return origin;
    }

    HttpClient client() {
        return client;
    }

    String vcenterTarget() {
        return routes.vcenterTemplate().replace(
                "{namespace}", rfc3986Segment(fixture.namespace()));
    }

    String kubernetesTarget() {
        return routes.kubernetesTemplate()
                .replace("{namespace}",
                        rfc3986Segment(fixture.namespace()))
                .replace("{clusterName}",
                        rfc3986Segment(fixture.clusterName()));
    }

    byte[] expectedNamespaceBody() {
        return bytes("{\"description\":"
                + quote(fixture.namespaceDescription()) + "}");
    }

    byte[] expectedLabelBody() {
        return bytes("{\"metadata\":{\"labels\":{"
                + quote(fixture.labelKey()) + ":"
                + quote(fixture.labelValue()) + "}}}");
    }

    byte[] expectedVersionBody() {
        return bytes("{\"spec\":{\"topology\":{\"version\":"
                + quote(fixture.targetVersion()) + "}}}");
    }

    synchronized List<RequestLog> requests() {
        return List.copyOf(requests);
    }

    synchronized boolean namespaceCommitted() {
        return namespaceCommitted;
    }

    synchronized boolean labelCommitted() {
        return labelCommitted;
    }

    synchronized boolean versionCommitted() {
        return versionCommitted;
    }

    @Override
    public void close() {
        closed = true;
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
    }

    private void serve(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        String rawPath = exchange.getRequestURI().getRawPath();
        String operation = identifyOperation(
                exchange.getRequestMethod(), rawPath);
        Response response;
        if (NAMESPACE_OPERATION.equals(operation)) {
            response = serveNamespace(exchange, requestBody);
        } else if (CLUSTER_OPERATION.equals(operation)) {
            response = serveCluster(exchange, requestBody);
        } else {
            response = new Response(
                    404, bytes("{\"error\":\"unnamed_operation\"}"));
        }

        synchronized (this) {
            requests.add(new RequestLog(
                    operation,
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().toASCIIString(),
                    copyHeaders(exchange.getRequestHeaders()),
                    requestBody,
                    response.status()));
        }
        response.headers().forEach(
                (name, value) ->
                        exchange.getResponseHeaders().set(name, value));
        if (response.body().length > 0
                && !response.headers().containsKey("Content-Type")) {
            exchange.getResponseHeaders().set(
                    "Content-Type", "application/json");
        }
        if (response.status() == 204) {
            exchange.sendResponseHeaders(response.status(), -1);
        } else {
            exchange.sendResponseHeaders(
                    response.status(), response.body().length);
            exchange.getResponseBody().write(response.body());
        }
        exchange.close();
    }

    private synchronized Response serveNamespace(
            HttpExchange exchange, byte[] body) {
        if (!validCommonRequest(exchange, body, "application/json")
                || !oneHeader(exchange.getRequestHeaders(),
                        "vmware-api-session-id")
                        .equals(fixture.vcenterSession())
                || exchange.getRequestHeaders().get("Authorization") != null
                || !java.util.Arrays.equals(body, expectedNamespaceBody())) {
            return new Response(
                    400, bytes("{\"error\":\"wire_contract_mismatch\"}"));
        }
        if (fixture.failurePoint() == FailurePoint.REDIRECT) {
            return new Response(
                    307,
                    new byte[0],
                    Map.of("Location", origin + "/unnamed-redirect-target"));
        }
        if (fixture.failurePoint() == FailurePoint.NAMESPACE) {
            return new Response(
                    409, bytes("{\"error\":\"namespace_conflict\"}"));
        }
        namespaceCommitted = true;
        return new Response(204, new byte[0]);
    }

    private synchronized Response serveCluster(
            HttpExchange exchange, byte[] body) {
        if (!validCommonRequest(
                    exchange, body, "application/merge-patch+json")
                || !oneHeader(exchange.getRequestHeaders(), "Authorization")
                        .equals("Bearer " + fixture.kubernetesToken())
                || exchange.getRequestHeaders()
                        .get("vmware-api-session-id") != null) {
            return new Response(
                    400, bytes("{\"error\":\"wire_contract_mismatch\"}"));
        }
        kubernetesAttempts++;
        if (kubernetesAttempts == 1) {
            if (!java.util.Arrays.equals(body, expectedLabelBody())) {
                return new Response(
                        400, bytes("{\"error\":\"wrong_label_patch\"}"));
            }
            if (fixture.failurePoint() == FailurePoint.LABEL) {
                return new Response(
                        409, bytes("{\"error\":\"label_conflict\"}"));
            }
            labelCommitted = true;
            return clusterResponse(200);
        }
        if (kubernetesAttempts == 2) {
            if (!java.util.Arrays.equals(body, expectedVersionBody())) {
                return new Response(
                        400, bytes("{\"error\":\"wrong_version_patch\"}"));
            }
            if (fixture.failurePoint() == FailurePoint.VERSION) {
                return new Response(
                        422,
                        bytes("{\"error\":\"unsupported_version\","
                                + "\"marker\":"
                                + quote(fixture.sensitiveMarker()) + "}"));
            }
            versionCommitted = true;
            return clusterResponse(200);
        }
        return new Response(
                409, bytes("{\"error\":\"unexpected_extra_patch\"}"));
    }

    private boolean validCommonRequest(
            HttpExchange exchange, byte[] body, String contentType) {
        return exchange.getRequestURI().getRawQuery() == null
                && oneHeader(exchange.getRequestHeaders(), "Accept")
                        .equals("application/json")
                && oneHeader(exchange.getRequestHeaders(), "Content-Type")
                        .equals(contentType)
                && oneHeader(exchange.getRequestHeaders(), "Content-Length")
                        .equals(Integer.toString(body.length))
                && exchange.getRequestHeaders().get("Transfer-Encoding") == null
                && exchange.getRequestHeaders().get("Content-Encoding") == null;
    }

    private Response clusterResponse(int status) {
        String body = "{\"apiVersion\":\"cluster.x-k8s.io/v1beta2\","
                + "\"kind\":\"Cluster\",\"metadata\":{\"namespace\":"
                + quote(fixture.namespace()) + ",\"name\":"
                + quote(fixture.clusterName()) + "}}";
        return new Response(status, bytes(body));
    }

    private String identifyOperation(String method, String rawPath) {
        if (method.equals(routes.vcenterMethod())
                && rawPath.equals(vcenterTarget())) {
            return routes.vcenterOperation();
        }
        if (method.equals(routes.kubernetesMethod())
                && rawPath.equals(kubernetesTarget())) {
            return routes.kubernetesOperation();
        }
        return "UNNAMED";
    }

    private static Routes loadRoutes(Path contractPath) throws IOException {
        String text = Files.readString(
                contractPath, StandardCharsets.UTF_8);
        Matcher vcenter = VCENTER_CONTRACT.matcher(text);
        Matcher kubernetes = KUBERNETES_CONTRACT.matcher(text);
        if (!vcenter.find()) {
            throw new IOException(
                    "contract must name exactly one VMware operation");
        }
        String vcenterOperation = vcenter.group(1);
        String vcenterMethod = vcenter.group(2);
        String vcenterTemplate = vcenter.group(3);
        if (vcenter.find()) {
            throw new IOException(
                    "contract must name exactly one VMware operation");
        }
        if (!kubernetes.find()) {
            throw new IOException(
                    "contract must name exactly one Kubernetes operation");
        }
        String kubernetesOperation = kubernetes.group(1);
        String kubernetesMethod = kubernetes.group(2);
        String kubernetesTemplate = kubernetes.group(3);
        if (kubernetes.find()) {
            throw new IOException(
                    "contract must name exactly one Kubernetes operation");
        }
        Routes selected = new Routes(
                vcenterOperation,
                vcenterMethod,
                vcenterTemplate,
                kubernetesOperation,
                kubernetesMethod,
                kubernetesTemplate);
        if (!selected.vcenterOperation().equals(NAMESPACE_OPERATION)
                || !selected.kubernetesOperation().equals(CLUSTER_OPERATION)) {
            throw new IOException("contract names unexpected operations");
        }
        if (count(text, "\"operationId\"") != 1
                || count(text, "\"operationKey\"") != 1) {
            throw new IOException("contract route allow-list is not focused");
        }
        return selected;
    }

    private static int count(String text, String needle) {
        int result = 0;
        int offset = 0;
        while ((offset = text.indexOf(needle, offset)) >= 0) {
            result++;
            offset += needle.length();
        }
        return result;
    }

    private static Map<String, List<String>> copyHeaders(Headers headers) {
        Map<String, List<String>> result = new LinkedHashMap<>();
        headers.forEach((name, values) ->
                result.put(name, List.copyOf(values)));
        return result;
    }

    private static String oneHeader(Headers headers, String name) {
        List<String> values = headers.get(name);
        return values != null && values.size() == 1
                ? values.get(0)
                : "\u0000";
    }

    static String rfc3986Segment(String value) {
        byte[] encoded = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder result = new StringBuilder(encoded.length);
        final char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte item : encoded) {
            int current = item & 0xff;
            if ((current >= 'a' && current <= 'z')
                    || (current >= 'A' && current <= 'Z')
                    || (current >= '0' && current <= '9')
                    || current == '-' || current == '.'
                    || current == '_' || current == '~') {
                result.append((char) current);
            } else {
                result.append('%')
                        .append(hex[current >>> 4])
                        .append(hex[current & 0x0f]);
            }
        }
        return result.toString();
    }

    static String quote(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2);
        result.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (character < 0x20) {
                        result.append(String.format(
                                "\\u%04x", (int) character));
                    } else {
                        result.append(character);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static byte[] bytes(String value) {
        return value.getBytes(StandardCharsets.UTF_8);
    }

    /**
     * Handler-backed boundary used only when the authoring sandbox denies
     * listener creation. Normal verifier environments use the loopback server.
     */
    private static final class HandlerHttpClient extends HttpClient {
        private final ContractMockServer owner;
        private final SSLContext sslContext;

        private HandlerHttpClient(ContractMockServer owner) {
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
            return Optional.empty();
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
            if (owner.closed) {
                throw new IOException("fixture is closed");
            }
            byte[] requestBody = publish(request.bodyPublisher());
            Headers requestHeaders = new Headers();
            request.headers().map().forEach((name, values) ->
                    requestHeaders.put(name, new ArrayList<>(values)));
            requestHeaders.set(
                    "Content-Length",
                    Integer.toString(requestBody.length));
            MemoryExchange exchange = new MemoryExchange(
                    request.method(),
                    request.uri(),
                    requestHeaders,
                    requestBody);
            owner.serve(exchange);

            HttpHeaders responseHeaders = HttpHeaders.of(
                    exchange.responseHeaders(), (name, value) -> true);
            HttpResponse.ResponseInfo info =
                    new HttpResponse.ResponseInfo() {
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
            subscriber.onNext(
                    List.of(ByteBuffer.wrap(exchange.responseBytes())));
            subscriber.onComplete();
            try {
                T responseBody =
                        subscriber.getBody().toCompletableFuture().get();
                return new MemoryResponse<>(
                        request,
                        exchange.statusCode(),
                        responseHeaders,
                        responseBody);
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
                throw new IOException(
                        "request publisher failed", failed.getCause());
            }
        }
    }

    private static final class MemoryExchange extends HttpExchange {
        private final String method;
        private final URI uri;
        private final Headers requestHeaders;
        private final Headers responseHeaders = new Headers();
        private final ByteArrayInputStream requestBody;
        private final ByteArrayOutputStream responseBody =
                new ByteArrayOutputStream();
        private final Map<String, Object> attributes =
                new LinkedHashMap<>();
        private int statusCode = -1;

        private MemoryExchange(
                String method,
                URI uri,
                Headers requestHeaders,
                byte[] requestBody) {
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

        Map<String, List<String>> responseHeaders() {
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
        public void sendResponseHeaders(
                int responseCode, long responseLength) {
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
