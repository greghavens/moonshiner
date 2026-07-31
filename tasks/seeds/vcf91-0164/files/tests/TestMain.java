import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Protected acceptance harness for VksNamespaceBackupClient. */
public final class TestMain {
    private record Route(String name, String method, Pattern path) {
    }

    private static final class BodyCollector implements Flow.Subscriber<ByteBuffer> {
        private final ByteArrayOutputStream output = new ByteArrayOutputStream();
        private final CountDownLatch done = new CountDownLatch(1);
        private Throwable error;

        @Override
        public void onSubscribe(Flow.Subscription subscription) {
            subscription.request(Long.MAX_VALUE);
        }

        @Override
        public void onNext(ByteBuffer item) {
            byte[] bytes = new byte[item.remaining()];
            item.get(bytes);
            output.writeBytes(bytes);
        }

        @Override
        public void onError(Throwable failure) {
            error = failure;
            done.countDown();
        }

        @Override
        public void onComplete() {
            done.countDown();
        }

        byte[] finish() throws InterruptedException {
            if (!done.await(2, TimeUnit.SECONDS)) {
                throw new AssertionError("body publisher did not complete");
            }
            if (error != null) {
                throw new AssertionError("body publisher failed");
            }
            return output.toByteArray();
        }
    }

    private static final class ContractExchange
            implements VksNamespaceBackupClient.Exchange {
        private static final Set<String> EXPECTED_NAMES = Set.of(
                "getSupervisorNamespace",
                "listVksClusters",
                "createSupervisorBackup",
                "getTask");

        private final List<Route> routes;
        private final Path log;
        private final String scenario;
        private final String namespace;
        private final String supervisor;
        private final String taskId;
        private final String session;
        private final String token;
        private final String resultMarker;
        private final List<VksNamespaceBackupClient.Cluster> clusters;
        private int taskReads;
        private int listReads;

        ContractExchange(
                Path contract,
                Path log,
                String scenario,
                String namespace,
                String supervisor,
                String taskId,
                String session,
                String token,
                String resultMarker,
                List<VksNamespaceBackupClient.Cluster> clusters)
                throws IOException {
            this.routes = loadRoutes(contract);
            this.log = log;
            this.scenario = scenario;
            this.namespace = namespace;
            this.supervisor = supervisor;
            this.taskId = taskId;
            this.session = session;
            this.token = token;
            this.resultMarker = resultMarker;
            this.clusters = List.copyOf(clusters);
        }

        @Override
        public VksNamespaceBackupClient.WireResponse send(
                String logicalOperation, HttpRequest request)
                throws InterruptedException {
            String path = request.uri().getRawPath();
            Route matched = null;
            List<String> captures = List.of();
            for (Route route : routes) {
                Matcher matcher = route.path().matcher(path);
                if (route.method().equals(request.method()) && matcher.matches()) {
                    matched = route;
                    List<String> values = new ArrayList<>();
                    for (int group = 1; group <= matcher.groupCount(); group++) {
                        values.add(URLDecoder.decode(
                                matcher.group(group), StandardCharsets.UTF_8));
                    }
                    captures = values;
                    break;
                }
            }
            byte[] body = request.bodyPublisher().isPresent()
                    ? collect(request.bodyPublisher().orElseThrow())
                    : new byte[0];
            appendLog(request, matched == null ? null : matched.name(), body);
            if (matched == null) {
                return response(404, "{\"error\":\"route not in contract\"}");
            }

            return switch (matched.name()) {
                case "getSupervisorNamespace" -> namespaceResponse(captures);
                case "listVksClusters" -> clusterResponse(captures);
                case "createSupervisorBackup" -> backupResponse(captures);
                case "getTask" -> taskResponse(captures);
                default -> throw new AssertionError("unknown contract route");
            };
        }

        private VksNamespaceBackupClient.WireResponse namespaceResponse(
                List<String> captures) {
            require(captures.equals(List.of(namespace)), "fallback namespace capture");
            String status = "namespace_not_ready".equals(scenario)
                    ? "ERROR"
                    : "RUNNING";
            return response(
                    200,
                    "{"
                            + "\"supervisor\":" + quote(supervisor) + ","
                            + "\"config_status\":" + quote(status) + ","
                            + "\"description\":\"runtime fixture\","
                            + "\"messages\":[],"
                            + "\"stats\":{\"cpu_used\":137,"
                            + "\"memory_used\":911,\"storage_used\":4099},"
                            + "\"access_list\":[],\"storage_specs\":[]"
                            + "}");
        }

        private VksNamespaceBackupClient.WireResponse clusterResponse(
                List<String> captures) {
            require(captures.equals(List.of(namespace)), "fallback cluster capture");
            int read = listReads++;
            List<VksNamespaceBackupClient.Cluster> responseItems =
                    new ArrayList<>(clusters);
            boolean initialReverse = Set.of(
                    "empty_comment", "inventory_changed", "result_value")
                    .contains(scenario);
            if ((read + (initialReverse ? 1 : 0)) % 2 == 1) {
                java.util.Collections.reverse(responseItems);
            }
            StringBuilder items = new StringBuilder("[");
            for (int index = 0; index < responseItems.size(); index++) {
                if (index > 0) {
                    items.append(',');
                }
                VksNamespaceBackupClient.Cluster cluster = responseItems.get(index);
                String name = cluster.name();
                if ("malformed_cluster".equals(scenario) && read == 0 && index == 1) {
                    name = responseItems.get(0).name();
                }
                String version = cluster.topologyVersion();
                if ("inventory_changed".equals(scenario)
                        && read >= 1
                        && cluster.name().equals(clusters.get(1).name())) {
                    version += "-changed";
                }
                items.append("{\"apiVersion\":"
                                + "\"cluster.x-k8s.io/v1beta2\",")
                        .append("\"kind\":\"Cluster\",")
                        .append("\"metadata\":{\"name\":")
                        .append(quote(name))
                        .append(",\"namespace\":")
                        .append(quote(namespace))
                        .append("},\"spec\":{\"topology\":{\"version\":")
                        .append(quote(version))
                        .append("}}}");
            }
            items.append(']');
            return response(
                    200,
                    "{\"apiVersion\":\"cluster.x-k8s.io/v1beta2\","
                            + "\"kind\":\"ClusterList\","
                            + "\"metadata\":{\"resourceVersion\":"
                            + quote(Integer.toString(700 + read))
                            + "},\"items\":" + items + "}");
        }

        private VksNamespaceBackupClient.WireResponse backupResponse(
                List<String> captures) {
            require(captures.equals(List.of(supervisor)), "fallback supervisor capture");
            if ("api_error".equals(scenario)) {
                return response(
                        503,
                        "{\"error\":"
                                + quote(session + " " + token + " runtime-secret-body")
                                + "}");
            }
            return response(200, quote(taskId));
        }

        private VksNamespaceBackupClient.WireResponse taskResponse(
                List<String> captures) {
            require(captures.equals(List.of(taskId)), "fallback task capture");
            String status = nextTaskStatus();
            return response(
                    200,
                    "{"
                            + "\"description\":{\"id\":\"runtime.backup\","
                            + "\"default_message\":\"Supervisor backup\","
                            + "\"args\":[]},"
                            + "\"service\":\"com.vmware.vcenter."
                            + "namespace_management\","
                            + "\"operation\":\"backup\","
                            + "\"status\":" + quote(status) + ","
                            + "\"cancelable\":false"
                            + ("result_value".equals(scenario)
                                    ? ",\"result\":{\"ticket\":"
                                            + quote(resultMarker)
                                            + ",\"parts\":[\"metadata\",2]}"
                                    : "")
                            + ("FAILED".equals(status)
                                    ? ",\"error\":{\"secret\":"
                                            + quote(session + token)
                                            + "}"
                                    : "")
                            + "}");
        }

        private String nextTaskStatus() {
            int read = taskReads++;
            if ("happy".equals(scenario)) {
                return switch (Math.min(read, 3)) {
                    case 0 -> "PENDING";
                    case 1 -> "RUNNING";
                    case 2 -> "BLOCKED";
                    default -> "SUCCEEDED";
                };
            }
            if ("task_failed".equals(scenario)) {
                return read == 0 ? "RUNNING" : "FAILED";
            }
            if ("poll_timeout".equals(scenario)) {
                return "RUNNING";
            }
            if ("malformed_task".equals(scenario)) {
                return "MYSTERY";
            }
            return "SUCCEEDED";
        }

        private static byte[] collect(HttpRequest.BodyPublisher publisher)
                throws InterruptedException {
            BodyCollector collector = new BodyCollector();
            publisher.subscribe(collector);
            return collector.finish();
        }

        private void appendLog(HttpRequest request, String operation, byte[] body) {
            StringBuilder headers = new StringBuilder("[");
            boolean first = true;
            for (Map.Entry<String, List<String>> entry
                    : request.headers().map().entrySet()) {
                for (String value : entry.getValue()) {
                    if (!first) {
                        headers.append(',');
                    }
                    first = false;
                    headers.append('[')
                            .append(quote(entry.getKey()))
                            .append(',')
                            .append(quote(value))
                            .append(']');
                }
            }
            headers.append(']');
            String rawTarget = request.uri().getRawPath();
            if (request.uri().getRawQuery() != null) {
                rawTarget += "?" + request.uri().getRawQuery();
            }
            String line = "{"
                    + "\"method\":" + quote(request.method()) + ","
                    + "\"rawTarget\":" + quote(rawTarget) + ","
                    + "\"path\":" + quote(request.uri().getRawPath()) + ","
                    + "\"operation\":"
                    + (operation == null ? "null" : quote(operation))
                    + ",\"headers\":" + headers + ","
                    + "\"bodyBase64\":"
                    + quote(Base64.getEncoder().encodeToString(body))
                    + "}\n";
            try {
                Files.writeString(
                        log,
                        line,
                        StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.APPEND);
            } catch (IOException error) {
                throw new AssertionError("could not write fallback request log");
            }
        }

        private static List<Route> loadRoutes(Path contract) throws IOException {
            String text = Files.readString(contract, StandardCharsets.UTF_8);
            Pattern operation = Pattern.compile(
                    "\\\"contractName\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                            + ".*?\\\"method\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                            + ".*?\\\"pathTemplate\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"",
                    Pattern.DOTALL);
            Matcher matcher = operation.matcher(text);
            List<Route> routes = new ArrayList<>();
            Set<String> names = new HashSet<>();
            while (matcher.find()) {
                names.add(matcher.group(1));
                routes.add(new Route(
                        matcher.group(1),
                        matcher.group(2),
                        compileTemplate(matcher.group(3))));
            }
            require(
                    names.equals(EXPECTED_NAMES) && routes.size() == 4,
                    "fallback contract operation set");
            return routes;
        }

        private static Pattern compileTemplate(String template) {
            Matcher placeholders = Pattern.compile(
                    "\\{[A-Za-z_][A-Za-z0-9_]*\\}").matcher(template);
            int cursor = 0;
            StringBuilder expression = new StringBuilder("^");
            while (placeholders.find()) {
                expression.append(Pattern.quote(
                        template.substring(cursor, placeholders.start())));
                expression.append("([^/]+)");
                cursor = placeholders.end();
            }
            expression.append(Pattern.quote(template.substring(cursor))).append('$');
            return Pattern.compile(expression.toString());
        }

        private static VksNamespaceBackupClient.WireResponse response(
                int status, String body) {
            HttpHeaders headers = HttpHeaders.of(
                    Map.of("Content-Type", List.of("application/json")),
                    (name, value) -> true);
            return new VksNamespaceBackupClient.WireResponse(
                    status, headers, body.getBytes(StandardCharsets.UTF_8));
        }
    }

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 17 && args.length != 20) {
            throw new AssertionError("unexpected harness argument count");
        }
        String scenario = args[0];
        URI endpoint = URI.create(args[1]);
        String session = args[2];
        String token = args[3];
        String namespace = args[4];
        String supervisor = args[5];
        String taskId = args[6];
        String comment = "<null>".equals(args[7]) ? null : args[7];
        String resultMarker = args[8];
        int expectedPolls = Integer.parseInt(args[9]);
        int maxPolls = Integer.parseInt(args[10]);
        List<VksNamespaceBackupClient.Cluster> expected =
                parseClusters(args, 11, 17);

        assertEquals(
                "Vcenter.Namespaces.Instances_getV2",
                VksNamespaceBackupClient.GET_NAMESPACE_OPERATION,
                "namespace operation constant");
        assertEquals(
                "cluster.x-k8s.io/v1beta2:namespaced-clusters:list",
                VksNamespaceBackupClient.LIST_CLUSTERS_OPERATION,
                "Kubernetes operation constant");
        assertEquals(
                "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create",
                VksNamespaceBackupClient.CREATE_BACKUP_OPERATION,
                "backup operation constant");
        assertEquals(
                "Cis.Tasks_get",
                VksNamespaceBackupClient.GET_TASK_OPERATION,
                "task operation constant");

        if ("validation".equals(scenario)) {
            runValidation(endpoint, session, token);
            System.out.println("TEST_MAIN_OK " + scenario);
            return;
        }

        VksNamespaceBackupClient.Config config =
                new VksNamespaceBackupClient.Config(
                    endpoint,
                    endpoint,
                    session,
                    token,
                    Duration.ofSeconds(3),
                    Duration.ZERO,
                    maxPolls);
        VksNamespaceBackupClient client;
        if (args.length == 20) {
            require("in-memory".equals(args[17]), "fallback marker");
            client = new VksNamespaceBackupClient(
                    config,
                    new ContractExchange(
                            Path.of(args[19]),
                            Path.of(args[18]),
                            scenario,
                            namespace,
                            supervisor,
                            taskId,
                            session,
                            token,
                            resultMarker,
                            expected));
        } else {
            client = new VksNamespaceBackupClient(config);
        }
        VksNamespaceBackupClient.BackupRequest request =
                new VksNamespaceBackupClient.BackupRequest(namespace, comment);

        switch (scenario) {
            case "happy", "empty_comment", "result_value" -> {
                VksNamespaceBackupClient.Result result =
                        client.backupNamespace(request);
                assertEquals(namespace, result.namespace(), "result namespace");
                assertEquals(supervisor, result.supervisor(), "result supervisor");
                assertEquals(expected, result.clusters(), "sorted cluster output");
                assertSorted(result.clusters());
                assertUnmodifiable(result.clusters());
                VksNamespaceBackupClient.BackupMetadata backup = result.backup();
                assertEquals(
                        VksNamespaceBackupClient.CREATE_BACKUP_OPERATION,
                        backup.createOperation(),
                        "create metadata operation");
                assertEquals(
                        VksNamespaceBackupClient.GET_TASK_OPERATION,
                        backup.taskOperation(),
                        "task metadata operation");
                assertEquals(taskId, backup.taskId(), "task metadata id");
                assertEquals("SUCCEEDED", backup.status(), "terminal status");
                assertEquals(expectedPolls, backup.polls(), "poll count");
                if ("result_value".equals(scenario)) {
                    assertResultValue(backup.result(), resultMarker);
                } else {
                    assertEquals(null, backup.result(), "absent task result");
                }
            }
            case "task_failed" -> {
                VksNamespaceBackupClient.TaskFailedException error = expect(
                        VksNamespaceBackupClient.TaskFailedException.class,
                        () -> client.backupNamespace(request));
                assertEquals(taskId, error.taskId(), "failed task id");
                assertEquals(expectedPolls, error.polls(), "failed task polls");
                assertRedacted(error, session, token);
            }
            case "poll_timeout" -> {
                VksNamespaceBackupClient.PollTimeoutException error = expect(
                        VksNamespaceBackupClient.PollTimeoutException.class,
                        () -> client.backupNamespace(request));
                assertEquals(taskId, error.taskId(), "timed out task id");
                assertEquals(maxPolls, error.polls(), "timeout poll count");
                assertRedacted(error, session, token);
            }
            case "namespace_not_ready" -> {
                VksNamespaceBackupClient.NamespaceNotReadyException error = expect(
                        VksNamespaceBackupClient.NamespaceNotReadyException.class,
                        () -> client.backupNamespace(request));
                assertEquals("ERROR", error.status(), "namespace status");
                assertRedacted(error, session, token);
            }
            case "inventory_changed", "malformed_cluster" -> {
                VksNamespaceBackupClient.ProtocolException error = expect(
                        VksNamespaceBackupClient.ProtocolException.class,
                        () -> client.backupNamespace(request));
                assertEquals(
                        VksNamespaceBackupClient.LIST_CLUSTERS_OPERATION,
                        error.operation(),
                        "cluster protocol operation");
                assertRedacted(error, session, token);
            }
            case "malformed_task" -> {
                VksNamespaceBackupClient.ProtocolException error = expect(
                        VksNamespaceBackupClient.ProtocolException.class,
                        () -> client.backupNamespace(request));
                assertEquals(
                        VksNamespaceBackupClient.GET_TASK_OPERATION,
                        error.operation(),
                        "task protocol operation");
                assertRedacted(error, session, token);
            }
            case "api_error" -> {
                VksNamespaceBackupClient.ApiException error = expect(
                        VksNamespaceBackupClient.ApiException.class,
                        () -> client.backupNamespace(request));
                assertEquals(
                        VksNamespaceBackupClient.CREATE_BACKUP_OPERATION,
                        error.operation(),
                        "API error operation");
                assertEquals(Integer.valueOf(503), error.statusCode(), "API status");
                assertRedacted(error, session, token);
                require(
                        !error.getMessage().contains("runtime-secret-body"),
                        "response body leaked");
            }
            default -> throw new AssertionError("unknown scenario: " + scenario);
        }

        System.out.println("TEST_MAIN_OK " + scenario);
    }

    private static List<VksNamespaceBackupClient.Cluster> parseClusters(
            String[] args, int offset, int end) {
        require((end - offset) % 2 == 0, "cluster args must be pairs");
        List<VksNamespaceBackupClient.Cluster> result = new ArrayList<>();
        for (int index = offset; index < end; index += 2) {
            result.add(new VksNamespaceBackupClient.Cluster(
                    args[index], args[index + 1]));
        }
        result.sort((left, right) -> left.name().compareTo(right.name()));
        return List.copyOf(result);
    }

    private static void assertSorted(
            List<VksNamespaceBackupClient.Cluster> clusters) {
        for (int index = 1; index < clusters.size(); index++) {
            require(
                    clusters.get(index - 1).name()
                            .compareTo(clusters.get(index).name()) < 0,
                    "cluster output is not strictly name sorted");
        }
    }

    private static void assertUnmodifiable(
            List<VksNamespaceBackupClient.Cluster> clusters) {
        expect(
                UnsupportedOperationException.class,
                () -> clusters.add(new VksNamespaceBackupClient.Cluster("x", "y")));
    }

    @SuppressWarnings("unchecked")
    private static void assertResultValue(Object value, String marker) {
        require(value instanceof Map<?, ?>, "terminal result must be an object");
        Map<String, Object> result = (Map<String, Object>) value;
        assertEquals(marker, result.get("ticket"), "terminal result marker");
        require(result.get("parts") instanceof List<?>, "terminal result list");
        expect(
                UnsupportedOperationException.class,
                () -> result.put("mutated", Boolean.TRUE));
        List<Object> parts = (List<Object>) result.get("parts");
        expect(UnsupportedOperationException.class, () -> parts.add("mutated"));
    }

    private static void runValidation(
            URI endpoint, String session, String token) {
        expect(
                IllegalArgumentException.class,
                () -> new VksNamespaceBackupClient(
                        new VksNamespaceBackupClient.Config(
                                URI.create(endpoint + "/api"),
                                endpoint,
                                session,
                                token,
                                Duration.ofSeconds(1),
                                Duration.ZERO,
                                1)));
        expect(
                IllegalArgumentException.class,
                () -> new VksNamespaceBackupClient(
                        new VksNamespaceBackupClient.Config(
                                endpoint,
                                endpoint,
                                session + "\r\ninjected",
                                token,
                                Duration.ofSeconds(1),
                                Duration.ZERO,
                                1)));
        expect(
                IllegalArgumentException.class,
                () -> new VksNamespaceBackupClient(
                        new VksNamespaceBackupClient.Config(
                                endpoint,
                                endpoint,
                                session,
                                token,
                                Duration.ZERO,
                                Duration.ZERO,
                                1)));

        VksNamespaceBackupClient client = new VksNamespaceBackupClient(
                new VksNamespaceBackupClient.Config(
                        endpoint,
                        endpoint,
                        session,
                        token,
                        Duration.ofMillis(250),
                        Duration.ZERO,
                        1));
        expect(
                IllegalArgumentException.class,
                () -> client.backupNamespace(
                        new VksNamespaceBackupClient.BackupRequest(" \t", null)));
    }

    private static void assertRedacted(
            Throwable error, String session, String token) {
        String rendered = error.toString();
        require(!rendered.contains(session), "vCenter credential leaked");
        require(!rendered.contains(token), "Kubernetes credential leaked");
    }

    private static <T extends Throwable> T expect(
            Class<T> type, ThrowingRunnable runnable) {
        try {
            runnable.run();
        } catch (Throwable error) {
            if (type.isInstance(error)) {
                return type.cast(error);
            }
            throw new AssertionError(
                    "expected " + type.getName() + " but received " + error, error);
        }
        throw new AssertionError("expected " + type.getName());
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(
                    message + ": expected=" + expected + " actual=" + actual);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static String quote(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char item = value.charAt(index);
            switch (item) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (item < 0x20) {
                        result.append(String.format("\\u%04X", (int) item));
                    } else {
                        result.append(item);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
