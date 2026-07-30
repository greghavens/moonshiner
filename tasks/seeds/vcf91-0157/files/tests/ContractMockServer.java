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

/**
 * Loopback-only fixture. Its complete route table is projected from the
 * protected focused contract.
 */
final class ContractMockServer implements AutoCloseable {
    static final String VCENTER_OPERATION =
            "Vcenter.Namespaces.User.Instances_list";
    static final String KUBERNETES_OPERATION =
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:list";

    record Fixture(
            String oldVcenterSession,
            String newVcenterSession,
            String oldAccessToken,
            String newAccessToken,
            String namespaceA,
            String namespaceZ,
            String suffix) {
        List<String> namespaces() {
            return List.of(namespaceA, namespaceZ);
        }
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
            headers.forEach((key, values) ->
                    copied.put(key, List.copyOf(values)));
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
    }

    private record ContractRoutes(
            String vcenterMethod,
            String vcenterPath,
            String kubernetesMethod,
            String kubernetesTemplate) {
    }

    private static final Pattern VCENTER_CONTRACT = Pattern.compile(
            "\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                    + ".*?\\\"method\\\"\\s*:\\s*\\\"([A-Z]+)\\\""
                    + ".*?\\\"wirePath\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"",
            Pattern.DOTALL);
    private static final Pattern KUBERNETES_CONTRACT = Pattern.compile(
            "\\\"operationKey\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                    + ".*?\\\"method\\\"\\s*:\\s*\\\"([A-Z]+)\\\""
                    + ".*?\\\"pathTemplate\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"",
            Pattern.DOTALL);

    private final Fixture fixture;
    private final ContractRoutes routes;
    private final HttpServer server;
    private final ExecutorService executor;
    private final URI origin;
    private final HttpClient client;
    private final List<RequestLog> requests = new ArrayList<>();
    private final Map<String, Integer> clusterSuccesses = new LinkedHashMap<>();

    private String validVcenterSession;
    private int namespaceListSuccesses;

