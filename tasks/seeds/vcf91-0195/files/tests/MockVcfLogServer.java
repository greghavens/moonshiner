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
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/** Offline HttpClient contract fixture. It exposes no route outside contract.json. */
public final class MockVcfLogServer extends HttpClient implements AutoCloseable {
    public enum OldExchangeMode {
        HOLD,
        COMPLETE_EXCEPTIONALLY,
        CANCELLABLE,
        THROW_SYNCHRONOUSLY
    }

    public record RecordedRequest(String method, String rawPath, String rawQuery,
                                  Map<String, List<String>> headers, String body) {
        public String header(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null || values.isEmpty() ? null : String.join(",", values);
        }
    }

    private record Route(int status, String responseBody) {}

    private static final String CREATE_PATH = "/api/v2/agent/secrets";
    private static final String EXCHANGE_PATH = "/api/v2/agent/secrets/exchange";
    private static final String REVOKE_PATH =
            "/api/v2/agent/secrets/legacy%20west%2F1/revoke";
    private static final String OLD_EXCHANGE_BODY =
            "{\"secret\":\"old-\\\"s\\\\ecret\"}";
    private static final String NEW_EXCHANGE_BODY =
            "{\"secret\":\"new-\\\"s\\\\ecret\"}";
    private static final String NEW_TTL_EXCHANGE_BODY =
            "{\"secret\":\"new-\\\"s\\\\ecret\",\"ttl\":60000}";

    private final Object logLock = new Object();
    private final Object oldLock = new Object();
    private final List<RecordedRequest> requests = new ArrayList<>();
    private final CountDownLatch oldExchangeSeen = new CountDownLatch(1);
    private final CountDownLatch newLiveExchangeSeen = new CountDownLatch(1);
    private final HttpClient defaults = HttpClient.newBuilder().build();
    private final OldExchangeMode oldExchangeMode;
    private CompletableFuture<HttpResponse<String>> pendingOld;
    private HttpRequest pendingOldRequest;
    private CompletableFuture<HttpResponse<String>> pendingNewLive;
    private HttpRequest pendingNewLiveRequest;
    private boolean releaseOld;
    private boolean releaseNewLive;

    public MockVcfLogServer(Path contractPath, OldExchangeMode oldExchangeMode)
            throws IOException {
        verifyPinnedContract(contractPath);
        this.oldExchangeMode = oldExchangeMode;
    }

    public URI baseUri() {
        return URI.create("http://offline.vcf.test");
    }

    public boolean awaitOldExchange(Duration timeout) throws InterruptedException {
        return oldExchangeSeen.await(timeout.toMillis(), TimeUnit.MILLISECONDS);
    }

    public void releaseOldExchange() {
        synchronized (oldLock) {
            releaseOld = true;
            if (pendingOld != null) {
                pendingOld.complete(response(pendingOldRequest, 200,
                        sessionJson("token-old", "legacy west/1", 1_800_000L)));
            }
        }
    }

    public boolean awaitNewLiveExchange(Duration timeout) throws InterruptedException {
        return newLiveExchangeSeen.await(timeout.toMillis(), TimeUnit.MILLISECONDS);
    }

    public void releaseNewLiveExchange() {
        synchronized (oldLock) {
            releaseNewLive = true;
            if (pendingNewLive != null) {
                pendingNewLive.complete(response(pendingNewLiveRequest, 200,
                        sessionJson("token-new-live", "next \"blue\"/2", 60_000L)));
            }
        }
    }

