import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URLDecoder;
import java.net.URI;
import java.net.http.HttpRequest;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
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
            implements VksClusterApplyClient.Exchange {
        private static final Set<String> EXPECTED_NAMES = Set.of(
                "getSupervisorNamespace", "applyVksCluster");

        private final List<Route> routes;
        private final Path log;
        private final Path state;
        private final String scenario;
        private final String supervisor;
        private final String namespace;
        private final String cluster;
        private final String uid;
        private final String resourceVersion;
        private final long generation;
        private final Set<String> applied = new HashSet<>();
        private HttpRequest firstPatchRequest;
        private int patchAttempts;
        private int effects;

        ContractExchange(
                Path contract,
                Path log,
                Path state,
                String scenario,
                String supervisor,
                String namespace,
                String cluster,
                String uid,
                String resourceVersion,
                long generation) throws IOException {
            this.routes = loadRoutes(contract);
            this.log = log;
            this.state = state;
            this.scenario = scenario;
            this.supervisor = supervisor;
            this.namespace = namespace;
            this.cluster = cluster;
            this.uid = uid;
            this.resourceVersion = resourceVersion;
            this.generation = generation;
            writeState();
        }

        @Override
        public synchronized VksClusterApplyClient.WireResponse send(
                String logicalOperation, HttpRequest request)
                throws IOException, InterruptedException {
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

            if ("getSupervisorNamespace".equals(matched.name())) {
                check(captures.equals(List.of(namespace)), "fallback namespace");
                if ("redirect".equals(scenario)) {
                    return response(307, "");
                }
                String status = "namespace_not_ready".equals(scenario)
                        ? "ERROR" : "RUNNING";
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

            check(
                    captures.equals(List.of(namespace, cluster)),
                    "fallback Cluster capture");
            if (firstPatchRequest == null) {
                firstPatchRequest = request;
            } else {
                check(
                        firstPatchRequest == request,
                        "PATCH retry rebuilt the immutable request");
            }
            String fingerprint = request.uri().getRawQuery()
                    + "\0"
                    + Base64.getEncoder().encodeToString(body);
            patchAttempts++;
            if (applied.add(fingerprint)) {
                effects++;
            }
            writeState();
            if ("ambiguous".equals(scenario) && patchAttempts == 1) {
                throw new IOException("truncated fixture response");
            }
            return response(
                    200,
                    "{"
                            + "\"apiVersion\":\"cluster.x-k8s.io/v1beta2\","
                            + "\"kind\":\"Cluster\",\"metadata\":{\"name\":"
                            + quote(cluster)
                            + ",\"namespace\":" + quote(namespace)
                            + ",\"uid\":" + quote(uid)
                            + ",\"resourceVersion\":" + quote(resourceVersion)
                            + ",\"generation\":" + generation + "}}");
        }

        private static byte[] collect(HttpRequest.BodyPublisher publisher)
                throws InterruptedException {
            BodyCollector collector = new BodyCollector();
            publisher.subscribe(collector);
            return collector.finish();
        }

        private void appendLog(
                HttpRequest request, String operation, byte[] body)
                throws IOException {
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
            if (request.bodyPublisher().isPresent()) {
                if (!first) {
                    headers.append(',');
                }
                headers.append("[\"Content-Length\",")
                        .append(quote(Long.toString(
                                request.bodyPublisher()
                                        .orElseThrow()
                                        .contentLength())))
                        .append(']');
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
            Files.writeString(
                    log,
                    line,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.APPEND);
            try (FileChannel channel = FileChannel.open(
                    log, StandardOpenOption.WRITE)) {
                channel.force(true);
            }
        }

        private void writeState() throws IOException {
            Files.writeString(
                    state,
                    "{\"effects\":" + effects
                            + ",\"patchAttempts\":" + patchAttempts + "}",
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING);
            try (FileChannel channel = FileChannel.open(
                    state, StandardOpenOption.WRITE)) {
                channel.force(true);
            }
        }

        private static List<Route> loadRoutes(Path contract)
                throws IOException {
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
            check(names.equals(EXPECTED_NAMES), "fallback contract operations");
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
            expression.append(Pattern.quote(
                    template.substring(cursor))).append('$');
            return Pattern.compile(expression.toString());
        }

        private static VksClusterApplyClient.WireResponse response(
                int status, String body) {
            return new VksClusterApplyClient.WireResponse(
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

    private static void checkSanitized(
            RuntimeException error, String session, String token) {
        String message = String.valueOf(error.getMessage());
        check(!message.contains(session), "error exposed vCenter credential");
        check(!message.contains(token), "error exposed Kubernetes credential");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 16 && args.length != 20) {
            throw new IllegalArgumentException("unexpected harness argument count");
        }

        String scenario = args[0];
        URI endpoint = URI.create(args[1]);
        String session = args[2];
        String token = args[3];
        String supervisor = args[4];
        String namespace = args[5];
        String cluster = args[6];
        String fieldManager = args[7];
        String clusterClass = args[8];
        String version = args[9];
        String vmClass = args[10];
        String storageClass = args[11];
        String uid = args[12];
        String resourceVersion = args[13];
        long generation = Long.parseLong(args[14]);
        int expectedAttempts = Integer.parseInt(args[15]);

        ContractExchange fallback = null;
        if (args.length == 20) {
            check("in-memory".equals(args[16]), "fallback marker");
            fallback = new ContractExchange(
                    Path.of(args[19]),
                    Path.of(args[17]),
                    Path.of(args[18]),
                    scenario,
                    supervisor,
                    namespace,
                    cluster,
                    uid,
                    resourceVersion,
                    generation);
        }

        if ("invalid_config".equals(scenario)) {
            try {
                new VksClusterApplyClient(
                        new VksClusterApplyClient.Config(
                                URI.create(endpoint + "/not-an-origin"),
                                endpoint,
                                session,
                                token,
                                Duration.ofSeconds(3)),
                        fallback);
                throw new AssertionError("invalid config was accepted");
            } catch (IllegalArgumentException expected) {
                checkSanitized(expected, session, token);
                System.out.println("OK " + scenario);
                return;
            }
        }

        VksClusterApplyClient client = new VksClusterApplyClient(
                new VksClusterApplyClient.Config(
                        endpoint,
                        endpoint,
                        session,
                        token,
                        Duration.ofSeconds(3)),
                fallback);

        Integer workers = null;
        List<String> podCidrs = List.of();
        List<String> serviceCidrs = List.of();
        Boolean force = null;
        int controlPlaneReplicas = 3;
        if ("explicit_zero_false".equals(scenario)) {
            workers = Integer.valueOf(0);
            podCidrs = List.of("10.244.0.0/16", "fd00:10:244::/56");
            force = Boolean.FALSE;
        } else if ("ambiguous".equals(scenario)) {
            workers = Integer.valueOf(2);
            serviceCidrs = List.of("10.96.0.0/12");
            force = Boolean.TRUE;
        } else if ("invalid_request".equals(scenario)) {
            controlPlaneReplicas = 0;
        }

        VksClusterApplyClient.ApplyRequest request =
                new VksClusterApplyClient.ApplyRequest(
                        supervisor,
                        namespace,
                        cluster,
                        fieldManager,
                        clusterClass,
                        version,
                        vmClass,
                        storageClass,
                        controlPlaneReplicas,
                        workers,
                        podCidrs,
                        serviceCidrs,
                        force);

        if ("invalid_request".equals(scenario)) {
            try {
                client.apply(request);
                throw new AssertionError("invalid request was accepted");
            } catch (IllegalArgumentException expected) {
                checkSanitized(expected, session, token);
                System.out.println("OK " + scenario);
                return;
            }
        }

        if ("namespace_not_ready".equals(scenario)) {
            try {
                client.apply(request);
                throw new AssertionError("non-running namespace was accepted");
            } catch (VksClusterApplyClient.NamespaceNotReadyException expected) {
                check("ERROR".equals(expected.configStatus()), "wrong blocked status");
                checkSanitized(expected, session, token);
                System.out.println("OK " + scenario);
                return;
            }
        }

        if ("redirect".equals(scenario)) {
            try {
                client.apply(request);
                throw new AssertionError("redirect was followed or accepted");
            } catch (VksClusterApplyClient.ApiException expected) {
                check(
                        VksClusterApplyClient.NAMESPACE_OPERATION.equals(
                                expected.operation()),
                        "wrong redirect operation");
                check(expected.statusCode() == 307, "wrong redirect status");
                checkSanitized(expected, session, token);
                System.out.println("OK " + scenario);
                return;
            }
        }

        VksClusterApplyClient.ApplyResult result = client.apply(request);
        check(uid.equals(result.uid()), "wrong uid");
        check(
                resourceVersion.equals(result.resourceVersion()),
                "wrong resource version");
        check(generation == result.generation(), "wrong generation");
        check(
                expectedAttempts == result.patchAttempts(),
                "wrong PATCH attempt count");

        VksClusterApplyClient.ApiException probe =
                new VksClusterApplyClient.ApiException(
                        "probe", 409, new byte[] {1, 2, 3});
        byte[] copied = probe.responseBody();
        copied[0] = 9;
        check(probe.responseBody()[0] == 1, "response body was not defensive");

        System.out.println("OK " + scenario);
    }
}
