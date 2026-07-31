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
            implements VksFailureEvidenceClient.Exchange {
        private static final Set<String> EXPECTED_NAMES = Set.of(
                "listAuthorizedSupervisorNamespaces",
                "listPodWarningEvents",
                "readPreviousPodLog");

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
        public VksFailureEvidenceClient.WireResponse send(
                String logicalOperation, HttpRequest request)
                throws InterruptedException {
            String path = request.uri().getRawPath();
            Route matched = null;
            List<String> captures = List.of();
            for (Route route : routes) {
                Matcher matcher = route.path().matcher(path);
                if (route.method().equals(request.method())
                        && matcher.matches()) {
                    matched = route;
                    List<String> decoded = new ArrayList<>();
                    for (int group = 1;
                            group <= matcher.groupCount();
                            group++) {
                        decoded.add(URLDecoder.decode(
                                matcher.group(group), StandardCharsets.UTF_8));
                    }
                    captures = decoded;
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
            String expectedLogical = switch (matched.name()) {
                case "listAuthorizedSupervisorNamespaces" ->
                        VksFailureEvidenceClient.VCENTER_OPERATION;
                case "listPodWarningEvents" ->
                        VksFailureEvidenceClient.EVENTS_OPERATION;
                case "readPreviousPodLog" ->
                        VksFailureEvidenceClient.LOG_OPERATION;
                default -> throw new AssertionError("unknown route");
            };
            check(
                    logicalOperation.equals(expectedLogical),
                    "logical operation does not match contract route");
            return switch (matched.name()) {
                case "listAuthorizedSupervisorNamespaces" ->
                        namespaceResponse(captures);
                case "listPodWarningEvents" -> eventResponse(captures);
                case "readPreviousPodLog" -> logResponse(captures);
                default -> throw new AssertionError("unknown route");
            };
        }

        private VksFailureEvidenceClient.WireResponse namespaceResponse(
                List<String> captures) {
            check(captures.isEmpty(), "fallback namespace capture");
            if (required(values, "scenario").equals("vcenter_failure")) {
                return response(
                        503,
                        "{\"message\":"
                                + quote(required(values, "serverBodyMarker"))
                                + ",\"session\":"
                                + quote(required(values, "vcenterSession"))
                                + "}");
            }
            StringBuilder body = new StringBuilder("[{\"namespace\":")
                    .append(quote(required(
                            values, "otherSupervisorNamespace")))
                    .append(",\"master_host\":")
                    .append(quote(
                            "https://other-supervisor.example.test:6443"))
                    .append('}');
            if (!required(values, "scenario")
                    .equals("unauthorized_namespace")) {
                body.append(",{\"namespace\":")
                        .append(quote(required(
                                values, "supervisorNamespace")))
                        .append(",\"master_host\":")
                        .append(quote(required(
                                values, "supervisorMasterHost")))
                        .append('}');
            }
            return response(200, body.append(']').toString());
        }

        private VksFailureEvidenceClient.WireResponse eventResponse(
                List<String> captures) {
            check(
                    captures.equals(List.of(
                            required(values, "workloadNamespace"))),
                    "fallback event capture");
            if (required(values, "scenario").equals("bad_events")) {
                return response(
                        200,
                        eventList(
                                event(
                                        "BackOff",
                                        "wrong Pod identity",
                                        quote("not-an-integer"),
                                        required(values, "otherPodName"))));
            }
            String first = event(
                    "Unhealthy",
                    "Readiness probe failed for runtime fixture",
                    "2",
                    required(values, "podName"));
            if (Set.of("correlated", "event_only").contains(
                    required(values, "scenario"))) {
                return response(
                        200,
                        eventList(
                                first
                                        + ","
                                        + event(
                                                "BackOff",
                                                "Back-off restarting failed"
                                                        + " container "
                                                        + required(
                                                                values,
                                                                "containerName"),
                                                "7",
                                                required(
                                                        values, "podName"))));
            }
            return response(200, eventList(first));
        }

        private String eventList(String items) {
            return "{\"apiVersion\":\"v1\",\"kind\":\"EventList\","
                    + "\"metadata\":{\"resourceVersion\":\"19\"},"
                    + "\"items\":[" + items + "]}";
        }

        private String event(
                String reason,
                String message,
                String countJson,
                String pod) {
            return "{\"type\":\"Warning\",\"reason\":"
                    + quote(reason)
                    + ",\"message\":"
                    + quote(message)
                    + ",\"count\":"
                    + countJson
                    + ",\"involvedObject\":{\"kind\":\"Pod\","
                    + "\"namespace\":"
                    + quote(required(values, "workloadNamespace"))
                    + ",\"name\":"
                    + quote(pod)
                    + "}}";
        }

        private VksFailureEvidenceClient.WireResponse logResponse(
                List<String> captures) {
            check(
                    captures.equals(List.of(
                            required(values, "workloadNamespace"),
                            required(values, "podName"))),
                    "fallback log capture");
            String text;
            if (required(values, "scenario").equals("event_only")) {
                text = "2026-07-30T12:04:09Z java.net.ConnectException: "
                        + "Connection refused\n";
            } else {
                text = "2026-07-30T12:04:09Z "
                        + "java.net.UnknownHostException: "
                        + required(values, "upstreamHost")
                        + "\n2026-07-30T12:04:09Z"
                        + " at java.base/java.net.InetAddress.lookupAllHostAddr\n";
            }
            return new VksFailureEvidenceClient.WireResponse(
                    200, text.getBytes(StandardCharsets.UTF_8));
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
            check(
                    names.equals(EXPECTED_NAMES),
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
            expression.append(Pattern.quote(template.substring(cursor)))
                    .append('$');
            return Pattern.compile(expression.toString());
        }

        private static VksFailureEvidenceClient.WireResponse response(
                int status, String body) {
            return new VksFailureEvidenceClient.WireResponse(
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

    private static void checkRedacted(
            Throwable error, Properties values) {
        String text = String.valueOf(error);
        for (String key : List.of(
                "vcenterSession",
                "bearerToken",
                "serverBodyMarker")) {
            check(
                    !text.contains(required(values, key)),
                    "error disclosed " + key);
        }
    }

    private static VksFailureEvidenceClient newClient(
            String origin,
            Properties values,
            VksFailureEvidenceClient.Exchange exchange) {
        VksFailureEvidenceClient.Config config =
                new VksFailureEvidenceClient.Config(
                        URI.create(origin + "/api"),
                        URI.create(origin),
                        required(values, "vcenterSession"),
                        required(values, "bearerToken"),
                        Duration.ofSeconds(5));
        return exchange == null
                ? new VksFailureEvidenceClient(config)
                : new VksFailureEvidenceClient(config, exchange);
    }

    private static VksFailureEvidenceClient.Diagnosis diagnose(
            VksFailureEvidenceClient client, Properties values)
            throws InterruptedException {
        return client.diagnose(
                required(values, "supervisorNamespace"),
                required(values, "workloadNamespace"),
                required(values, "podName"),
                required(values, "containerName"),
                required(values, "upstreamHost"));
    }

    private static void verifyDiagnosis(
            String scenario,
            VksFailureEvidenceClient.Diagnosis result,
            Properties values) {
        check(
                result.supervisorNamespace().equals(
                        required(values, "supervisorNamespace")),
                "wrong Supervisor namespace");
        check(
                result.supervisorEndpoint().equals(
                        "https://" + required(values, "supervisorMasterHost")),
                "wrong normalized Supervisor endpoint");
        check(
                result.workloadNamespace().equals(
                        required(values, "workloadNamespace")),
                "wrong workload namespace");
        check(
                result.podName().equals(required(values, "podName")),
                "wrong Pod name");
        check(
                result.containerName().equals(
                        required(values, "containerName")),
                "wrong container name");
        VksFailureEvidenceClient.Cause expected =
                scenario.equals("correlated")
                        ? VksFailureEvidenceClient.Cause.UPSTREAM_DNS
                        : VksFailureEvidenceClient.Cause.INCONCLUSIVE;
        check(result.cause() == expected, "wrong correlated cause");
        int expectedEvents = scenario.equals("log_only") ? 1 : 2;
        check(
                result.warningEvents().size() == expectedEvents,
                "wrong event evidence count");
        check(
                result.warningEvents().get(0).reason().equals("Unhealthy"),
                "event service order changed");
        if (expectedEvents == 2) {
            check(
                    result.warningEvents().get(1).reason().equals("BackOff")
                            && result.warningEvents().get(1).count() == 7,
                    "BackOff evidence missing");
        }
        if (!scenario.equals("event_only")) {
            check(
                    result.previousContainerLog().contains(
                            "java.net.UnknownHostException: "
                                    + required(values, "upstreamHost")),
                    "previous log evidence missing");
        }
        try {
            result.warningEvents().add(
                    new VksFailureEvidenceClient.EventEvidence("x", "y", 1));
            throw new AssertionError("event evidence list is mutable");
        } catch (UnsupportedOperationException expectedFailure) {
            // Expected.
        }
    }

    private static void run(
            String scenario,
            String origin,
            Properties values,
            VksFailureEvidenceClient.Exchange exchange)
            throws Exception {
        VksFailureEvidenceClient client =
                newClient(origin, values, exchange);
        switch (scenario) {
            case "correlated", "event_only", "log_only" -> {
                verifyDiagnosis(scenario, diagnose(client, values), values);
            }
            case "unauthorized_namespace" -> {
                try {
                    diagnose(client, values);
                    throw new AssertionError("unauthorized namespace accepted");
                } catch (
                        VksFailureEvidenceClient.NamespaceNotAuthorizedException
                                expected) {
                    checkRedacted(expected, values);
                }
            }
            case "vcenter_failure" -> {
                try {
                    diagnose(client, values);
                    throw new AssertionError("vCenter failure accepted");
                } catch (VksFailureEvidenceClient.ApiException expected) {
                    check(
                            expected.operation().equals(
                                    VksFailureEvidenceClient.VCENTER_OPERATION)
                                    && expected.statusCode() == 503,
                            "wrong vCenter API failure");
                    checkRedacted(expected, values);
                }
            }
            case "bad_events" -> {
                try {
                    diagnose(client, values);
                    throw new AssertionError("malformed EventList accepted");
                } catch (VksFailureEvidenceClient.ProtocolException expected) {
                    check(
                            expected.operation().equals(
                                    VksFailureEvidenceClient.EVENTS_OPERATION),
                            "wrong malformed-success operation");
                    checkRedacted(expected, values);
                }
            }
            case "validation" -> {
                try {
                    client.diagnose(
                            required(values, "supervisorNamespace"),
                            required(values, "workloadNamespace"),
                            required(values, "podName"),
                            required(values, "containerName"),
                            " \n");
                    throw new AssertionError("invalid input accepted");
                } catch (IllegalArgumentException expected) {
                    checkRedacted(expected, values);
                }
            }
            default -> throw new AssertionError("unknown scenario");
        }
    }

    public static void main(String[] arguments) throws Exception {
        check(
                arguments.length == 3 || arguments.length == 5,
                "usage: TestMain scenario origin fixture [contract log]");
        Properties values = new Properties();
        try (var input = Files.newInputStream(Path.of(arguments[2]))) {
            values.load(input);
        }
        check(
                required(values, "scenario").equals(arguments[0]),
                "fixture scenario mismatch");
        VksFailureEvidenceClient.Exchange exchange = null;
        String origin = arguments[1];
        if (origin.equals("fallback")) {
            check(arguments.length == 5, "fallback paths missing");
            exchange = new ContractExchange(
                    Path.of(arguments[3]), Path.of(arguments[4]), values);
            origin = "http://127.0.0.1";
        } else {
            check(arguments.length == 3, "unexpected live-mode arguments");
        }
        run(arguments[0], origin, values, exchange);
        System.out.println("OK " + arguments[0]);
    }
}
