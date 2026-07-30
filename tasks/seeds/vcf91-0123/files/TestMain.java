import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class TestMain {
    private static final String COMMIT =
            "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";
    private static final String SPEC_PATH =
            "specifications/vsphere/openapi/automation/vcenter.yaml";
    private static final String SPEC_BLOB =
            "8028b0824c4ff3503d05f44814f967938a795c40";
    private static final String CONTRACT_SHA256 =
            "0f4acc7bfb5f93b2d63a2a65c811f8b645820ab5f6a200e9e204c25e0cf48d81";
    private static final String SOURCES_SHA256 =
            "5903a5a71765c61b5f86ec2084c3000c46db28e535697269f3e386e7ff24d2e0";

    private static final String CATEGORY_OPERATION =
            "Cis.Tagging.Category_create";
    private static final String TAG_OPERATION =
            "Cis.Tagging.Tag_create";
    private static final String ATTACH_OPERATION =
            "Cis.Tagging.TagAssociation_attach";

    private static final String SESSION_ID = "session-contract-fixture";
    private static final String CATEGORY_ID = "category-42";
    private static final String TAG_ID = "tag-42";

    private static final String CATEGORY_BODY =
            "{\"name\":\"Deployment Ring\",\"description\":"
                    + "\"Managed by release automation\",\"cardinality\":"
                    + "\"MULTIPLE\",\"associable_types\":[\"VirtualMachine\"]}";
    private static final String TAG_BODY =
            "{\"name\":\"canary\",\"description\":\"Early rollout cohort\","
                    + "\"category_id\":\"category-42\"}";
    private static final String ATTACH_BODY =
            "{\"object_id\":{\"type\":\"VirtualMachine\",\"id\":\"vm-202\"}}";

    public static void main(String[] args) throws Exception {
        verifyProtectedProjection();
        verifyConstructorValidation();
        verifyPartialFailureAndWire();
        System.out.println(
                "All vCenter tagging contract checks passed.");
    }

    private static void verifyProtectedProjection() throws Exception {
        Path contractPath = Path.of("docs", "contract.json");
        Path sourcesPath = Path.of("docs", "official_sources.json");
        byte[] contractBytes = Files.readAllBytes(contractPath);
        byte[] sourcesBytes = Files.readAllBytes(sourcesPath);

        equal(sha256(contractBytes), CONTRACT_SHA256,
                "protected contract digest");
        equal(sha256(sourcesBytes), SOURCES_SHA256,
                "protected provenance digest");

        String contract = new String(contractBytes, StandardCharsets.UTF_8);
        String sources = new String(sourcesBytes, StandardCharsets.UTF_8);
        containsExactly(contract, "\"operationId\":", 3,
                "focused contract operation count");
        containsExactly(sources, "\"operationId\":", 3,
                "official operation record count");
        containsExactly(sources, "\"operationIds\":", 1,
                "official operationIds list");

        for (String operationId : List.of(
                CATEGORY_OPERATION, TAG_OPERATION, ATTACH_OPERATION)) {
            contains(contract, "\"" + operationId + "\"",
                    "contract records " + operationId);
            contains(sources, "\"" + operationId + "\"",
                    "provenance records " + operationId);
        }
        for (String expected : List.of(
                COMMIT, SPEC_PATH, SPEC_BLOB,
                "\"apiVersion\": \"9.1.0.0\"",
                "\"name\": \"vmware-api-session-id\"")) {
            contains(contract, expected,
                    "contract provenance/security value " + expected);
        }
        for (String expected : List.of(COMMIT, SPEC_PATH, SPEC_BLOB)) {
            contains(sources, expected,
                    "official provenance value " + expected);
        }
        contains(contract,
                "\"unsetBehavior\": \"omit; the server generates an identifier\"",
                "optional identifier omission rule");
        containsExactly(contract,
                "\"unsetBehavior\": \"omit; the server generates an identifier\"",
                2, "both optional create identifiers are projected");
        contains(contract, "\"201\": {",
                "create success status");
        contains(contract, "\"204\": {",
                "attach success status");
        contains(contract, "\"403\": {",
                "attach authorization failure status");
    }

    private static void verifyConstructorValidation() {
        HttpClient http = HttpClient.newHttpClient();
        throwsType(IllegalArgumentException.class,
                () -> new VcenterTaggingClient(
                        URI.create("/relative"),
                        SESSION_ID,
                        http),
                "relative server origin rejected");
        throwsType(IllegalArgumentException.class,
                () -> new VcenterTaggingClient(
                        URI.create("http://127.0.0.1:1/api"),
                        SESSION_ID,
                        http),
                "non-root server origin rejected");
        throwsType(IllegalArgumentException.class,
                () -> new VcenterTaggingClient(
                        URI.create("http://127.0.0.1:1"),
                        "  ",
                        http),
                "blank session rejected");
    }

    private static void verifyPartialFailureAndWire() throws Exception {
        try (ContractMock mock = new ContractMock()) {
            VcenterTaggingClient client = new VcenterTaggingClient(
                    mock.origin(),
                    SESSION_ID,
                    HttpClient.newHttpClient());
            VcenterTaggingClient.ChangeRequest request =
                    new VcenterTaggingClient.ChangeRequest(
                            new VcenterTaggingClient.CategorySpec(
                                    "Deployment Ring",
                                    "Managed by release automation",
                                    "MULTIPLE",
                                    List.of("VirtualMachine"),
                                    null),
                            new VcenterTaggingClient.TagSpec(
                                    "canary",
                                    "Early rollout cohort",
                                    null),
                            new VcenterTaggingClient.DynamicId(
                                    "VirtualMachine",
                                    "vm-202"));

            VcenterTaggingClient.ChangeReport report =
                    client.createAndAttach(request);
            List<VcenterTaggingClient.StepOutcome> outcomes =
                    report.outcomes();
            equal(outcomes.size(), 3,
                    "one report outcome per attempted operation");

            assertSuccess(outcomes.get(0), CATEGORY_OPERATION, CATEGORY_ID,
                    "category outcome");
            assertSuccess(outcomes.get(1), TAG_OPERATION, TAG_ID,
                    "tag outcome");

            VcenterTaggingClient.StepOutcome attach = outcomes.get(2);
            equal(attach.operationId(), ATTACH_OPERATION,
                    "attachment operationId");
            equal(attach.status(),
                    VcenterTaggingClient.StepStatus.FAILED,
                    "attachment failure status");
            equal(attach.resourceId(), null,
                    "failed attachment has no resource id");
            check(attach.error() != null,
                    "failed attachment carries its error");
            equal(attach.error().statusCode(), 403,
                    "attachment HTTP status");
            equal(attach.error().errorType(), "UNAUTHORIZED",
                    "attachment error_type");
            equal(attach.error().getMessage(),
                    "Attach privilege missing",
                    "attachment first default_message");
            check(!attach.error().toString().contains(SESSION_ID),
                    "attachment error does not disclose session");

            throwsType(UnsupportedOperationException.class,
                    () -> outcomes.add(outcomes.get(0)),
                    "report outcomes are immutable");

            List<RecordedRequest> log = mock.requestLog();
            equal(log.size(), 3,
                    "exactly three requests reached the mock");
            assertWire(log.get(0), 0, CATEGORY_OPERATION,
                    "/api/cis/tagging/category", CATEGORY_BODY);
            assertWire(log.get(1), 1, TAG_OPERATION,
                    "/api/cis/tagging/tag", TAG_BODY);
            assertWire(log.get(2), 2, ATTACH_OPERATION,
                    "/api/cis/tagging/tag-association/tag-42?action=attach",
                    ATTACH_BODY);

            check(!log.get(0).body().contains("\"category_id\""),
                    "unset category_id omitted");
            check(!log.get(0).body().contains("null"),
                    "category request contains no null placeholder");
            check(!log.get(1).body().contains("\"tag_id\""),
                    "unset tag_id omitted");
            check(!log.get(1).body().contains("null"),
                    "tag request contains no null placeholder");
        }
    }

    private static void assertSuccess(
            VcenterTaggingClient.StepOutcome outcome,
            String operationId,
            String resourceId,
            String label) {
        equal(outcome.operationId(), operationId,
                label + " operationId");
        equal(outcome.status(),
                VcenterTaggingClient.StepStatus.SUCCEEDED,
                label + " status");
        equal(outcome.resourceId(), resourceId,
                label + " resource id");
        equal(outcome.error(), null,
                label + " has no error");
    }

    private static void assertWire(
            RecordedRequest request,
            int sequence,
            String operationId,
            String target,
            String body) {
        equal(request.sequence(), sequence,
                "request sequence " + sequence);
        equal(request.operationId(), operationId,
                "request operation " + sequence);
        equal(request.method(), "POST",
                "request method " + sequence);
        equal(request.rawTarget(), target,
                "request target " + sequence);
        equal(request.sessionId(), SESSION_ID,
                "request session header " + sequence);
        equal(request.accept(), "application/json",
                "request Accept " + sequence);
        equal(request.contentType(), "application/json",
                "request Content-Type " + sequence);
        equal(request.authorization(), null,
                "request has no Authorization " + sequence);
        equal(request.body(), body,
                "request body bytes " + sequence);
        check(request.wireValid(),
                "mock accepted exact wire for request " + sequence);
    }

    private static String sha256(byte[] value) throws Exception {
        return HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(value));
    }

    private static void contains(
            String text, String needle, String message) {
        check(text.contains(needle), message);
    }

    private static void containsExactly(
            String text, String needle, int expected, String message) {
        int count = 0;
        int offset = 0;
        while ((offset = text.indexOf(needle, offset)) >= 0) {
            count++;
            offset += needle.length();
        }
        equal(count, expected, message);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object actual, Object expected, String message) {
        if (!java.util.Objects.equals(actual, expected)) {
            throw new AssertionError(
                    message + " (expected " + expected
                            + ", got " + actual + ")");
        }
    }

    private static void throwsType(
            Class<? extends Throwable> expected,
            ThrowingRunnable action,
            String message) {
        Throwable caught = null;
        try {
            action.run();
        } catch (Throwable error) {
            caught = error;
        }
        if (caught == null || !expected.isInstance(caught)) {
            throw new AssertionError(
                    message + " (expected " + expected.getSimpleName()
                            + ", got " + caught + ")");
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    private record RecordedRequest(
            int sequence,
            String operationId,
            boolean wireValid,
            String method,
            String rawTarget,
            String sessionId,
            String authorization,
            String accept,
            String contentType,
            String body) {
    }

    /**
     * Loopback fixture pinned by the protected contract digest above. It logs
     * every request, but returns operation responses only for the three
     * operationIds projected in docs/contract.json.
     */
    private static final class ContractMock implements AutoCloseable {
        private final HttpServer server;
        private final ExecutorService executor;
        private final List<RecordedRequest> requests = new ArrayList<>();

        ContractMock() throws IOException {
            server = HttpServer.create(
                    new InetSocketAddress("127.0.0.1", 0), 0);
            executor = Executors.newCachedThreadPool();
            server.setExecutor(executor);
            server.createContext("/", this::handle);
            server.start();
        }

        URI origin() {
            return URI.create(
                    "http://127.0.0.1:" + server.getAddress().getPort());
        }

        synchronized List<RecordedRequest> requestLog() {
            return List.copyOf(requests);
        }

        private void handle(HttpExchange exchange) throws IOException {
            byte[] bodyBytes = exchange.getRequestBody().readAllBytes();
            String body = new String(bodyBytes, StandardCharsets.UTF_8);
            String target = exchange.getRequestURI().toString();
            Headers headers = exchange.getRequestHeaders();
            String operationId = operationFor(
                    exchange.getRequestMethod(), target);
            int sequence;
            boolean valid;
            synchronized (this) {
                sequence = requests.size();
                valid = validWire(
                        sequence, operationId, exchange, target, body);
                requests.add(new RecordedRequest(
                        sequence,
                        operationId,
                        valid,
                        exchange.getRequestMethod(),
                        target,
                        headers.getFirst("vmware-api-session-id"),
                        headers.getFirst("Authorization"),
                        headers.getFirst("Accept"),
                        headers.getFirst("Content-Type"),
                        body));
            }

            if (operationId == null) {
                sendJson(exchange, 404,
                        error("NOT_FOUND", "Operation is outside contract"));
                return;
            }
            if (!valid) {
                sendJson(exchange, 400,
                        error("INVALID_ARGUMENT", "Wire contract mismatch"));
                return;
            }
            if (operationId.equals(CATEGORY_OPERATION)) {
                sendJson(exchange, 201, "\"" + CATEGORY_ID + "\"");
                return;
            }
            if (operationId.equals(TAG_OPERATION)) {
                sendJson(exchange, 201, "\"" + TAG_ID + "\"");
                return;
            }
            sendJson(exchange, 403,
                    error("UNAUTHORIZED", "Attach privilege missing"));
        }

        private boolean validWire(
                int sequence,
                String operationId,
                HttpExchange exchange,
                String target,
                String body) {
            if (operationId == null
                    || !exchange.getRequestMethod().equals("POST")) {
                return false;
            }
            Headers headers = exchange.getRequestHeaders();
            if (!SESSION_ID.equals(
                    headers.getFirst("vmware-api-session-id"))
                    || headers.getFirst("Authorization") != null
                    || !"application/json".equals(
                    headers.getFirst("Accept"))
                    || !"application/json".equals(
                    headers.getFirst("Content-Type"))) {
                return false;
            }
            return switch (sequence) {
                case 0 -> operationId.equals(CATEGORY_OPERATION)
                        && target.equals("/api/cis/tagging/category")
                        && body.equals(CATEGORY_BODY);
                case 1 -> operationId.equals(TAG_OPERATION)
                        && target.equals("/api/cis/tagging/tag")
                        && body.equals(TAG_BODY);
                case 2 -> operationId.equals(ATTACH_OPERATION)
                        && target.equals(
                        "/api/cis/tagging/tag-association/tag-42"
                                + "?action=attach")
                        && body.equals(ATTACH_BODY);
                default -> false;
            };
        }

        private String operationFor(String method, String target) {
            if (!method.equals("POST")) {
                return null;
            }
            if (target.equals("/api/cis/tagging/category")) {
                return CATEGORY_OPERATION;
            }
            if (target.equals("/api/cis/tagging/tag")) {
                return TAG_OPERATION;
            }
            if (target.matches(
                    "/api/cis/tagging/tag-association/[^/?]+"
                            + "\\?action=attach")) {
                return ATTACH_OPERATION;
            }
            return null;
        }

        private static String error(String type, String message) {
            return "{\"error_type\":\"" + type + "\",\"messages\":[{"
                    + "\"id\":\"com.vmware.vapi.std.errors\","
                    + "\"default_message\":\"" + message + "\","
                    + "\"args\":[]}]}";
        }

        private static void sendJson(
                HttpExchange exchange, int status, String body)
                throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set(
                    "Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (var output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        }

        @Override
        public void close() {
            server.stop(0);
            executor.shutdownNow();
        }
    }
}