    public RecordedRequest awaitRequest(Predicate<RecordedRequest> predicate, Duration timeout)
            throws InterruptedException {
        long deadline = System.nanoTime() + timeout.toNanos();
        synchronized (logLock) {
            while (true) {
                for (RecordedRequest request : requests) {
                    if (predicate.test(request)) {
                        return request;
                    }
                }
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) {
                    return null;
                }
                TimeUnit.NANOSECONDS.timedWait(logLock, remaining);
            }
        }
    }

    public List<RecordedRequest> requestLog() {
        synchronized (logLock) {
            return List.copyOf(requests);
        }
    }

    @Override
    public void close() {
        releaseOldExchange();
        releaseNewLiveExchange();
    }

    @Override
    public Optional<CookieHandler> cookieHandler() {
        return defaults.cookieHandler();
    }

    @Override
    public Optional<Duration> connectTimeout() {
        return defaults.connectTimeout();
    }

    @Override
    public Redirect followRedirects() {
        return defaults.followRedirects();
    }

    @Override
    public Optional<ProxySelector> proxy() {
        return defaults.proxy();
    }

    @Override
    public SSLContext sslContext() {
        return defaults.sslContext();
    }

    @Override
    public SSLParameters sslParameters() {
        return defaults.sslParameters();
    }

    @Override
    public Optional<Authenticator> authenticator() {
        return defaults.authenticator();
    }

    @Override
    public Version version() {
        return defaults.version();
    }

    @Override
    public Optional<Executor> executor() {
        return defaults.executor();
    }

    @Override
    public <T> HttpResponse<T> send(HttpRequest request,
                                    HttpResponse.BodyHandler<T> responseBodyHandler)
            throws IOException, InterruptedException {
        String body = requestBody(request);
        record(request, body);
        Route route = route(request, body);
        return response(request, route.status(), route.responseBody());
    }

    @Override
    public <T> CompletableFuture<HttpResponse<T>> sendAsync(
            HttpRequest request, HttpResponse.BodyHandler<T> responseBodyHandler) {
        String body = requestBody(request);
        record(request, body);
        if (EXCHANGE_PATH.equals(request.uri().getRawPath())
                && OLD_EXCHANGE_BODY.equals(body)) {
            oldExchangeSeen.countDown();
            return oldExchange(request);
        }
        if (EXCHANGE_PATH.equals(request.uri().getRawPath())
                && NEW_TTL_EXCHANGE_BODY.equals(body)) {
            newLiveExchangeSeen.countDown();
            return newLiveExchange(request);
        }
        Route route = route(request, body);
        return CompletableFuture.completedFuture(
                response(request, route.status(), route.responseBody()));
    }

    @Override
    public <T> CompletableFuture<HttpResponse<T>> sendAsync(
            HttpRequest request,
            HttpResponse.BodyHandler<T> responseBodyHandler,
            HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
        return sendAsync(request, responseBodyHandler);
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private <T> CompletableFuture<HttpResponse<T>> oldExchange(HttpRequest request) {
        if (oldExchangeMode == OldExchangeMode.THROW_SYNCHRONOUSLY) {
            throw new IllegalArgumentException("synthetic synchronous dispatch failure");
        }
        if (oldExchangeMode == OldExchangeMode.COMPLETE_EXCEPTIONALLY) {
            CompletableFuture<HttpResponse<T>> failed = new CompletableFuture<>();
            failed.completeExceptionally(new IOException("synthetic exchange failure"));
            return failed;
        }
        synchronized (oldLock) {
            pendingOld = new CompletableFuture<>();
            pendingOldRequest = request;
            if (releaseOld) {
                pendingOld.complete(response(request, 200,
                        sessionJson("token-old", "legacy west/1", 1_800_000L)));
            }
            return (CompletableFuture) pendingOld;
        }
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private <T> CompletableFuture<HttpResponse<T>> newLiveExchange(HttpRequest request) {
        synchronized (oldLock) {
            pendingNewLive = new CompletableFuture<>();
            pendingNewLiveRequest = request;
            if (releaseNewLive) {
                pendingNewLive.complete(response(request, 200,
                        sessionJson("token-new-live", "next \"blue\"/2", 60_000L)));
            }
            return (CompletableFuture) pendingNewLive;
        }
    }

    private Route route(HttpRequest request, String body) {
        String path = request.uri().getRawPath();
        if (!"POST".equals(request.method())) {
            return new Route(405, "{\"error\":\"method not allowed\"}");
        }
        if (CREATE_PATH.equals(path)) {
            return new Route(201,
                    "{\"id\":\"secret-2\",\"name\":\"next \\\"blue\\\"/2\","
                            + "\"secret\":\"new-\\\"s\\\\ecret\",\"status\":\"ACTIVE\"}");
        }
        if (EXCHANGE_PATH.equals(path)) {
            if (NEW_EXCHANGE_BODY.equals(body)) {
                return new Route(200, sessionJson("token-new-validation", "next \"blue\"/2",
                        1_800_000L));
            }
            if (NEW_TTL_EXCHANGE_BODY.equals(body)) {
                return new Route(200,
                        sessionJson("token-new-live", "next \"blue\"/2", 60_000L));
            }
            return new Route(400, "{\"error\":\"request violates contract fixture\"}");
        }
        if (REVOKE_PATH.equals(path)) {
            return new Route(200,
                    "{\"id\":\"secret-1\",\"name\":\"legacy west/1\","
                            + "\"status\":\"REVOKED\"}");
        }
        return new Route(404, "{\"error\":\"operation not in contract\"}");
    }

    private void record(HttpRequest request, String body) {
        Map<String, List<String>> headers = new LinkedHashMap<>();
        request.headers().map().forEach((name, values) ->
                headers.put(name.toLowerCase(Locale.ROOT), List.copyOf(values)));
        RecordedRequest recorded = new RecordedRequest(
                request.method(),
                request.uri().getRawPath(),
                request.uri().getRawQuery(),
                Map.copyOf(headers),
                body);
        synchronized (logLock) {
            requests.add(recorded);
            logLock.notifyAll();
        }
    }

    private static String requestBody(HttpRequest request) {
        HttpRequest.BodyPublisher publisher = request.bodyPublisher().orElse(null);
        if (publisher == null) {
            return "";
        }
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        CountDownLatch done = new CountDownLatch(1);
        CompletableFuture<Throwable> failure = new CompletableFuture<>();
        publisher.subscribe(new Flow.Subscriber<>() {
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
                failure.complete(throwable);
                done.countDown();
            }

            @Override
            public void onComplete() {
                done.countDown();
            }
        });
        try {
            if (!done.await(2, TimeUnit.SECONDS)) {
                throw new IllegalStateException("request body publisher did not complete");
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted while reading request body", interrupted);
        }
        if (failure.isDone()) {
            throw new IllegalStateException("request body publisher failed", failure.join());
        }
        return bytes.toString(StandardCharsets.UTF_8);
    }

    @SuppressWarnings("unchecked")
    private static <T> HttpResponse<T> response(HttpRequest request, int status, String body) {
        return new StringResponse<>(request, status, (T) body);
    }

    private record StringResponse<T>(HttpRequest request, int statusCode, T body)
            implements HttpResponse<T> {
        @Override
        public Optional<HttpResponse<T>> previousResponse() {
            return Optional.empty();
        }

        @Override
        public HttpHeaders headers() {
            return HttpHeaders.of(Map.of("content-type", List.of("application/json")),
                    (name, value) -> true);
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

    private static String sessionJson(String token, String name, long ttl) {
        return "{\"access_token\":\"" + token + "\",\"name\":\""
                + name.replace("\\", "\\\\").replace("\"", "\\\"")
                + "\",\"new_secret\":\"server-rollover\",\"ttl\":" + ttl + "}";
    }

    private static void verifyPinnedContract(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        requireContractText(contract,
                "\"commit_sha\": \"c3f3b52c845dd967cabbc21680e893292077d5ba\"");
        requireContractText(contract,
                "\"spec_path\": \"specifications/vcf-operations/log-management-openapi.json\"");
        requireContractText(contract, "\"operationId\": \"createAgentSecret\"");
        requireContractText(contract, "\"operationId\": \"createAgentSession\"");
        requireContractText(contract, "\"operationId\": \"revokeAgentSecret\"");
        requireContractText(contract, "\"name\": \"X-JWT-Token\"");
        if (count(contract, "\"operationId\"") != 3) {
            throw new IOException("contract mock requires exactly three operations");
        }
    }

    private static void requireContractText(String contract, String required) throws IOException {
        if (!contract.contains(required)) {
            throw new IOException("contract is not the pinned VCF log-management subset");
        }
    }

    private static int count(String text, String needle) {
        int count = 0;
        int from = 0;
        while ((from = text.indexOf(needle, from)) >= 0) {
            count++;
            from += needle.length();
        }
        return count;
    }
}
