import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URLDecoder;
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
import java.util.Properties;
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
            implements VksClusterProvisionClient.Exchange {
        private static final Set<String> EXPECTED_NAMES = Set.of(
                "getSupervisorNamespace", "createVksCluster");

        private final List<Route> routes;
        private final Path log;
        private final Properties values;

        ContractExchange(Path contract, Path log, Properties values)
                throws IOException {
            this.routes = loadRoutes(contract);
            this.log = log;
            this.values = values;
        }

        @Override
        public VksClusterProvisionClient.WireResponse send(
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
                case "createVksCluster" -> createResponse(captures);
                default -> throw new AssertionError("unknown contract route");
            };
        }

        private VksClusterProvisionClient.WireResponse namespaceResponse(
                List<String> captures) {
            check(
                    captures.equals(List.of(
                            required(values, "supervisorNamespace"))),
                    "fallback namespace capture");
            String scenario = required(values, "scenario");
            String status = scenario.equals("namespace_not_ready")
                    ? "ERROR" : "RUNNING";
            String supervisor = scenario.equals("supervisor_mismatch")
                    ? required(values, "differentSupervisor")
                    : required(values, "supervisor");
            String stats = scenario.equals("malformed_precheck")
                    ? "{\"cpu_used\":7,\"memory_used\":31}"
                    : "{\"cpu_used\":7,\"memory_used\":31,"
                            + "\"storage_used\":127}";
            return response(
                    200,
                    "{"
                            + "\"supervisor\":" + quote(supervisor) + ","
                            + "\"config_status\":" + quote(status) + ","
                            + "\"description\":\"runtime fixture\","
                            + "\"messages\":[],\"stats\":" + stats + ","
                            + "\"access_list\":[],\"storage_specs\":[]"
                            + "}");
        }

        private VksClusterProvisionClient.WireResponse createResponse(
                List<String> captures) {
            check(
                    captures.equals(List.of(
                            required(values, "supervisorNamespace"))),
                    "fallback create capture");
            String scenario = required(values, "scenario");
            if (scenario.equals("create_rejected")) {
                return response(
                        409,
                        "{\"message\":\"fixture conflict contains a secret\"}");
            }
            String name = scenario.equals("create_bad_identity")
                    ? required(values, "differentClusterName")
                    : required(values, "clusterName");
            return response(
                    201,
                    "{"
                            + "\"apiVersion\":\"cluster.x-k8s.io/v1beta2\","
                            + "\"kind\":\"Cluster\","
                            + "\"metadata\":{\"name\":" + quote(name)
                            + ",\"namespace\":"
                            + quote(required(values, "supervisorNamespace"))
                            + ",\"uid\":" + quote(required(values, "uid"))
                            + ",\"resourceVersion\":"
                            + quote(required(values, "resourceVersion"))
                            + "},\"status\":{\"phase\":"
                            + quote(required(values, "phase")) + "}}");
        }

        private static byte[] collect(HttpRequest.BodyPublisher publisher)
                throws InterruptedException {
            BodyCollector collector = new BodyCollector();
            publisher.subscribe(collector);
            return collector.finish();
        }

        private void appendLog(
                HttpRequest request, String operation, byte[] body) {
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
                    + "\"operation\":"
                    + (operation == null ? "null" : quote(operation)) + ","
                    + "\"headers\":" + headers + ","
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

        private static List<Route> loadRoutes(Path contract)
                throws IOException {
            String text = Files.readString(contract, StandardCharsets.UTF_8);
            Pattern operation = Pattern.compile(
                    "\\\"contractName\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                            + ".*?\\\"method\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                            + ".*?\\\"pathTemplate\\\"\\s*:\\s*"
                            + "\\\"([^\\\"]+)\\\"",
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
            expression.append(Pattern.quote(template.substring(cursor)))
                    .append('$');
            return Pattern.compile(expression.toString());
        }

        private static VksClusterProvisionClient.WireResponse response(
                int status, String body) {
            return new VksClusterProvisionClient.WireResponse(
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
                default -> result.append(item);
            }
        }
        return result.append('"').toString();
    }

    private static String required(Properties values, String key) {
        String value = values.getProperty(key);
        if (value == null || value.isEmpty()) {
            throw new AssertionError("missing fixture property: " + key);
        }
        return value;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static VksClusterProvisionClient.ClusterRequest request(
            Properties values, String scenario) {
        String classNamespace = null;
        Integer controlPlaneReplicas = null;
        String workerPoolName = null;
        Integer workerReplicas = null;
        String serviceDomain = null;
        if (scenario.equals("full")) {
            classNamespace = required(values, "classNamespace");
            controlPlaneReplicas =
                    Integer.valueOf(required(values, "controlPlaneReplicas"));
            workerPoolName = required(values, "workerPoolName");
            workerReplicas = Integer.valueOf(required(values, "workerReplicas"));
            serviceDomain = required(values, "serviceDomain");
        } else if (scenario.equals("invalid_request")) {
            workerPoolName = required(values, "workerPoolName");
        }
        return new VksClusterProvisionClient.ClusterRequest(
                required(values, "supervisorNamespace"),
                required(values, "supervisor"),
                required(values, "clusterName"),
                required(values, "clusterClass"),
                required(values, "kubernetesVersion"),
                required(values, "vmClass"),
                required(values, "storageClass"),
                classNamespace,
                controlPlaneReplicas,
                workerPoolName,
                workerReplicas,
                serviceDomain);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3 && args.length != 5) {
            throw new AssertionError(
                    "usage: TestMain <origin|fallback> <fixture> <scenario>"
                            + " [contract log]");
        }
        String origin = args[0];
        Properties values = new Properties();
        try (var input = Files.newInputStream(Path.of(args[1]))) {
            values.load(input);
        }
        String scenario = args[2];
        check(scenario.equals(required(values, "scenario")), "scenario mismatch");

        URI endpoint = URI.create(
                origin.equals("fallback") ? "http://127.0.0.1" : origin);
        VksClusterProvisionClient.Config config =
                new VksClusterProvisionClient.Config(
                        endpoint,
                        endpoint,
                        required(values, "vcenterSession"),
                        required(values, "bearerToken"),
                        Duration.ofSeconds(5));
        VksClusterProvisionClient client;
        if (origin.equals("fallback")) {
            check(args.length == 5, "fallback paths missing");
            client = new VksClusterProvisionClient(
                    config,
                    new ContractExchange(
                            Path.of(args[3]), Path.of(args[4]), values));
        } else {
            check(args.length == 3, "unexpected loopback arguments");
            client = new VksClusterProvisionClient(config);
        }

        if (scenario.equals("invalid_request")) {
            try {
                client.createIfNamespaceReady(request(values, scenario));
                throw new AssertionError("invalid request was accepted");
            } catch (IllegalArgumentException expected) {
                System.out.println("EXPECTED invalid_request");
                return;
            }
        }

        if (scenario.equals("namespace_not_ready")) {
            try {
                client.createIfNamespaceReady(request(values, scenario));
                throw new AssertionError("unready namespace was accepted");
            } catch (VksClusterProvisionClient.NamespaceNotReadyException expected) {
                System.out.println("EXPECTED namespace_not_ready");
                return;
            }
        }

        if (scenario.equals("supervisor_mismatch")
                || scenario.equals("malformed_precheck")
                || scenario.equals("create_bad_identity")) {
            try {
                client.createIfNamespaceReady(request(values, scenario));
                throw new AssertionError("invalid response was accepted");
            } catch (VksClusterProvisionClient.ProtocolException expected) {
                System.out.println("EXPECTED " + scenario);
                return;
            }
        }

        if (scenario.equals("create_rejected")) {
            try {
                client.createIfNamespaceReady(request(values, scenario));
                throw new AssertionError("rejected create was accepted");
            } catch (VksClusterProvisionClient.ApiException expected) {
                check(
                        expected.operation().equals(
                                "cluster.x-k8s.io/v1beta2:namespaced-clusters:create"),
                        "wrong rejected operation");
                check(expected.statusCode() == 409, "wrong rejected status");
                check(
                        !expected.getMessage().contains(required(values, "bearerToken")),
                        "bearer leaked in error");
                System.out.println("EXPECTED create_rejected");
                return;
            }
        }

        VksClusterProvisionClient.ProvisionedCluster result =
                client.createIfNamespaceReady(request(values, scenario));
        check(
                result.namespace().equals(required(values, "supervisorNamespace")),
                "wrong result namespace");
        check(
                result.name().equals(required(values, "clusterName")),
                "wrong result name");
        check(result.uid().equals(required(values, "uid")), "wrong result uid");
        check(
                result.resourceVersion().equals(required(values, "resourceVersion")),
                "wrong result resourceVersion");
        check(result.phase().equals(required(values, "phase")), "wrong result phase");
        System.out.println("OK " + scenario);
    }
}
