import java.io.IOException;
import java.net.Authenticator;
import java.net.CookieHandler;
import java.net.ProxySelector;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicInteger;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;

public final class TestMain {
    private static final String DESCRIPTION =
            "Quarterly \"blue\" refresh\nowner: café \\ core\u0001";
    private static final String ICON_ID = "icon-\"quoted\"-\\-\b-end";
    private static final String NAME = "Release\t雪\rline";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <base-uri> <bearer-token>");
        }

        CountingHttpClient transport = new CountingHttpClient(HttpClient.newHttpClient());
        VcfAutomationClient client = new VcfAutomationClient(
                transport, URI.create(args[0]), args[1]);

        // Repeating the same field-replacement PATCH must still perform one call each time.
        invoke(client, transport, "dep 9/blue", DESCRIPTION, null, null);
        invoke(client, transport, "dep 9/blue", DESCRIPTION, null, null);

        // Cover all non-null members (including a present empty string) and URI delimiters.
        invoke(client, transport, "all?#/% café", "", ICON_ID, NAME);

        // With every optional value unset, the request body must be an empty JSON object.
        invoke(client, transport, "unset", null, null, null);

        if (transport.requestCount() != 4) {
            throw new AssertionError("unexpected total transport call count");
        }
    }

    private static void invoke(
            VcfAutomationClient client,
            CountingHttpClient transport,
            String deploymentId,
            String description,
            String iconId,
            String name) throws Exception {
        int before = transport.requestCount();
        HttpResponse<String> response = client.patchDeployment(
                deploymentId, description, iconId, name);
        int calls = transport.requestCount() - before;
        if (calls != 1) {
            throw new AssertionError(
                    "patchDeployment must use the supplied HttpClient exactly once; got " + calls);
        }
        if (!transport.wasLastResponse(response)) {
            throw new AssertionError("client did not return the HttpResponse from the supplied client");
        }
        if (response.statusCode() != 200) {
            throw new AssertionError("unexpected status: " + response.statusCode());
        }
        if (!response.request().method().equals("PATCH")) {
            throw new AssertionError("returned response is not for the PATCH request");
        }
        if (!response.headers().firstValue("X-Contract-Mock")
                .orElse("").equals("patch-deployment")) {
            throw new AssertionError("client did not return the loopback HttpResponse");
        }
        if (!response.body().contains("\"id\"")
                || !response.body().contains("\"description\"")) {
            throw new AssertionError("response did not contain the deployment fields");
        }
    }

    private static final class CountingHttpClient extends HttpClient {
        private final HttpClient delegate;
        private final AtomicInteger requestCount = new AtomicInteger();
        private volatile HttpResponse<?> lastResponse;

        private CountingHttpClient(HttpClient delegate) {
            this.delegate = delegate;
        }

        private int requestCount() {
            return requestCount.get();
        }

        private boolean wasLastResponse(HttpResponse<?> response) {
            return lastResponse == response;
        }

        @Override
        public Optional<CookieHandler> cookieHandler() {
            return delegate.cookieHandler();
        }

        @Override
        public Optional<Duration> connectTimeout() {
            return delegate.connectTimeout();
        }

        @Override
        public Redirect followRedirects() {
            return delegate.followRedirects();
        }

        @Override
        public Optional<ProxySelector> proxy() {
            return delegate.proxy();
        }

        @Override
        public SSLContext sslContext() {
            return delegate.sslContext();
        }

        @Override
        public SSLParameters sslParameters() {
            return delegate.sslParameters();
        }

        @Override
        public Optional<Authenticator> authenticator() {
            return delegate.authenticator();
        }

        @Override
        public Version version() {
            return delegate.version();
        }

        @Override
        public Optional<Executor> executor() {
            return delegate.executor();
        }

        @Override
        public <T> HttpResponse<T> send(
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler)
                throws IOException, InterruptedException {
            requestCount.incrementAndGet();
            HttpResponse<T> response = delegate.send(request, responseBodyHandler);
            lastResponse = response;
            return response;
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler) {
            requestCount.incrementAndGet();
            return delegate.sendAsync(request, responseBodyHandler).thenApply(response -> {
                lastResponse = response;
                return response;
            });
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request,
                HttpResponse.BodyHandler<T> responseBodyHandler,
                HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
            requestCount.incrementAndGet();
            return delegate.sendAsync(request, responseBodyHandler, pushPromiseHandler)
                    .thenApply(response -> {
                        lastResponse = response;
                        return response;
                    });
        }
    }
}
