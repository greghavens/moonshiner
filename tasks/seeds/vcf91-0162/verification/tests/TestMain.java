import java.io.ByteArrayOutputStream;
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
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Flow;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class TestMain {
    private record Route(String name, String method, Pattern path) {
    }

    private record Snapshot(
            int createCount,
            int oldNamespaceCount,
            int newNamespaceCount,
            int clusterGetCount,
            int deleteCount,
            boolean deleteBeforeDrain,
            boolean deletedOldSession) {
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
            if (!done.await(2, TimeUnit.SECONDS) || error != null) {
                throw new AssertionError("body publisher did not complete");
            }
            return output.toByteArray();
        }
    }

    private static final class ContractExchange
            implements VcfVksSessionRotationClient.Exchange {
        private static final Set<String> EXPECTED_NAMES = Set.of(
                "createVcenterSession",
                "listSupervisorNamespaces",
                "getVksCluster",
                "deleteVcenterSession");
        private static final Map<String, String> EXPECTED_OPERATIONS = Map.of(
                "createVcenterSession",
                VcfVksSessionRotationClient.CREATE_SESSION_OPERATION,
                "listSupervisorNamespaces",
                VcfVksSessionRotationClient.LIST_NAMESPACES_OPERATION,
                "getVksCluster",
                VcfVksSessionRotationClient.GET_CLUSTER_OPERATION,
                "deleteVcenterSession",
                VcfVksSessionRotationClient.DELETE_SESSION_OPERATION);

        private final List<Route> routes;
        private final Path log;
        private final String oldSession;
        private final String newSession;
        private final String expectedBasic;
        private final String kubernetesToken;
        private final String namespace;
        private final String clusterName;
        private final String topologyVersion;
        private final CountDownLatch newClusterSeen = new CountDownLatch(1);
        private final Object stateLock = new Object();
        private final Object logLock = new Object();
        private int sequence;
        private int createCount;
        private int oldNamespaceCount;
        private int newNamespaceCount;
        private int clusterGetCount;
        private int deleteCount;
        private boolean oldNamespaceCompleted;
        private boolean deleteBeforeDrain;
        private boolean deletedOldSession;

        ContractExchange(
                Path contract,
                Path log,
                String oldSession,
                String newSession,
                String expectedBasic,
                String kubernetesToken,
                String namespace,
                String clusterName,
                String topologyVersion) throws Exception {
            this.routes = loadRoutes(contract);
            this.log = log;
            this.oldSession = oldSession;
            this.newSession = newSession;
            this.expectedBasic = expectedBasic;
            this.kubernetesToken = kubernetesToken;
            this.namespace = namespace;
            this.clusterName = clusterName;
            this.topologyVersion = topologyVersion;
        }

        @Override
        public VcfVksSessionRotationClient.WireResponse send(
                String logicalOperation,
                HttpRequest request) throws InterruptedException {
            String path = request.uri().getRawPath();
            Route matched = null;
            List<String> captures = List.of();
            for (Route route : routes) {
                Matcher matcher = route.path().matcher(path);
                if (route.method().equals(request.method())
                        && matcher.matches()) {
                    matched = route;
                    List<String> values = new ArrayList<>();
                    for (int group = 1;
                            group <= matcher.groupCount();
                            group++) {
                        values.add(URLDecoder.decode(
                                matcher.group(group),
                                StandardCharsets.UTF_8));
                    }
                    captures = values;
                    break;
                }
            }

            byte[] body = request.bodyPublisher().isPresent()
                    ? collect(request.bodyPublisher().orElseThrow())
                    : new byte[0];
            appendLog(
                    request,
                    matched == null ? null : matched.name(),
                    body);
            if (matched == null) {
                return response(
                        404, "{\"error\":\"operation not in contract\"}");
            }
            require(
                    EXPECTED_OPERATIONS.get(matched.name())
                            .equals(logicalOperation),
                    "logical operation differs from contract route");
            if (request.uri().getRawQuery() != null || body.length != 0) {
                return response(400, "{\"error\":\"wire shape rejected\"}");
            }

            if ("createVcenterSession".equals(matched.name())) {
                if (!request.headers().firstValue("Authorization")
                        .orElse("")
                        .equals(expectedBasic)) {
                    return response(
                            401, "{\"error\":\"authentication rejected\"}");
                }
                synchronized (stateLock) {
                    createCount++;
                }
                return response(201, jsonString(newSession));
            }

            if ("listSupervisorNamespaces".equals(matched.name())) {
                String session = request.headers()
                        .firstValue("vmware-api-session-id")
                        .orElse("");
                if (session.equals(oldSession)) {
                    synchronized (stateLock) {
                        oldNamespaceCount++;
                    }
                    if (!newClusterSeen.await(8, TimeUnit.SECONDS)) {
                        return response(
                                503, "{\"error\":\"handoff stalled\"}");
                    }
                    synchronized (stateLock) {
                        oldNamespaceCompleted = true;
                    }
                    return namespaceResponse(request.uri().getRawAuthority());
                }
                if (session.equals(newSession)) {
                    synchronized (stateLock) {
                        newNamespaceCount++;
                    }
                    return namespaceResponse(request.uri().getRawAuthority());
                }
                return response(
                        401, "{\"error\":\"authentication rejected\"}");
            }

            if ("getVksCluster".equals(matched.name())) {
                if (!request.headers().firstValue("Authorization")
                                .orElse("")
                                .equals("Bearer " + kubernetesToken)
                        || !captures.equals(List.of(namespace, clusterName))) {
                    return response(
                            401, "{\"error\":\"authentication rejected\"}");
                }
                synchronized (stateLock) {
                    clusterGetCount++;
                }
                VcfVksSessionRotationClient.WireResponse result = response(
                        200,
                        "{"
                                + "\"apiVersion\":"
                                + jsonString("cluster.x-k8s.io/v1beta2")
                                + ",\"kind\":\"Cluster\","
                                + "\"metadata\":{\"namespace\":"
                                + jsonString(namespace)
                                + ",\"name\":"
                                + jsonString(clusterName)
                                + "},\"spec\":{\"topology\":{\"version\":"
                                + jsonString(topologyVersion)
                                + "}}}");
                newClusterSeen.countDown();
                return result;
            }

            String session = request.headers()
                    .firstValue("vmware-api-session-id")
                    .orElse("");
            boolean drained;
            synchronized (stateLock) {
                deleteCount++;
                drained = oldNamespaceCompleted && clusterGetCount == 2;
                if (session.equals(oldSession) && drained) {
                    deletedOldSession = true;
                } else {
                    deleteBeforeDrain = true;
                }
            }
            if (!session.equals(oldSession)) {
                return response(
                        401, "{\"error\":\"authentication rejected\"}");
            }
            if (!drained) {
                return response(
                        409, "{\"error\":\"old session is leased\"}");
            }
            return response(204, "");
        }

        Snapshot snapshot() {
            synchronized (stateLock) {
                return new Snapshot(
                        createCount,
                        oldNamespaceCount,
                        newNamespaceCount,
                        clusterGetCount,
                        deleteCount,
                        deleteBeforeDrain,
                        deletedOldSession);
            }
        }

        private VcfVksSessionRotationClient.WireResponse namespaceResponse(
                String authority) {
            return response(
                    200,
                    "[{\"namespace\":"
                            + jsonString(namespace)
                            + ",\"master_host\":"
                            + jsonString(authority)
                            + "}]");
        }

        private void appendLog(
                HttpRequest request, String operation, byte[] body) {
            String rawTarget = request.uri().getRawPath();
            if (request.uri().getRawQuery() != null) {
                rawTarget += "?" + request.uri().getRawQuery();
            }
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
                            .append(jsonString(entry.getKey().toLowerCase()))
                            .append(',')
                            .append(jsonString(value))
                            .append(']');
                }
            }
            headers.append(']');
            synchronized (logLock) {
                sequence++;
                String record = "{"
                        + "\"method\":" + jsonString(request.method()) + ","
                        + "\"raw_target\":" + jsonString(rawTarget) + ","
                        + "\"path\":"
                        + jsonString(request.uri().getRawPath()) + ","
                        + "\"query\":"
                        + jsonString(request.uri().getRawQuery() == null
                                ? "" : request.uri().getRawQuery()) + ","
                        + "\"authority\":"
                        + jsonString(request.uri().getRawAuthority()) + ","
                        + "\"headers\":" + headers + ","
                        + "\"body_utf8\":"
                        + jsonString(new String(body, StandardCharsets.UTF_8))
                        + ",\"body_length\":" + body.length + ","
                        + "\"publisher_present\":"
                        + request.bodyPublisher().isPresent() + ","
                        + "\"publisher_length\":"
                        + request.bodyPublisher()
                                .map(HttpRequest.BodyPublisher::contentLength)
                                .orElse(-1L)
                        + ",\"operation\":"
                        + (operation == null
                                ? "null" : jsonString(operation)) + ","
                        + "\"sequence\":" + sequence
                        + "}\n";
                byte[] encoded = record.getBytes(StandardCharsets.UTF_8);
                try (FileChannel channel = FileChannel.open(
                        log,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.WRITE,
                        StandardOpenOption.APPEND)) {
                    channel.write(ByteBuffer.wrap(encoded));
                    channel.force(true);
                } catch (Exception error) {
                    throw new AssertionError("could not append request log");
                }
            }
        }

        private static List<Route> loadRoutes(Path contract)
                throws Exception {
            String text = Files.readString(contract, StandardCharsets.UTF_8);
            Pattern marker = Pattern.compile(
                    "\\\"contractName\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
            Matcher matches = marker.matcher(text);
            List<String> names = new ArrayList<>();
            List<Integer> starts = new ArrayList<>();
            while (matches.find()) {
                names.add(matches.group(1));
                starts.add(matches.start());
            }
            require(
                    names.size() == EXPECTED_NAMES.size()
                            && new HashSet<>(names).equals(EXPECTED_NAMES),
                    "fallback contract allow-list differs");
            List<Route> result = new ArrayList<>();
            for (int index = 0; index < names.size(); index++) {
                int end = index + 1 < names.size()
                        ? starts.get(index + 1) : text.length();
                String section = text.substring(starts.get(index), end);
                String method = extract(section, "method");
                String template = extract(section, "pathTemplate");
                result.add(new Route(
                        names.get(index), method, routePattern(template)));
            }
            return result;
        }

        private static String extract(String section, String key) {
            Matcher match = Pattern.compile(
                    "\\\"" + Pattern.quote(key)
                            + "\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"")
                    .matcher(section);
            if (!match.find()) {
                throw new AssertionError("contract field missing: " + key);
            }
            return match.group(1);
        }

        private static Pattern routePattern(String template) {
            StringBuilder pattern = new StringBuilder("^");
            Matcher fields = Pattern.compile(
                    "\\{[A-Za-z_][A-Za-z0-9_]*\\}").matcher(template);
            int cursor = 0;
            while (fields.find()) {
                pattern.append(Pattern.quote(
                        template.substring(cursor, fields.start())));
                pattern.append("([^/]+)");
                cursor = fields.end();
            }
            pattern.append(Pattern.quote(template.substring(cursor)))
                    .append('$');
            return Pattern.compile(pattern.toString());
        }

        private static byte[] collect(HttpRequest.BodyPublisher publisher)
                throws InterruptedException {
            BodyCollector collector = new BodyCollector();
            publisher.subscribe(collector);
            return collector.finish();
        }

        private static VcfVksSessionRotationClient.WireResponse response(
                int status, String body) {
            return new VcfVksSessionRotationClient.WireResponse(
                    status, body.getBytes(StandardCharsets.UTF_8));
        }
    }

    @FunctionalInterface
    private interface ThrowingCall {
        void run() throws Exception;
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (character < 0x20) {
                        result.append(String.format(
                                "\\u%04x", (int) character));
                    } else {
                        result.append(character);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static void expectValidation(ThrowingCall call, String label)
            throws Exception {
        try {
            call.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(label + " did not fail validation");
    }

    private static int logLines(Path log) throws Exception {
        if (!Files.exists(log)) {
            return 0;
        }
        try (var lines = Files.lines(log)) {
            return (int) lines.filter(line -> !line.isEmpty()).count();
        }
    }

    private static void waitForLogLines(Path log, int expected, long millis)
            throws Exception {
        long deadline = System.nanoTime()
                + TimeUnit.MILLISECONDS.toNanos(millis);
        while (System.nanoTime() < deadline) {
            if (logLines(log) >= expected) {
                return;
            }
            Thread.sleep(10);
        }
        throw new AssertionError("request log did not reach " + expected);
    }

    private static void waitForGeneration(
            VcfVksSessionRotationClient client,
            Future<?> rotation,
            long expected,
            long millis) throws Exception {
        long deadline = System.nanoTime()
                + TimeUnit.MILLISECONDS.toNanos(millis);
        while (System.nanoTime() < deadline) {
            if (client.sessionGeneration() == expected) {
                return;
            }
            if (rotation.isDone()) {
                await(rotation, 1);
                throw new AssertionError(
                        "rotation completed without publishing generation");
            }
            Thread.sleep(10);
        }
        throw new AssertionError("replacement generation was not published");
    }

    private static <T> T await(Future<T> future, long seconds)
            throws Exception {
        try {
            return future.get(seconds, TimeUnit.SECONDS);
        } catch (ExecutionException error) {
            Throwable cause = error.getCause();
            if (cause instanceof Exception exception) {
                throw exception;
            }
            if (cause instanceof Error fatal) {
                throw fatal;
            }
            throw new AssertionError("future failed");
        } catch (TimeoutException error) {
            throw new AssertionError("concurrent operation timed out");
        }
    }

    public static void main(String[] arguments) throws Exception {
        require(arguments.length == 11, "expected protected arguments");
        String mode = arguments[0];
        URI endpoint = URI.create(arguments[1]);
        String oldSession = arguments[2];
        String kubernetesToken = arguments[3];
        String username = arguments[4];
        String password = arguments[5];
        String namespace = arguments[6];
        String clusterName = arguments[7];
        String topologyVersion = arguments[8];
        Path requestLog = Path.of(arguments[9]);
        Path contractPath = Path.of(arguments[10]);

        ContractExchange fallback = null;
        VcfVksSessionRotationClient client;
        if ("fallback".equals(mode)) {
            String newSession = System.getenv("VCF_TEST_NEW_SESSION");
            String expectedBasic = System.getenv("VCF_TEST_BASIC");
            require(
                    newSession != null && expectedBasic != null,
                    "fallback secrets are missing");
            fallback = new ContractExchange(
                    contractPath,
                    requestLog,
                    oldSession,
                    newSession,
                    expectedBasic,
                    kubernetesToken,
                    namespace,
                    clusterName,
                    topologyVersion);
            client = new VcfVksSessionRotationClient(
                    endpoint,
                    oldSession,
                    kubernetesToken,
                    "http",
                    Duration.ofSeconds(5),
                    fallback);
        } else {
            require("loopback".equals(mode), "unknown transport mode");
            client = new VcfVksSessionRotationClient(
                    endpoint,
                    oldSession,
                    kubernetesToken,
                    "http",
                    Duration.ofSeconds(5));
        }
        require(client.sessionGeneration() == 0, "initial generation differs");
        require(logLines(requestLog) == 0, "construction performed traffic");

        expectValidation(
                () -> new VcfVksSessionRotationClient(
                        URI.create(endpoint + "/api"),
                        oldSession,
                        kubernetesToken,
                        "http",
                        Duration.ofSeconds(1)),
                "origin path");
        expectValidation(
                () -> new VcfVksSessionRotationClient(
                        endpoint,
                        "bad\nsession",
                        kubernetesToken,
                        "http",
                        Duration.ofSeconds(1)),
                "session newline");
        expectValidation(
                () -> new VcfVksSessionRotationClient(
                        endpoint,
                        oldSession,
                        kubernetesToken,
                        "ftp",
                        Duration.ofSeconds(1)),
                "Kubernetes scheme");
        expectValidation(
                () -> new VcfVksSessionRotationClient(
                        endpoint,
                        oldSession,
                        kubernetesToken,
                        "http",
                        Duration.ZERO),
                "zero timeout");
        expectValidation(
                () -> client.getCluster(namespace, " "),
                "blank Cluster name");
        expectValidation(
                () -> client.rotateVcenterSession("ambiguous:user", password),
                "Basic username colon");
        require(logLines(requestLog) == 0, "validation performed traffic");

        ExecutorService executor = Executors.newFixedThreadPool(3);
        try {
            Future<VcfVksSessionRotationClient.ClusterResult> oldLookup =
                    executor.submit(
                            () -> client.getCluster(namespace, clusterName));
            waitForLogLines(requestLog, 1, 3000);

            Future<Long> rotation = executor.submit(
                    () -> client.rotateVcenterSession(username, password));
            waitForGeneration(client, rotation, 1, 3000);
            require(
                    !rotation.isDone(),
                    "rotation returned before the old generation drained");

            Future<VcfVksSessionRotationClient.ClusterResult> newLookup =
                    executor.submit(
                            () -> client.getCluster(namespace, clusterName));
            VcfVksSessionRotationClient.ClusterResult newResult =
                    await(newLookup, 5);
            VcfVksSessionRotationClient.ClusterResult oldResult =
                    await(oldLookup, 5);
            long publishedGeneration = await(rotation, 5);

            VcfVksSessionRotationClient.ClusterResult expectedOld =
                    new VcfVksSessionRotationClient.ClusterResult(
                            VcfVksSessionRotationClient.LIST_NAMESPACES_OPERATION,
                            VcfVksSessionRotationClient.GET_CLUSTER_OPERATION,
                            namespace,
                            clusterName,
                            0,
                            topologyVersion);
            VcfVksSessionRotationClient.ClusterResult expectedNew =
                    new VcfVksSessionRotationClient.ClusterResult(
                            VcfVksSessionRotationClient.LIST_NAMESPACES_OPERATION,
                            VcfVksSessionRotationClient.GET_CLUSTER_OPERATION,
                            namespace,
                            clusterName,
                            1,
                            topologyVersion);
            require(expectedOld.equals(oldResult), "old lookup result differs");
            require(expectedNew.equals(newResult), "new lookup result differs");
            require(publishedGeneration == 1, "rotation result differs");
            require(client.sessionGeneration() == 1, "final generation differs");

            String rendered = oldResult + "\n" + newResult;
            for (String secret : new String[] {
                    oldSession, kubernetesToken, username, password
            }) {
                require(!rendered.contains(secret), "result disclosed a secret");
            }
        } finally {
            executor.shutdownNow();
            executor.awaitTermination(2, TimeUnit.SECONDS);
        }

        if (fallback != null) {
            require(
                    fallback.snapshot().equals(new Snapshot(
                            1, 1, 1, 2, 1, false, true)),
                    "fallback drain state differs");
        }
        System.out.println("TEST_MAIN_OK");
    }
}
