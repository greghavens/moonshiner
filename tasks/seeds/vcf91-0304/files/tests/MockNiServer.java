import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
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
import java.util.concurrent.Executor;
import java.util.concurrent.Flow;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/**
 * Offline contract fixture for the three VCF Operations for Networks 9.1 operations named in
 * {@code docs/contract.json}: {@code getApplicationById}, {@code listApplicationTiers} and
 * {@code addTier}. It is an in-process {@link HttpClient}, so the production client still builds
 * genuine JDK {@link HttpRequest} objects while verification needs no network namespace.
 *
 * <p>No other route is served; anything else answers 404 so an off-contract call is visible in
 * the request log rather than silently tolerated. All state is private to one fixture instance.
 */
public final class MockNiServer implements AutoCloseable {

    private static final String BASE_PATH = "/api/ni";
    private static final String APPS_PREFIX = BASE_PATH + "/groups/applications/";
    private static final String BASE_URL = "http://127.0.0.1";

    private final List<Record> requestLog = Collections.synchronizedList(new ArrayList<>());
    private final HttpClient httpClient = new FixtureHttpClient();

    /** applicationId -> application name */
    private final Map<String, String> applications = new LinkedHashMap<>();
    /** applicationId -> tier entity id -> tier name, in creation order */
    private final Map<String, Map<String, String>> tiers = new LinkedHashMap<>();
    /** applicationId -> tier entity ids withheld from the next list response for that application */
    private final Map<String, Set<String>> withheldFromNextList = new LinkedHashMap<>();

    private int nextTierSuffix = 100000001;
    private Integer nextAddFailureStatus;
    private String nextAddFailureMessage;
    private Integer nextListFailureStatus;
    private String nextListFailureMessage;

    public String baseUrl() {
        return BASE_URL;
    }

    public HttpClient httpClient() {
        return httpClient;
    }

    /** Registers an application so that {@code getApplicationById} resolves it. */
    public MockNiServer addApplication(String applicationId, String applicationName) {
        applications.put(applicationId, applicationName);
        tiers.computeIfAbsent(applicationId, key -> new LinkedHashMap<>());
        return this;
    }

    /** Creates a tier that is immediately visible to {@code listApplicationTiers}. */
    public String seedTier(String applicationId, String tierName) {
        return createTier(applicationId, tierName);
    }

    /**
     * Creates a tier that already exists but is withheld from the next tier-list response. This
     * deterministically reproduces losing a race to a concurrent creator.
     */
    public String seedTierHiddenFromNextList(String applicationId, String tierName) {
        String id = createTier(applicationId, tierName);
        withheldFromNextList.computeIfAbsent(applicationId, key -> new LinkedHashSet<>()).add(id);
        return id;
    }

    /** Makes the next addTier response fail without changing server state. */
    public MockNiServer failNextAddTier(int status, String message) {
        nextAddFailureStatus = status;
        nextAddFailureMessage = message;
        return this;
    }

    /** Makes the next listApplicationTiers response fail without changing server state. */
    public MockNiServer failNextTierList(int status, String message) {
        nextListFailureStatus = status;
        nextListFailureMessage = message;
        return this;
    }

    /** Every request the fixture received, in arrival order. */
    public List<Record> requestLog() {
        return List.copyOf(requestLog);
    }

    @Override
    public void close() {
        // There is no process, thread, port or other external resource to release.
    }

    private String createTier(String applicationId, String tierName) {
        String id = "18230:562:" + (nextTierSuffix++);
        tiers.computeIfAbsent(applicationId, key -> new LinkedHashMap<>()).put(id, tierName);
        return id;
    }

    // --------------------------------------------------------------- routing

