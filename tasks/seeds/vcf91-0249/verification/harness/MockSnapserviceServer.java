import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
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
import java.nio.file.StandardOpenOption;
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
import java.util.concurrent.CompletionException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.locks.LockSupport;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSession;

/**
 * In-process stand-in for a vSAN Data Protection snapshot appliance.
 *
 * The routing table is built at startup from docs/contract.json: the appliance answers
 * exactly the operations that the contract names and nothing else. A request whose
 * method, path or query does not match a contract operation gets a 404 and is recorded
 * in the request log as unmatched, so an off-contract call is visible to the verifier
 * rather than silently tolerated.
 *
 * Every request is appended to the request log as one JSON object per line.
 */
final class MockSnapserviceServer {

    /** Header carrying the session token, per the api_key_auth scheme in the contract. */
    static final String SESSION_HEADER = "vmware-api-session-id";

    static final String CLUSTER = "domain-c9";
    static final String PROTECTION_GROUP = "pg-101";

    private static final class Operation {
        final String operationId;
        final String method;
        final Pattern pattern;
        final List<String> pathParameters;
        final Map<String, String> requiredQuery;
        final Set<String> allowedQuery;

        Operation(String operationId, String method, Pattern pattern, List<String> pathParameters,
                  Map<String, String> requiredQuery, Set<String> allowedQuery) {
            this.operationId = operationId;
            this.method = method;
            this.pattern = pattern;
            this.pathParameters = pathParameters;
            this.requiredQuery = requiredQuery;
            this.allowedQuery = allowedQuery;
        }
    }

    private static final class TaskFixture {
        final String taskId;
        final List<String> statuses;
        final String result;
        final String failureMessage;
        final boolean repeatLastStatus;
        final AtomicInteger polls = new AtomicInteger();

        TaskFixture(String taskId, List<String> statuses, String result, String failureMessage,
                    boolean repeatLastStatus) {
            this.taskId = taskId;
            this.statuses = statuses;
            this.result = result;
            this.failureMessage = failureMessage;
            this.repeatLastStatus = repeatLastStatus;
        }
    }

    private final String sessionId;
    private final Path requestLog;
    private final List<Operation> operations = new ArrayList<>();
    private final Map<String, TaskFixture> tasksById = new LinkedHashMap<>();
    private final Map<String, String> taskIdBySnapshotName = new LinkedHashMap<>();
    private final Map<String, Map<String, Object>> snapshotsById = new LinkedHashMap<>();
    private final Set<String> retentionUnits = new LinkedHashSet<>();
    private final AtomicInteger sequence = new AtomicInteger();
    private final CountDownLatch pauseProbeEvent = new CountDownLatch(1);
    private volatile boolean pauseProbeFirstPoll;
    private volatile boolean pauseProbeReleased;

    private String basePath;

    MockSnapserviceServer(Path contractPath, Path requestLog, String sessionId) throws IOException {
        this.sessionId = sessionId;
        this.requestLog = requestLog;
        loadContract(contractPath);
        loadFixtures();
        Files.createDirectories(requestLog.toAbsolutePath().getParent());
        Files.write(requestLog, new byte[0]);
    }