    ContractMockServer(Path contract, Fixture fixture) throws IOException {
        this.fixture = fixture;
        this.routes = loadRoutes(contract);
        this.validVcenterSession = fixture.oldVcenterSession();
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
                        runnable, "vcf91-0157-contract-mock");
                thread.setDaemon(true);
                return thread;
            });
            started.setExecutor(pool);
            started.createContext("/", this::serve);
            started.start();
            selectedOrigin = URI.create(
                    "http://127.0.0.1:" + started.getAddress().getPort());
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

    URI origin() {
        return origin;
    }

    HttpClient client() {
        return client;
    }

    String vcenterPath() {
        return routes.vcenterPath();
    }

    String kubernetesPath(String namespace) {
        return routes.kubernetesTemplate().replace(
                "{namespace}", rfc3986Segment(namespace));
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
        String rawPath = exchange.getRequestURI().getRawPath();
        String operation = identifyOperation(exchange.getRequestMethod(), rawPath);
        int status;
        byte[] response;
        if (VCENTER_OPERATION.equals(operation)) {
            Response selected = serveNamespaces(exchange, body);
            status = selected.status();
            response = selected.body();
        } else if (KUBERNETES_OPERATION.equals(operation)) {
            Response selected = serveClusters(exchange, rawPath, body);
            status = selected.status();
            response = selected.body();
        } else {
            status = 404;
            response = jsonBytes("{\"error_type\":\"NOT_FOUND\"}");
        }

        synchronized (this) {
            requests.add(new RequestLog(
                    operation,
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().toASCIIString(),
                    copyHeaders(exchange.getRequestHeaders()),
                    body,
                    status));
        }
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, response.length);
        exchange.getResponseBody().write(response);
        exchange.close();
    }

    private synchronized Response serveNamespaces(
            HttpExchange exchange, byte[] body) {
        boolean valid = noQuery(exchange)
                && body.length == 0
                && oneHeader(exchange, "Accept").equals("application/json")
                && oneHeader(exchange, "vmware-api-session-id")
                        .equals(validVcenterSession)
                && exchange.getRequestHeaders().get("Authorization") == null
                && exchange.getRequestHeaders().get("Content-Type") == null;
        if (!valid) {
            return unauthenticated();
        }

        namespaceListSuccesses++;
        List<String> namespaces = new ArrayList<>(fixture.namespaces());
        if ((namespaceListSuccesses & 1) == 1) {
            Collections.reverse(namespaces);
        }
        List<String> items = new ArrayList<>();
        for (String namespace : namespaces) {
            items.add("{\"namespace\":" + quote(namespace)
                    + ",\"master_host\":" + quote(origin.toString()) + "}");
        }
        return new Response(200, jsonBytes("[" + String.join(",", items) + "]"));
    }

    private synchronized Response serveClusters(
            HttpExchange exchange, String rawPath, byte[] body) {
        String namespace = namespaceFromRawPath(rawPath);
        String authorization = oneHeader(exchange, "Authorization");
        boolean semanticShape = namespace != null
                && fixture.namespaces().contains(namespace)
                && noQuery(exchange)
                && body.length == 0
                && oneHeader(exchange, "Accept").equals("application/json")
                && exchange.getRequestHeaders().get("vmware-api-session-id") == null
                && exchange.getRequestHeaders().get("Content-Type") == null;
        if (!semanticShape) {
            return new Response(400,
                    jsonBytes("{\"error\":\"request_contract_mismatch\"}"));
        }

        if (authorization.equals("Bearer " + fixture.oldAccessToken())) {
            if (namespace.equals(fixture.namespaceZ())) {
                return unauthenticated();
            }
        } else if (authorization.equals("Bearer " + fixture.newAccessToken())) {
            validVcenterSession = fixture.newVcenterSession();
        } else {
            return unauthenticated();
        }

        int successes = clusterSuccesses.merge(namespace, 1, Integer::sum);
        List<String> items = new ArrayList<>(clusterItems(namespace));
        if ((successes & 1) == 1) {
            Collections.reverse(items);
        }
        String response = "{\"apiVersion\":\"cluster.x-k8s.io/v1beta2\","
                + "\"kind\":\"ClusterList\","
                + "\"metadata\":{\"resourceVersion\":\""
                + (100 + successes) + "\"},"
                + "\"items\":[" + String.join(",", items) + "]}";
        return new Response(200, jsonBytes(response));
    }

    private List<String> clusterItems(String namespace) {
        String prefix = namespace.equals(fixture.namespaceA()) ? "alpha" : "zeta";
        String nameA = prefix + "-a-" + fixture.suffix();
        String nameZ = prefix + "-z-" + fixture.suffix();
        return List.of(
                clusterJson(namespace, nameA, "uid-" + nameA,
                        "v1.32.4+vmware.1", "Provisioned"),
                clusterJson(namespace, nameZ, "uid-" + nameZ,
                        "v1.33.1+vmware.2", "Running"));
    }

    private static String clusterJson(
            String namespace,
            String name,
            String uid,
            String version,
            String phase) {
        return "{\"apiVersion\":\"cluster.x-k8s.io/v1beta2\","
                + "\"kind\":\"Cluster\","
                + "\"metadata\":{\"name\":" + quote(name)
                + ",\"namespace\":" + quote(namespace)
                + ",\"uid\":" + quote(uid) + "},"
                + "\"spec\":{\"topology\":{\"version\":"
                + quote(version) + "}},"
                + "\"status\":{\"phase\":" + quote(phase) + "}}";
    }

    private String identifyOperation(String method, String rawPath) {
        if (method.equals(routes.vcenterMethod())
                && rawPath.equals(routes.vcenterPath())) {
            return VCENTER_OPERATION;
        }
        if (method.equals(routes.kubernetesMethod())
                && namespaceFromRawPath(rawPath) != null) {
            return KUBERNETES_OPERATION;
        }
        return null;
    }

    private String namespaceFromRawPath(String rawPath) {
        String template = routes.kubernetesTemplate();
        int marker = template.indexOf("{namespace}");
        String prefix = template.substring(0, marker);
        String suffix = template.substring(marker + "{namespace}".length());
        if (!rawPath.startsWith(prefix) || !rawPath.endsWith(suffix)) {
            return null;
        }
        String segment = rawPath.substring(
                prefix.length(), rawPath.length() - suffix.length());
        if (segment.isEmpty() || segment.contains("/")) {
            return null;
        }
        return percentDecode(segment);
    }

    private static ContractRoutes loadRoutes(Path contract) throws IOException {
        String json = Files.readString(contract, StandardCharsets.UTF_8);
        Matcher vcenter = VCENTER_CONTRACT.matcher(json);
        Matcher kubernetes = KUBERNETES_CONTRACT.matcher(json);
        if (!vcenter.find()) {
            throw new IOException("focused contract must name one VMware operation");
        }
        String vcenterOperation = vcenter.group(1);
        String vcenterMethod = vcenter.group(2);
        String vcenterPath = vcenter.group(3);
        if (vcenter.find()) {
            throw new IOException("focused contract must name one VMware operation");
        }
        if (!kubernetes.find()) {
            throw new IOException("focused contract must name one Kubernetes operation");
        }
        String kubernetesOperation = kubernetes.group(1);
        String kubernetesMethod = kubernetes.group(2);
        String template = kubernetes.group(3);
        if (kubernetes.find()) {
            throw new IOException(
                    "focused contract must name one Kubernetes operation");
        }
        if (!VCENTER_OPERATION.equals(vcenterOperation)
                || !KUBERNETES_OPERATION.equals(kubernetesOperation)) {
            throw new IOException("focused contract operation names are not pinned");
        }
        if (template.indexOf("{namespace}") < 0
                || template.indexOf("{namespace}")
                        != template.lastIndexOf("{namespace}")) {
            throw new IOException("Kubernetes template must have one namespace");
        }
        return new ContractRoutes(
                vcenterMethod,
                vcenterPath,
                kubernetesMethod,
                template);
    }

    private static Map<String, List<String>> copyHeaders(Headers headers) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        headers.forEach((key, values) ->
                copy.put(key.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return copy;
    }

    private static boolean noQuery(HttpExchange exchange) {
        return exchange.getRequestURI().getRawQuery() == null;
    }

    private static String oneHeader(HttpExchange exchange, String name) {
        List<String> values = exchange.getRequestHeaders().get(name);
        return values != null && values.size() == 1 ? values.get(0) : "";
    }

    private static Response unauthenticated() {
        return new Response(401, jsonBytes(
                "{\"error_type\":\"UNAUTHENTICATED\","
                        + "\"messages\":[{\"id\":\"authentication.required\","
                        + "\"default_message\":\"Authentication required.\"}]}"));
    }

    private static byte[] jsonBytes(String value) {
        return value.getBytes(StandardCharsets.UTF_8);
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

    private static String rfc3986Segment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder();
        for (byte item : bytes) {
            int b = item & 0xff;
            if ((b >= 'a' && b <= 'z')
                    || (b >= 'A' && b <= 'Z')
                    || (b >= '0' && b <= '9')
                    || b == '-' || b == '.' || b == '_' || b == '~') {
                out.append((char) b);
            } else {
                out.append('%');
                out.append("0123456789ABCDEF".charAt(b >>> 4));
                out.append("0123456789ABCDEF".charAt(b & 0xf));
            }
        }
        return out.toString();
    }

    private static String percentDecode(String value) {
        byte[] output = new byte[value.length()];
        int used = 0;
        for (int i = 0; i < value.length();) {
            char c = value.charAt(i);
            if (c == '%' && i + 2 < value.length()) {
                int high = Character.digit(value.charAt(i + 1), 16);
                int low = Character.digit(value.charAt(i + 2), 16);
                if (high < 0 || low < 0) {
                    return null;
                }
                output[used++] = (byte) ((high << 4) | low);
                i += 3;
            } else if (c <= 0x7f) {
                output[used++] = (byte) c;
                i++;
            } else {
                return null;
            }
        }
        return new String(output, 0, used, StandardCharsets.UTF_8);
    }

    private record Response(int status, byte[] body) {
    }

    /**
     * Handler-backed boundary used only in socket-denying authoring
     * sandboxes. Normal verifier environments use the loopback listener.
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
            byte[] requestBody = publish(request.bodyPublisher());
            Headers requestHeaders = new Headers();
            request.headers().map().forEach((name, values) ->
                    requestHeaders.put(name, new ArrayList<>(values)));
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
        private final Map<String, Object> attributes = new LinkedHashMap<>();
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
