import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Collectors;

public final class TestMain {
    private static final String API_PATH = "/v1/sddc-manager/trusted-certificates";
    private static final String TOKEN = "fixture-bearer-token";
    private static final String CERT_A =
            "-----BEGIN CERTIFICATE-----\nBASE-A\n-----END CERTIFICATE-----";
    private static final String CERT_B =
            "-----BEGIN CERTIFICATE-----\nBASE-B\n-----END CERTIFICATE-----";
    private static final String TARGET_CERT =
            "-----BEGIN CERTIFICATE-----\nTARGET \"retry\" \\\\ fixture\n"
                    + "-----END CERTIFICATE-----";

    public static void main(String[] args) throws Exception {
        assertPinnedContract();
        testAlternatingCollectionOrderAndRetrySafety();
        testDuplicateRaceIsReconciledWithoutSecondPost();
        testOnlyContractOperationsAreServed();
        testErrorsAreNotReportedAsSuccess();
        System.out.println("all checks passed");
    }

    private static void assertPinnedContract() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));

        assertContains(contract, "\"apiVersion\": \"9.1.0.0\"", "contract API version");
        assertContains(contract, "\"operationId\": \"getTrustedCertificates\"",
                "GET operationId");
        assertContains(contract, "\"operationId\": \"addTrustedCertificate\"",
                "POST operationId");
        assertContains(contract, "\"path\": \"" + API_PATH + "\"", "contract path");
        assertContains(contract, "\"status\": 409", "documented duplicate status");
        assertContains(contract, "\"deprecated\": true", "deprecated request property");

        assertContains(sources,
                "\"commitSha\": \"3949fc33339fc5ea1b77eadb258f1cf49aa88e26\"",
                "pinned repository commit");
        assertContains(sources,
                "\"specPath\": \"specifications/sddc-manager/sddc-manager-openapi.json\"",
                "pinned spec path");
        assertContains(sources, "\"operationId\": \"getTrustedCertificates\"",
                "GET provenance");
        assertContains(sources, "\"operationId\": \"addTrustedCertificate\"",
                "POST provenance");
    }

    private static void testAlternatingCollectionOrderAndRetrySafety() throws Exception {
        try (MockSddcManager mock = new MockSddcManager(false)) {
            SddcTrustedCertificatesClient client = new SddcTrustedCertificatesClient(
                    mock.baseUri(), TOKEN, HttpClient.newHttpClient());

            List<SddcTrustedCertificatesClient.TrustedCertificate> first =
                    client.listTrustedCertificates();
            List<SddcTrustedCertificatesClient.TrustedCertificate> second =
                    client.listTrustedCertificates();
            List<SddcTrustedCertificatesClient.TrustedCertificate> flippedAgain =
                    client.listTrustedCertificates();

            assertAliases(first, "vcf_AA", "vcf_BB");
            assertAliases(second, "vcf_AA", "vcf_BB");
            assertAliases(flippedAgain, "vcf_AA", "vcf_BB");
            assertEquals(first, second,
                    "collection output changed when the mock flipped response order");
            assertEquals(second, flippedAgain,
                    "collection output changed when the mock flipped response order again");
            assertImmutable(first);

            List<SddcTrustedCertificatesClient.TrustedCertificate> created =
                    client.ensureTrustedCertificate(TARGET_CERT);
            List<SddcTrustedCertificatesClient.TrustedCertificate> retried =
                    client.ensureTrustedCertificate(TARGET_CERT);
            assertAliases(created, "vcf_AA", "vcf_BB", "vcf_CC");
            assertAliases(retried, "vcf_AA", "vcf_BB", "vcf_CC");
            assertEquals(created, retried, "retry returned different collection output");
            assertEquals(1, mock.countCertificate(TARGET_CERT),
                    "the trusted certificate effect was duplicated");

            List<LoggedRequest> log = mock.requestLog();
            assertMethods(log, "GET", "GET", "GET", "GET", "POST", "GET");
            for (LoggedRequest request : log) {
                assertEquals(API_PATH, request.path(), "wrong request path");
                assertEquals("Bearer " + TOKEN, request.authorization(),
                        "wrong Authorization header");
                assertEquals("application/json", request.accept(), "wrong Accept header");
            }
            LoggedRequest post = log.get(4);
            assertEquals("application/json", post.contentType(),
                    "wrong POST Content-Type");
            assertEquals("{\"certificate\":" + jsonString(TARGET_CERT) + "}", post.body(),
                    "POST body must contain only the required certificate property");
            assertFalse(post.body().contains("certificateUsageType"),
                    "deprecated optional field was sent");
        }
    }

    private static void testDuplicateRaceIsReconciledWithoutSecondPost() throws Exception {
        try (MockSddcManager mock = new MockSddcManager(true)) {
            SddcTrustedCertificatesClient client = new SddcTrustedCertificatesClient(
                    mock.baseUri(), TOKEN, HttpClient.newHttpClient());

            List<SddcTrustedCertificatesClient.TrustedCertificate> result =
                    client.ensureTrustedCertificate(TARGET_CERT);

            assertAliases(result, "vcf_AA", "vcf_BB", "vcf_CC");
            assertMethods(mock.requestLog(), "GET", "POST", "GET");
            assertEquals(1, mock.countCertificate(TARGET_CERT),
                    "409 reconciliation duplicated the side effect");
        }
    }

    private static void testOnlyContractOperationsAreServed() throws Exception {
        try (MockSddcManager mock = new MockSddcManager(false)) {
            HttpRequest request = HttpRequest.newBuilder(mock.baseUri().resolve(API_PATH))
                    .header("Authorization", "Bearer " + TOKEN)
                    .method("PUT", HttpRequest.BodyPublishers.noBody())
                    .build();
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    request, HttpResponse.BodyHandlers.ofString());
            assertEquals(405, response.statusCode(),
                    "mock served a method not named by the protected contract");
            assertMethods(mock.requestLog(), "PUT");
        }
    }

    private static void testErrorsAreNotReportedAsSuccess() throws Exception {
        try (MockSddcManager mock = new MockSddcManager(false)) {
            mock.failNextGet(500);
            SddcTrustedCertificatesClient client = new SddcTrustedCertificatesClient(
                    mock.baseUri(), TOKEN, HttpClient.newHttpClient());
            try {
                client.listTrustedCertificates();
                throw new AssertionError("HTTP 500 was reported as success");
            } catch (IOException expected) {
                assertContains(expected.getMessage(), "500",
                        "error did not identify the HTTP status");
                assertFalse(expected.getMessage().contains(TOKEN),
                        "error leaked the bearer token");
                assertFalse(expected.getMessage().contains("BEGIN CERTIFICATE"),
                        "error leaked certificate text");
            }
        }
    }

    private static void assertImmutable(
            List<SddcTrustedCertificatesClient.TrustedCertificate> certificates) {
        try {
            certificates.add(new SddcTrustedCertificatesClient.TrustedCertificate(
                    "should-not-work", "should-not-work"));
            throw new AssertionError("returned collection is mutable");
        } catch (UnsupportedOperationException expected) {
            // Expected.
        }
    }

    private static void assertAliases(
            List<SddcTrustedCertificatesClient.TrustedCertificate> certificates,
            String... expected) {
        List<String> aliases = certificates.stream()
                .map(SddcTrustedCertificatesClient.TrustedCertificate::alias)
                .collect(Collectors.toList());
        assertEquals(List.of(expected), aliases, "aliases are not in stable sorted order");
    }

    private static void assertMethods(List<LoggedRequest> requests, String... methods) {
        List<String> actual = requests.stream()
                .map(LoggedRequest::method)
                .collect(Collectors.toList());
        assertEquals(List.of(methods), actual, "unexpected request sequence");
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char c = value.charAt(index);
            switch (c) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (c < 0x20) {
                        result.append(String.format("\\u%04x", (int) c));
                    } else {
                        result.append(c);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static void assertContains(String actual, String expected, String label) {
        if (actual == null || !actual.contains(expected)) {
            throw new AssertionError(label + " missing " + expected);
        }
    }

    private static void assertFalse(boolean condition, String message) {
        if (condition) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected=" + expected + ", actual=" + actual);
        }
    }

    private record LoggedRequest(
            String method,
            String path,
            String authorization,
            String accept,
            String contentType,
            String body) {
    }

    private record FixtureCertificate(String alias, String certificate) {
    }

    /**
     * Loopback-only SDDC Manager fixture. Its request log is deliberately exposed
     * to the tests, and its collection orientation toggles for every page response.
     */
    private static final class MockSddcManager implements AutoCloseable {
        private final HttpServer server;
        private final List<FixtureCertificate> certificates = new ArrayList<>();
        private final List<LoggedRequest> requests = new ArrayList<>();
        private final AtomicInteger pageResponses = new AtomicInteger();
        private boolean conflictOnNextPost;
        private int nextGetFailure;

        MockSddcManager(boolean conflictOnNextPost) throws IOException {
            this.conflictOnNextPost = conflictOnNextPost;
            certificates.add(new FixtureCertificate("vcf_BB", CERT_B));
            certificates.add(new FixtureCertificate("vcf_AA", CERT_A));
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext(API_PATH, this::handle);
            server.start();
        }

        URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
        }

        synchronized void failNextGet(int status) {
            nextGetFailure = status;
        }

        synchronized int countCertificate(String certificate) {
            return (int) certificates.stream()
                    .filter(item -> item.certificate().equals(certificate))
                    .count();
        }

        synchronized List<LoggedRequest> requestLog() {
            return List.copyOf(requests);
        }

        private void handle(HttpExchange exchange) throws IOException {
            byte[] requestBytes = exchange.getRequestBody().readAllBytes();
            String requestBody = new String(requestBytes, StandardCharsets.UTF_8);
            String path = exchange.getRequestURI().getRawPath();
            synchronized (this) {
                requests.add(new LoggedRequest(
                        exchange.getRequestMethod(),
                        path,
                        exchange.getRequestHeaders().getFirst("Authorization"),
                        exchange.getRequestHeaders().getFirst("Accept"),
                        exchange.getRequestHeaders().getFirst("Content-Type"),
                        requestBody));
            }

            if (!API_PATH.equals(path) || exchange.getRequestURI().getRawQuery() != null) {
                writeJson(exchange, 404,
                        "{\"errorCode\":\"NOT_FOUND\",\"message\":\"Unknown operation\"}");
                return;
            }
            switch (exchange.getRequestMethod()) {
                case "GET" -> handleGet(exchange);
                case "POST" -> handlePost(exchange, requestBody);
                default -> {
                    exchange.getResponseHeaders().set("Allow", "GET, POST");
                    writeJson(exchange, 405,
                            "{\"errorCode\":\"METHOD_NOT_ALLOWED\","
                                    + "\"message\":\"Operation is not in contract\"}");
                }
            }
        }

        private synchronized void handleGet(HttpExchange exchange) throws IOException {
            if (nextGetFailure != 0) {
                int status = nextGetFailure;
                nextGetFailure = 0;
                writeJson(exchange, status,
                        "{\"errorCode\":\"FIXTURE_FAILURE\",\"message\":\"forced failure\"}");
                return;
            }
            writePage(exchange);
        }

        private synchronized void handlePost(HttpExchange exchange, String body)
                throws IOException {
            String expectedBody = "{\"certificate\":" + jsonString(TARGET_CERT) + "}";
            if (!"application/json".equals(
                    exchange.getRequestHeaders().getFirst("Content-Type"))
                    || !expectedBody.equals(body)) {
                writeJson(exchange, 400,
                        "{\"errorCode\":\"BAD_REQUEST\",\"message\":\"wire shape mismatch\"}");
                return;
            }

            if (conflictOnNextPost) {
                conflictOnNextPost = false;
                addTargetIfAbsent();
                writeJson(exchange, 409,
                        "{\"errorCode\":\"ALREADY_EXISTS\","
                                + "\"message\":\"Trusted certificate already exists\"}");
                return;
            }
            if (certificates.stream().anyMatch(
                    item -> item.certificate().equals(TARGET_CERT))) {
                writeJson(exchange, 409,
                        "{\"errorCode\":\"ALREADY_EXISTS\","
                                + "\"message\":\"Trusted certificate already exists\"}");
                return;
            }
            addTargetIfAbsent();
            writePage(exchange);
        }

        private void addTargetIfAbsent() {
            if (certificates.stream().noneMatch(
                    item -> item.certificate().equals(TARGET_CERT))) {
                certificates.add(new FixtureCertificate("vcf_CC", TARGET_CERT));
            }
        }

        private void writePage(HttpExchange exchange) throws IOException {
            List<FixtureCertificate> snapshot = new ArrayList<>(certificates);
            snapshot.sort(Comparator
                    .comparing(FixtureCertificate::alias)
                    .thenComparing(FixtureCertificate::certificate));
            if ((pageResponses.incrementAndGet() & 1) == 1) {
                Collections.reverse(snapshot);
            }

            String elements = snapshot.stream()
                    .map(item -> "{\"alias\":" + jsonString(item.alias())
                            + ",\"certificate\":" + jsonString(item.certificate()) + "}")
                    .collect(Collectors.joining(","));
            writeJson(exchange, 200, "{\"elements\":[" + elements + "]}");
        }

        private static void writeJson(HttpExchange exchange, int status, String body)
                throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (var output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }
}
