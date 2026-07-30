import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URLDecoder;
import java.net.URI;
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

public final class TestMain {
    private record Route(String name, String method, Pattern path) {
    }

    private static final class BodyCollector
            implements Flow.Subscriber<ByteBuffer> {
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
            implements VksSupervisorBackupClient.Exchange {
        private static final Set<String> EXPECTED_NAMES = Set.of(
                "getSupervisorNamespace",
                "getVksDeployment",
                "createSupervisorBackup",
                "getTask");

        private final List<Route> routes;
        private final Path log;
        private final String scenario;
        private final String supervisorNamespace;
        private final String supervisor;
        private final String workloadNamespace;
        private final String deployment;
        private final String taskId;
        private int taskReads;

        ContractExchange(
                Path contract,
                Path log,
                String scenario,
                String supervisorNamespace,
                String supervisor,
                String workloadNamespace,
                String deployment,
                String taskId) throws IOException {
            this.routes = loadRoutes(contract);
            this.log = log;
            this.scenario = scenario;
            this.supervisorNamespace = supervisorNamespace;
            this.supervisor = supervisor;
            this.workloadNamespace = workloadNamespace;
            this.deployment = deployment;
            this.taskId = taskId;
        }

        @Override
        public VksSupervisorBackupClient.WireResponse send(
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
                case "getSupervisorNamespace" -> {
                    check(captures.equals(List.of(supervisorNamespace)),
                            "in-memory namespace capture");
                    String status = "namespace_not_ready".equals(scenario)
                            ? "ERROR" : "RUNNING";
                    yield response(
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
                case "getVksDeployment" -> {
                    check(
                            captures.equals(List.of(workloadNamespace, deployment)),
                            "in-memory deployment capture");
                    boolean stable = !"unstable".equals(scenario);
                    int available = stable ? 3 : 2;
                    int unavailable = stable ? 0 : 1;
                    yield response(
                            200,
                            "{"
                                    + "\"apiVersion\":\"apps/v1\","
                                    + "\"kind\":\"Deployment\","
                                    + "\"metadata\":{\"name\":" + quote(deployment)
                                    + ",\"namespace\":" + quote(workloadNamespace)
                                    + ",\"generation\":17},"
                                    + "\"spec\":{\"replicas\":3},"
                                    + "\"status\":{\"observedGeneration\":17,"
                                    + "\"availableReplicas\":" + available + ","
                                    + "\"updatedReplicas\":3,"
                                    + "\"unavailableReplicas\":" + unavailable + "}"
                                    + "}");
                }
                case "createSupervisorBackup" -> {
                    check(captures.equals(List.of(supervisor)),
                            "in-memory supervisor capture");
                    yield response(200, quote(taskId));
                }
                case "getTask" -> {
                    check(captures.equals(List.of(taskId)),
                            "in-memory task capture");
                    String status = nextTaskStatus();
                    yield response(
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
                                    + ("FAILED".equals(status)
                                            ? ",\"error\":{\"fixture\":\"redacted\"}"
                                            : "")
                                    + "}");
                }
                default -> throw new AssertionError("unknown contract route");
            };
        }

        private String nextTaskStatus() {
            int read = taskReads++;
            if ("happy".equals(scenario)) {
                return switch (Math.min(read, 2)) {
                    case 0 -> "PENDING";
                    case 1 -> "RUNNING";
                    default -> "SUCCEEDED";
                };
            }
            if ("explicit_false".equals(scenario)) {
                return "SUCCEEDED";
            }
            if ("task_failed".equals(scenario)) {
                return read == 0 ? "RUNNING" : "FAILED";
            }
            if ("poll_limit".equals(scenario)) {
                return "RUNNING";
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
                    + "\"operation\":" + (operation == null ? "null" : quote(operation))
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
            check(names.equals(EXPECTED_NAMES), "fallback contract operation set");
            return routes;
        }

        private static Pattern compileTemplate(String template) {
            Matcher placeholders =
                    Pattern.compile("\\{[A-Za-z_][A-Za-z0-9_]*\\}").matcher(template);
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

        private static VksSupervisorBackupClient.WireResponse response(
                int status, String body) {
            return new VksSupervisorBackupClient.WireResponse(
                    status, body.getBytes(StandardCharsets.UTF_8));
        }
    }

    private static String quote(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char item = value.charAt(index);
            switch (item) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
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

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 11 && args.length != 15) {
            throw new IllegalArgumentException("unexpected harness argument count");
        }

        String scenario = args[0];
        URI endpoint = URI.create(args[1]);
        String session = args[2];
        String token = args[3];
        String supervisorNamespace = args[4];
        String supervisor = args[5];
        String workloadNamespace = args[6];
        String deployment = args[7];
        String commentValue = args[8];
        int expectedPolls = Integer.parseInt(args[9]);
        int maxPolls = Integer.parseInt(args[10]);

        String comment = "explicit_false".equals(scenario) ? null : commentValue;
        Boolean ignoreHealth = "explicit_false".equals(scenario) ? Boolean.FALSE : null;

        VksSupervisorBackupClient.Config config =
                new VksSupervisorBackupClient.Config(
                    endpoint,
                    endpoint,
                    session,
                    token,
                    Duration.ofSeconds(3),
                    Duration.ZERO,
                    maxPolls);
        VksSupervisorBackupClient client;
        if (args.length == 15) {
            check("in-memory".equals(args[11]), "fallback marker");
            client = new VksSupervisorBackupClient(
                    config,
                    new ContractExchange(
                            Path.of(args[14]),
                            Path.of(args[13]),
                            scenario,
                            supervisorNamespace,
                            supervisor,
                            workloadNamespace,
                            deployment,
                            args[12]));
        } else {
            client = new VksSupervisorBackupClient(config);
        }

        VksSupervisorBackupClient.BackupRequest request =
                new VksSupervisorBackupClient.BackupRequest(
                        supervisorNamespace,
                        supervisor,
                        workloadNamespace,
                        deployment,
                        comment,
                        ignoreHealth);

        switch (scenario) {
            case "happy", "explicit_false" -> {
                VksSupervisorBackupClient.BackupResult result =
                        client.backupWhenDeploymentStable(request);
                check("SUCCEEDED".equals(result.status()), "terminal status");
                check(result.polls() == expectedPolls, "poll count");
                check(supervisor.equals(result.supervisor()), "supervisor");
                check(deployment.equals(result.deployment()), "deployment");
                check(result.taskId() != null && !result.taskId().isBlank(), "task id");
            }
            case "task_failed" -> {
                try {
                    client.backupWhenDeploymentStable(request);
                    throw new AssertionError("expected TaskFailedException");
                } catch (VksSupervisorBackupClient.TaskFailedException expected) {
                    check(!expected.getMessage().contains(session), "session leaked");
                    check(!expected.getMessage().contains(token), "token leaked");
                }
            }
            case "unstable" -> {
                try {
                    client.backupWhenDeploymentStable(request);
                    throw new AssertionError("expected DeploymentNotStableException");
                } catch (VksSupervisorBackupClient.DeploymentNotStableException expected) {
                    // expected
                }
            }
            case "namespace_not_ready" -> {
                try {
                    client.backupWhenDeploymentStable(request);
                    throw new AssertionError("expected NamespaceNotReadyException");
                } catch (VksSupervisorBackupClient.NamespaceNotReadyException expected) {
                    // expected
                }
            }
            case "poll_limit" -> {
                try {
                    client.backupWhenDeploymentStable(request);
                    throw new AssertionError("expected PollLimitException");
                } catch (VksSupervisorBackupClient.PollLimitException expected) {
                    // expected
                }
            }
            default -> throw new IllegalArgumentException("unknown scenario");
        }

        System.out.println("TEST_MAIN_OK " + scenario);
    }
}