    private void loadContract(Path contractPath) throws IOException {
        Map<String, Object> contract = Json.asObject(
                Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8)));
        basePath = Json.asString(Json.asObject(contract.get("server")).get("base_path"));

        Map<String, Object> security = Json.asObject(contract.get("security"));
        String headerName = Json.asString(security.get("header_name"));
        if (!SESSION_HEADER.equals(headerName)) {
            throw new IllegalStateException("contract declares an unexpected auth header: " + headerName);
        }

        Map<String, Object> schemas = Json.asObject(contract.get("schemas"));
        Map<String, Object> retention = Json.asObject(schemas.get("Snapservice.RetentionPeriod"));
        Map<String, Object> retentionProps = Json.asObject(retention.get("properties"));
        for (Object unit : Json.asArray(Json.asObject(retentionProps.get("unit")).get("enum"))) {
            retentionUnits.add(Json.asString(unit));
        }

        for (Object raw : Json.asArray(contract.get("operations"))) {
            Map<String, Object> op = Json.asObject(raw);
            String operationId = Json.asString(op.get("operation_id"));
            String method = Json.asString(op.get("method"));
            String template = Json.asString(op.get("path_template"));

            List<String> pathParameters = new ArrayList<>();
            StringBuilder regex = new StringBuilder(Pattern.quote(basePath));
            Matcher m = Pattern.compile("\\{([a-zA-Z_]+)\\}").matcher(template);
            int last = 0;
            while (m.find()) {
                regex.append(Pattern.quote(template.substring(last, m.start())));
                regex.append("([^/]+)");
                pathParameters.add(m.group(1));
                last = m.end();
            }
            regex.append(Pattern.quote(template.substring(last)));

            Map<String, String> requiredQuery = new LinkedHashMap<>();
            for (Map.Entry<String, Object> e : Json.asObject(op.get("required_query")).entrySet()) {
                requiredQuery.put(e.getKey(), String.valueOf(e.getValue()));
            }
            Set<String> allowedQuery = new LinkedHashSet<>();
            for (Object name : Json.asArray(op.get("allowed_query_parameters"))) {
                allowedQuery.add(Json.asString(name));
            }

            operations.add(new Operation(operationId, method, Pattern.compile("^" + regex + "$"),
                    pathParameters, requiredQuery, allowedQuery));
        }
    }

    private void loadFixtures() {
        register(new TaskFixture("task-0001", Arrays.asList("PENDING", "RUNNING", "SUCCEEDED"),
                "snap-1001", null, false), "nightly-keep-7d");
        register(new TaskFixture("task-0002", Arrays.asList("RUNNING", "SUCCEEDED"),
                "snap-1002", null, false), "adhoc-no-retention");
        register(new TaskFixture("task-0003", Arrays.asList("PENDING", "RUNNING", "BLOCKED", "FAILED"),
                null, "Quiescing failed on virtual machine vm-42 while snapshotting the protection group.",
                false), "doomed-snapshot");
        register(new TaskFixture("task-0004", Arrays.asList("RUNNING"), null, null, true),
                "stuck-forever");
        register(new TaskFixture("task-0005", Arrays.asList("RUNNING", "SUCCEEDED"),
                "snap-1005", null, false), "pause-interrupt-probe");

        snapshotsById.put("snap-1001", snapshotInfo("nightly-keep-7d", "2026-08-04T02:15:28.000Z",
                "2026-08-04T02:15:30.000Z", "2026-08-11T02:15:30.000Z"));
        snapshotsById.put("snap-1002", snapshotInfo("adhoc-no-retention", "2026-08-04T02:20:11.000Z",
                "2026-08-04T02:20:13.000Z", null));
    }

    private void register(TaskFixture fixture, String snapshotName) {
        tasksById.put(fixture.taskId, fixture);
        taskIdBySnapshotName.put(snapshotName, fixture.taskId);
    }

    private Map<String, Object> snapshotInfo(String name, String startTime, String endTime, String expiresAt) {
        Map<String, Object> vmSnapshot = new LinkedHashMap<>();
        vmSnapshot.put("snapshot", "vmsnap-" + name.hashCode());
        vmSnapshot.put("name", name);
        vmSnapshot.put("created_at", startTime);
        vmSnapshot.put("vm", "vm-42");

        Map<String, Object> info = new LinkedHashMap<>();
        info.put("name", name);
        info.put("snapshot_type", "ONE_TIME");
        info.put("start_time", startTime);
        info.put("end_time", endTime);
        info.put("pg", PROTECTION_GROUP);
        info.put("vm_snapshots", new ArrayList<>(List.of(vmSnapshot)));
        // expires_at is omitted entirely when the snapshot has no expiry, exactly as the
        // specification describes it, so a client must cope with an absent property.
        if (expiresAt != null) {
            info.put("expires_at", expiresAt);
        }
        return info;
    }

    int start() {
        return 0;
    }

    void stop() {
    }

    String baseUrl() {
        return "http://snapservice.test" + basePath;
    }

    HttpClient httpClient() {
        return new ApplianceHttpClient();
    }

    boolean awaitPauseProbeEvent(long timeout, TimeUnit unit) throws InterruptedException {
        return pauseProbeEvent.await(timeout, unit);
    }

    boolean pauseProbeFirstPollObserved() {
        return pauseProbeFirstPoll;
    }

    void signalPauseProbeClientDone() {
        pauseProbeEvent.countDown();
    }

    void releasePauseProbeFirstPoll() {
        pauseProbeReleased = true;
    }

    private Reply dispatch(HttpRequest request) throws IOException {
        String method = request.method();
        String path = request.uri().getRawPath();
        String query = request.uri().getRawQuery();
        String body = new String(readRequestBody(request), StandardCharsets.UTF_8);

        Map<String, String> queryParams = parseQuery(query);
        Operation matched = null;
        Matcher matcher = null;
        for (Operation op : operations) {
            Matcher candidate = op.pattern.matcher(path);
            if (!candidate.matches() || !op.method.equals(method)) {
                continue;
            }
            if (!queryMatches(op, queryParams)) {
                continue;
            }
            matched = op;
            matcher = candidate;
            break;
        }

        Reply reply;
        if (matched == null) {
            reply = error(404, "com.vmware.snapservice.no_such_operation",
                    "No operation in docs/contract.json matches " + method + " " + path
                            + (query == null ? "" : "?" + query) + ".");
        } else if (!sessionId.equals(header(request.headers(), SESSION_HEADER))) {
            reply = error(401, "com.vmware.vapi.std.errors.unauthenticated",
                    "Missing or invalid " + SESSION_HEADER + " header.");
        } else {
            Map<String, String> pathParams = new LinkedHashMap<>();
            for (int i = 0; i < matched.pathParameters.size(); i++) {
                pathParams.put(matched.pathParameters.get(i), decode(matcher.group(i + 1)));
            }
            reply = handle(matched, pathParams, request.headers(), body);
        }

        log(method, path, query, request.headers(), body,
                matched == null ? null : matched.operationId, reply.status);
        return reply;
    }

    private boolean queryMatches(Operation op, Map<String, String> queryParams) {
        for (Map.Entry<String, String> required : op.requiredQuery.entrySet()) {
            if (!required.getValue().equals(queryParams.get(required.getKey()))) {
                return false;
            }
        }
        return op.allowedQuery.containsAll(queryParams.keySet());
    }

    private Reply handle(Operation op, Map<String, String> pathParams, HttpHeaders headers, String body) {
        switch (op.operationId) {
            case "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task":
                return createSnapshot(pathParams, headers, body);
            case "Snapservice.Tasks_get":
                return getTask(pathParams.get("task"));
            case "Snapservice.Clusters.ProtectionGroups.Snapshots_get":
                return getSnapshot(pathParams);
            default:
                return error(500, "com.vmware.vapi.std.errors.error",
                        "Contract operation " + op.operationId + " has no fixture in the mock.");
        }
    }

    private Reply createSnapshot(Map<String, String> pathParams, HttpHeaders headers, String body) {
        String contentType = header(headers, "content-type");
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            return error(415, "com.vmware.vapi.std.errors.unsupported_media_type",
                    "The request body must be sent as application/json, got: " + contentType);
        }
        if (!CLUSTER.equals(pathParams.get("cluster")) || !PROTECTION_GROUP.equals(pathParams.get("pg"))) {
            return error(404, "com.vmware.vapi.std.errors.not_found",
                    "No such cluster or protection group: " + pathParams);
        }

        Map<String, Object> spec;
        try {
            spec = Json.asObject(Json.parse(body));
        } catch (RuntimeException e) {
            return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                    "Request body is not a JSON object: " + e.getMessage());
        }

        Set<String> unknown = new LinkedHashSet<>(spec.keySet());
        unknown.removeAll(Set.of("name", "retention"));
        if (!unknown.isEmpty()) {
            return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                    "CreateSpec has no such properties: " + unknown);
        }
        if (!spec.containsKey("name") || !(spec.get("name") instanceof String)
                || ((String) spec.get("name")).isEmpty()) {
            return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                    "CreateSpec.name is required and must be a non-empty string.");
        }

        if (spec.containsKey("retention")) {
            Object retentionValue = spec.get("retention");
            if (!(retentionValue instanceof Map)) {
                return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                        "CreateSpec.retention must be a RetentionPeriod object when present; omit the "
                                + "property when there is no retention to express, got " + Json.describe(retentionValue) + ".");
            }
            Map<String, Object> retention = Json.asObject(retentionValue);
            Set<String> unknownRetention = new LinkedHashSet<>(retention.keySet());
            unknownRetention.removeAll(Set.of("unit", "duration"));
            if (!unknownRetention.isEmpty()) {
                return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                        "RetentionPeriod has no such properties: " + unknownRetention);
            }
            if (!(retention.get("unit") instanceof String)
                    || !retentionUnits.contains(retention.get("unit"))) {
                return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                        "RetentionPeriod.unit is required and must be one of " + retentionUnits
                                + ", got " + Json.describe(retention.get("unit")) + ".");
            }
            if (!(retention.get("duration") instanceof Long)) {
                return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                        "RetentionPeriod.duration is required and must be a JSON integer, got "
                                + Json.describe(retention.get("duration")) + ".");
            }
        }

        String name = (String) spec.get("name");
        String taskId = taskIdBySnapshotName.get(name);
        if (taskId == null) {
            return error(400, "com.vmware.vapi.std.errors.invalid_argument",
                    "This appliance fixture has no scenario for snapshot name '" + name + "'.");
        }
        // A $Task operation answers 202 with the task identifier as a bare JSON string.
        return new Reply(202, Json.write(taskId));
    }

    private Reply getTask(String taskId) {
        TaskFixture fixture = tasksById.get(taskId);
        if (fixture == null) {
            return error(404, "com.vmware.vapi.std.errors.not_found", "No such task: " + taskId);
        }
        int poll = fixture.polls.incrementAndGet();
        if ("task-0005".equals(taskId) && poll == 1) {
            pauseProbeFirstPoll = true;
            pauseProbeEvent.countDown();
            boolean interrupted = false;
            while (!pauseProbeReleased) {
                LockSupport.parkNanos(100_000L);
                if (Thread.interrupted()) {
                    interrupted = true;
                }
            }
            if (interrupted) {
                Thread.currentThread().interrupt();
            }
        }
        int index = Math.min(poll - 1, fixture.statuses.size() - 1);
        String status = fixture.repeatLastStatus
                ? fixture.statuses.get(fixture.statuses.size() - 1)
                : fixture.statuses.get(index);

        Map<String, Object> info = new LinkedHashMap<>();
        info.put("description", localizable("com.vmware.snapservice.protection_group.snapshot.create",
                "Create a protection group snapshot."));
        info.put("service", "com.vmware.snapservice.clusters.protection_groups.snapshots");
        info.put("operation", "create");
        info.put("status", status);
        info.put("cancelable", Boolean.FALSE);
        if (!"PENDING".equals(status)) {
            info.put("start_time", "2026-08-04T02:15:28.000Z");
        }
        if ("SUCCEEDED".equals(status)) {
            info.put("end_time", "2026-08-04T02:15:30.000Z");
            info.put("result", fixture.result);
        }
        if ("FAILED".equals(status)) {
            info.put("end_time", "2026-08-04T02:15:31.000Z");
            Map<String, Object> error = new LinkedHashMap<>();
            error.put("messages", new ArrayList<>(List.of(
                    localizable("com.vmware.snapservice.snapshot.quiesce_failed", fixture.failureMessage))));
            info.put("error", error);
        }
        return new Reply(200, Json.write(info));
    }

    private Reply getSnapshot(Map<String, String> pathParams) {
        if (!CLUSTER.equals(pathParams.get("cluster")) || !PROTECTION_GROUP.equals(pathParams.get("pg"))) {
            return error(404, "com.vmware.vapi.std.errors.not_found",
                    "No such cluster or protection group: " + pathParams);
        }
        Map<String, Object> info = snapshotsById.get(pathParams.get("snapshot"));
        if (info == null) {
            return error(404, "com.vmware.vapi.std.errors.not_found",
                    "No such snapshot: " + pathParams.get("snapshot"));
        }
        return new Reply(200, Json.write(info));
    }

    private static Map<String, Object> localizable(String id, String defaultMessage) {
        Map<String, Object> message = new LinkedHashMap<>();
        message.put("id", id);
        message.put("default_message", defaultMessage);
        message.put("args", new ArrayList<>());
        return message;
    }

    private void log(String method, String path, String query, HttpHeaders headers, String body,
                     String operationId, int status) {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("seq", sequence.incrementAndGet());
        entry.put("method", method);
        entry.put("path", path);
        entry.put("query", query);
        entry.put("operation_id", operationId);
        entry.put("status", status);

        Map<String, Object> loggedHeaders = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> h : headers.map().entrySet()) {
            loggedHeaders.put(h.getKey().toLowerCase(Locale.ROOT), String.join(", ", h.getValue()));
        }
        entry.put("headers", loggedHeaders);
        entry.put("body", body);

        try {
            Files.writeString(requestLog, Json.write(entry) + System.lineSeparator(),
                    StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    static List<Map<String, Object>> readRequestLog(Path requestLog) throws IOException {
        List<Map<String, Object>> entries = new ArrayList<>();
        for (String line : Files.readAllLines(requestLog, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                entries.add(Json.asObject(Json.parse(line)));
            }
        }
        return entries;
    }

    private static String header(HttpHeaders headers, String name) {
        return headers.firstValue(name).orElse(null);
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> params = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return params;
        }
        for (String pair : rawQuery.split("&")) {
            int eq = pair.indexOf('=');
            if (eq < 0) {
                params.put(decode(pair), "");
            } else {
                params.put(decode(pair.substring(0, eq)), decode(pair.substring(eq + 1)));
            }
        }
        return params;
    }

    private static String decode(String value) {
        return java.net.URLDecoder.decode(value, StandardCharsets.UTF_8);
    }

    private static Reply error(int status, String id, String message) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error_type", id);
        body.put("messages", new ArrayList<>(List.of(localizable(id, message))));
        return new Reply(status, Json.write(body));
    }

    private static byte[] readRequestBody(HttpRequest request) throws IOException {
        if (request.bodyPublisher().isEmpty()) {
            return new byte[0];
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        CompletableFuture<Void> complete = new CompletableFuture<>();
        request.bodyPublisher().get().subscribe(new Flow.Subscriber<ByteBuffer>() {
            @Override
            public void onSubscribe(Flow.Subscription subscription) {
                subscription.request(Long.MAX_VALUE);
            }

            @Override
            public void onNext(ByteBuffer item) {
                byte[] bytes = new byte[item.remaining()];
                item.get(bytes);
                out.writeBytes(bytes);
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
            return out.toByteArray();
        } catch (CompletionException e) {
            throw new IOException("could not read request body", e.getCause());
        }
    }

    private final class ApplianceHttpClient extends HttpClient {
        private final SSLContext sslContext = defaultSslContext();

        @Override
        public Optional<CookieHandler> cookieHandler() {
            return Optional.empty();
        }

        @Override
        public Optional<Duration> connectTimeout() {
            return Optional.of(Duration.ofSeconds(1));
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
        public Optional<Executor> executor() {
            return Optional.empty();
        }

        @Override
        public <T> HttpResponse<T> send(HttpRequest request, HttpResponse.BodyHandler<T> handler)
                throws IOException {
            Reply reply = dispatch(request);
            HttpHeaders responseHeaders = HttpHeaders.of(
                    Map.of("content-type", List.of("application/json")), (name, value) -> true);
            byte[] bytes = reply.body.getBytes(StandardCharsets.UTF_8);
            T body = applyBodyHandler(handler, reply.status, responseHeaders, bytes);
            return new ApplianceResponse<>(request, reply.status, responseHeaders, body);
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> handler) {
            try {
                return CompletableFuture.completedFuture(send(request, handler));
            } catch (IOException e) {
                return CompletableFuture.failedFuture(e);
            }
        }

        @Override
        public <T> CompletableFuture<HttpResponse<T>> sendAsync(
                HttpRequest request, HttpResponse.BodyHandler<T> handler,
                HttpResponse.PushPromiseHandler<T> pushPromiseHandler) {
            return sendAsync(request, handler);
        }
    }

    private static <T> T applyBodyHandler(HttpResponse.BodyHandler<T> handler, int status,
                                          HttpHeaders headers, byte[] bytes) throws IOException {
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
            @Override
            public void request(long count) {
            }

            @Override
            public void cancel() {
            }
        });
        subscriber.onNext(List.of(ByteBuffer.wrap(bytes)));
        subscriber.onComplete();
        try {
            return subscriber.getBody().toCompletableFuture().join();
        } catch (CompletionException e) {
            throw new IOException("could not decode mock response", e.getCause());
        }
    }

    private static final class ApplianceResponse<T> implements HttpResponse<T> {
        private final HttpRequest request;
        private final int status;
        private final HttpHeaders headers;
        private final T body;

        ApplianceResponse(HttpRequest request, int status, HttpHeaders headers, T body) {
            this.request = request;
            this.status = status;
            this.headers = headers;
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
            return headers;
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

    private static SSLContext defaultSslContext() {
        try {
            return SSLContext.getDefault();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static final class Reply {
        final int status;
        final String body;

        Reply(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }
}