    private Response route(HttpRequest request) throws IOException {
        String body = readBody(request);
        Map<String, String> headers = new LinkedHashMap<>();
        request.headers().map().forEach((name, values) -> {
            if (!values.isEmpty()) {
                headers.put(name.toLowerCase(Locale.ROOT), values.get(0));
            }
        });
        URI uri = request.uri();
        String rawQuery = uri.getRawQuery();
        String target = uri.getRawPath() + (rawQuery == null ? "" : "?" + rawQuery);
        Record record = new Record(
                requestLog.size() + 1,
                request.method(),
                uri.getRawPath(),
                rawQuery == null ? "" : rawQuery,
                target,
                headers,
                body);
        requestLog.add(record);

        String authorization = record.headers().get("authorization");
        if (authorization == null || !authorization.startsWith("NetworkInsight ")
                || authorization.length() <= "NetworkInsight ".length()) {
            return error(401, "Missing or malformed NetworkInsight API key");
        }

        String path = record.path();
        if (!path.startsWith(APPS_PREFIX)) {
            return error(404, "No operation is served at " + path);
        }
        String remainder = path.substring(APPS_PREFIX.length());
        String applicationId;
        boolean tiersCollection;
        if (remainder.endsWith("/tiers")) {
            applicationId = remainder.substring(0, remainder.length() - "/tiers".length());
            tiersCollection = true;
        } else {
            applicationId = remainder;
            tiersCollection = false;
        }
        if (applicationId.isEmpty() || applicationId.contains("/")) {
            return error(404, "No operation is served at " + path);
        }

        String method = record.method();
        if (!tiersCollection && "GET".equals(method)) {
            return getApplicationById(applicationId);
        }
        if (tiersCollection && "GET".equals(method)) {
            return listApplicationTiers(applicationId);
        }
        if (tiersCollection && "POST".equals(method)) {
            return addTier(applicationId, record.body());
        }
        return error(404, "No operation is served by " + method + " " + path);
    }

    // ------------------------------------------------------------ operations

    private Response getApplicationById(String applicationId) {
        String name = applications.get(applicationId);
        if (name == null) {
            return error(404, "Application " + applicationId + " was not found");
        }
        Map<String, Object> application = new LinkedHashMap<>();
        application.put("entity_id", applicationId);
        application.put("name", name);
        application.put("entity_type", "Application");
        application.put("create_time", 1509410056733L);
        application.put("created_by", "admin@local");
        application.put("last_modified_time", 0L);
        application.put("last_modified_by", "");
        application.put("last_modified_by_service", "");
        return response(200, application);
    }

    private Response listApplicationTiers(String applicationId) {
        if (!applications.containsKey(applicationId)) {
            return error(404, "Application " + applicationId + " was not found");
        }
        if (nextListFailureStatus != null) {
            int status = nextListFailureStatus;
            String message = nextListFailureMessage;
            nextListFailureStatus = null;
            nextListFailureMessage = null;
            return error(status, message);
        }
        Set<String> withheld = withheldFromNextList.remove(applicationId);
        List<Object> results = new ArrayList<>();
        for (Map.Entry<String, String> entry : tiers.get(applicationId).entrySet()) {
            if (withheld != null && withheld.contains(entry.getKey())) {
                continue;
            }
            results.add(tierBody(applicationId, entry.getKey(), entry.getValue()));
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("results", results);
        return response(200, body);
    }

    private Response addTier(String applicationId, String body) {
        if (!applications.containsKey(applicationId)) {
            return error(404, "Application " + applicationId + " was not found");
        }
        if (nextAddFailureStatus != null) {
            int status = nextAddFailureStatus;
            String message = nextAddFailureMessage;
            nextAddFailureStatus = null;
            nextAddFailureMessage = null;
            return error(status, message);
        }
        Map<String, Object> request;
        try {
            request = Json.asObject(Json.parse(body));
        } catch (RuntimeException e) {
            return error(400, "Request body is not a JSON object");
        }
        if (request == null) {
            return error(400, "Request body is not a JSON object");
        }
        String name = Json.asString(request.get("name"));
        if (name == null || name.isEmpty()) {
            return error(400, "Tier name is required");
        }
        for (String existing : tiers.get(applicationId).values()) {
            if (existing.equals(name)) {
                return error(400,
                        "A tier named '" + name + "' already exists in application "
                                + applicationId);
            }
        }
        String id = createTier(applicationId, name);
        return response(201, tierBody(applicationId, id, name));
    }

    private Map<String, Object> tierBody(String applicationId, String tierId, String tierName) {
        Map<String, Object> application = new LinkedHashMap<>();
        application.put("entity_id", applicationId);
        application.put("entity_type", "Application");

        Map<String, Object> tier = new LinkedHashMap<>();
        tier.put("entity_id", tierId);
        tier.put("name", tierName);
        tier.put("entity_type", "Tier");
        tier.put("application", application);
        return tier;
    }

    // ------------------------------------------------------------- responses

    private static Response error(int status, String message) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("code", (long) status);
        body.put("message", message);
        return response(status, body);
    }

    private static Response response(int status, Object body) {
        return new Response(status, Json.write(body));
    }

    private static String readBody(HttpRequest request) throws IOException {
        Optional<HttpRequest.BodyPublisher> publisher = request.bodyPublisher();
        if (publisher.isEmpty()) {
            return "";
        }
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        CompletableFuture<Void> complete = new CompletableFuture<>();
        publisher.get().subscribe(new Flow.Subscriber<ByteBuffer>() {
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
                complete.complete(null);
            }
        });
        try {
            complete.join();
        } catch (CompletionException e) {
            throw new IOException("request body publisher failed", e.getCause());
        }
        return bytes.toString(StandardCharsets.UTF_8);
    }

    private static <T> T decodeBody(
            HttpResponse.BodyHandler<T> handler, int status, String body) {
        HttpHeaders headers = HttpHeaders.of(
                Map.of("Content-Type", List.of("application/json")), (name, value) -> true);
        HttpResponse.ResponseInfo info = new HttpResponse.ResponseInfo() {
            @Override
            public int statusCode() {
                return status;
            }

            @Override
            public HttpHeaders headers() {
                return headers;
            }

            @Override
            public HttpClient.Version version() {
                return HttpClient.Version.HTTP_1_1;
            }
        };
        HttpResponse.BodySubscriber<T> subscriber = handler.apply(info);
        subscriber.onSubscribe(new Flow.Subscription() {
            private boolean sent;

            @Override
            public void request(long count) {
                if (!sent && count > 0) {
                    sent = true;
                    subscriber.onNext(List.of(
                            ByteBuffer.wrap(body.getBytes(StandardCharsets.UTF_8))));
                    subscriber.onComplete();
                }
            }

            @Override
            public void cancel() {
                sent = true;
            }
        });
        return subscriber.getBody().toCompletableFuture().join();
    }

    private final class FixtureHttpClient extends HttpClient {
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
            try {
                return SSLContext.getDefault();
            } catch (NoSuchAlgorithmException e) {
                throw new IllegalStateException(e);
            }
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
        public Optional<Executor> executor() {
            return Optional.empty();
        }

        @Override
        public <T> HttpResponse<T> send(
                HttpRequest request, HttpResponse.BodyHandler<T> responseBodyHandler)
                throws IOException {
            Response routed = route(request);
            T body = decodeBody(responseBodyHandler, routed.status(), routed.body());
            return new FixtureHttpResponse<>(request, routed.status(), body);
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> responseBodyHandler) {
            return CompletableFuture.supplyAsync(() -> {
                try {
                    return send(request, responseBodyHandler);
                } catch (IOException e) {
                    throw new CompletionException(e);
                }
            });
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler,
                HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
            return sendAsync(request, responseBodyHandler);
        }
    }

    private static final class FixtureHttpResponse<T> implements HttpResponse<T> {
        private final HttpRequest request;
        private final int status;
        private final T body;

        private FixtureHttpResponse(HttpRequest request, int status, T body) {
            this.request = request;
            this.status = status;
            this.body = body;
        }

        @Override
        public int statusCode() {
            return status;
        }

        @Override
        public HttpRequest request() {
            return request;
        }

        @Override
        public Optional<HttpResponse<T>> previousResponse() {
            return Optional.empty();
        }

        @Override
        public HttpHeaders headers() {
            return HttpHeaders.of(
                    Map.of("Content-Type", List.of("application/json")),
                    (name, value) -> true);
        }

        @Override
        public T body() {
            return body;
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

    private record Response(int status, String body) {
    }

    /** One received request, captured before any routing decision. */
    public record Record(
            int sequence,
            String method,
            String path,
            String query,
            String target,
            Map<String, String> headers,
            String body) {
    }
}
