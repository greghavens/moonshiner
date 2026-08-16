import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/** Deterministic verifier and contract-pinned loopback VCF Automation fixture. */
public final class TestMain {
    private static final String CONTRACT_SHA256 = "bf3b4d6a639b6fd3aa1b5fd4a887d72b3ff3a1b5b5ac9e4db8c5d96b65a9bf8e";
    private static final String OPERATION_PATH = "/project-service/api/projects";
    private static final String API_VERSION = "2019-01-15";

    private record Datum(String id, String name) {}

    private record RequestRecord(
            String method,
            String path,
            Map<String, String> query,
            String accept,
            String authorization) {}

    private static final class ContractPinnedMock implements AutoCloseable {
        private final List<Datum> projects;
        private final HttpServer server;
        private final ExecutorService executor;
        private final AtomicInteger responseNumber = new AtomicInteger();
        private final List<RequestRecord> requestLog = new CopyOnWriteArrayList<>();

        ContractPinnedMock(List<Datum> projects) throws IOException {
            this.projects = List.copyOf(projects);
            server = HttpServer.create(
                    new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
            server.createContext(OPERATION_PATH, this::handleProjects);
            executor = Executors.newCachedThreadPool();
            server.setExecutor(executor);
            server.start();
        }

        String baseUrl() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        List<RequestRecord> requestLog() {
            return List.copyOf(requestLog);
        }

        private void handleProjects(HttpExchange exchange) throws IOException {
            URI uri = exchange.getRequestURI();
            Map<String, String> query = parseQuery(uri.getRawQuery());
            requestLog.add(new RequestRecord(
                    exchange.getRequestMethod(),
                    uri.getPath(),
                    query,
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Authorization")));

            if (!OPERATION_PATH.equals(uri.getPath())) {
                send(exchange, 404, "{\"error\":\"operation not found\"}");
                return;
            }
            if (!"GET".equals(exchange.getRequestMethod())) {
                send(exchange, 405, "{\"error\":\"method not allowed\"}");
                return;
            }

            int page;
            int size;
            try {
                page = Integer.parseInt(query.getOrDefault("page", "-1"));
                size = Integer.parseInt(query.getOrDefault("size", "-1"));
            } catch (NumberFormatException e) {
                send(exchange, 400, "{\"error\":\"bad paging parameters\"}");
                return;
            }
            if (page < 0 || size < 1 || !API_VERSION.equals(query.get("apiVersion"))) {
                send(exchange, 400, "{\"error\":\"contract query required\"}");
                return;
            }
            if (!"application/json".equals(exchange.getRequestHeaders().getFirst("Accept"))
                    || !"Bearer fixture-token".equals(
                            exchange.getRequestHeaders().getFirst("Authorization"))) {
                send(exchange, 401, "{\"error\":\"contract headers required\"}");
                return;
            }

            int from = Math.min(page * size, projects.size());
            int to = Math.min(from + size, projects.size());
            List<Datum> pageContent = new ArrayList<>(projects.subList(from, to));

            // Deliberately vary service order so callers cannot rely on it.
            if ((responseNumber.incrementAndGet() & 1) == 1) {
                Collections.reverse(pageContent);
            }

            int totalPages = Math.max(1, (projects.size() + size - 1) / size);
            boolean last = page >= totalPages - 1;
            StringBuilder body = new StringBuilder();
            body.append("{\"totalElements\":").append(projects.size())
                    .append(",\"totalPages\":").append(totalPages)
                    .append(",\"pageable\":{\"pageNumber\":").append(page)
                    .append(",\"pageSize\":").append(size)
                    .append(",\"paged\":true},\"size\":").append(size)
                    .append(",\"content\":[");
            for (int i = 0; i < pageContent.size(); i++) {
                if (i > 0) {
                    body.append(',');
                }
                Datum datum = pageContent.get(i);
                body.append("{\"description\":\"ignored\",\"name\":\"")
                        .append(jsonEscape(datum.name()))
                        .append("\",\"metadata\":{\"fixture\":true,\"weight\":1.25},\"id\":\"")
                        .append(jsonEscape(datum.id())).append("\"}");
            }
            body.append("],\"number\":").append(page)
                    .append(",\"first\":").append(page == 0)
                    .append(",\"last\":").append(last)
                    .append(",\"numberOfElements\":").append(pageContent.size())
                    .append(",\"empty\":").append(pageContent.isEmpty())
                    .append(",\"unused\":null}");
            send(exchange, 200, body.toString());
        }

        @Override
        public void close() {
            server.stop(0);
            executor.shutdownNow();
        }

        private static void send(HttpExchange exchange, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (var output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        }
    }

    public static void main(String[] args) throws Exception {
        verifyContractDigest();

        List<Datum> firstFixture = List.of(
                new Datum("project-30", "Zulu"),
                new Datum("project-20", "Alpha"),
                new Datum("project-10", "Alpha"),
                new Datum("project-40", "Bravo"),
                new Datum("project-50", "Echo"),
                new Datum("project-60", "Delta"));
        List<AutomationClient.Project> firstExpected = List.of(
                new AutomationClient.Project("project-10", "Alpha"),
                new AutomationClient.Project("project-20", "Alpha"),
                new AutomationClient.Project("project-40", "Bravo"),
                new AutomationClient.Project("project-60", "Delta"),
                new AutomationClient.Project("project-50", "Echo"),
                new AutomationClient.Project("project-30", "Zulu"));
        runScenario(firstFixture, firstExpected, 2, 2);

        List<Datum> secondFixture = List.of(
                new Datum("same-2", "Same"),
                new Datum("omega", "Omega"),
                new Datum("quote", "A\"Quote"),
                new Datum("same-1", "Same"),
                new Datum("slash", "Back\\slash"));
        List<AutomationClient.Project> secondExpected = List.of(
                new AutomationClient.Project("quote", "A\"Quote"),
                new AutomationClient.Project("slash", "Back\\slash"),
                new AutomationClient.Project("omega", "Omega"),
                new AutomationClient.Project("same-1", "Same"),
                new AutomationClient.Project("same-2", "Same"));
        runScenario(secondFixture, secondExpected, 3, 2);

        runScenario(List.of(), List.of(), 4, 1);
        System.out.println("PASS: all pages retrieved and stable order verified");
    }

    private static void runScenario(
            List<Datum> fixture,
            List<AutomationClient.Project> expected,
            int pageSize,
            int retrievals) throws Exception {
        try (ContractPinnedMock mock = new ContractPinnedMock(fixture)) {
            AutomationClient client = new AutomationClient(
                    mock.baseUrl(), "fixture-token", pageSize);
            for (int retrieval = 0; retrieval < retrievals; retrieval++) {
                List<AutomationClient.Project> actual = client.listAllProjects();
                require(expected.equals(actual),
                        "retrieval " + retrieval
                                + " was incomplete or not sorted by name then id: " + actual);
            }
            int pages = Math.max(1, (fixture.size() + pageSize - 1) / pageSize);
            verifyRequests(mock.requestLog(), pageSize, pages, retrievals);
        }
    }

    private static void verifyRequests(
            List<RequestRecord> requests, int pageSize, int pages, int retrievals) {
        int expectedCount = pages * retrievals;
        require(requests.size() == expectedCount,
                "expected " + expectedCount + " requests, got " + requests.size());
        for (int i = 0; i < requests.size(); i++) {
            RequestRecord request = requests.get(i);
            int expectedPage = i % pages;
            require("GET".equals(request.method()), "request " + i + " was not GET");
            require(OPERATION_PATH.equals(request.path()), "request " + i + " used wrong path");
            require(Integer.toString(expectedPage).equals(request.query().get("page")),
                    "request " + i + " used wrong page");
            require(Integer.toString(pageSize).equals(request.query().get("size")),
                    "request " + i + " did not use configured size");
            require(API_VERSION.equals(request.query().get("apiVersion")),
                    "request " + i + " did not pin apiVersion");
            require("application/json".equals(request.accept()),
                    "request " + i + " had wrong Accept header");
            require("Bearer fixture-token".equals(request.authorization()),
                    "request " + i + " had wrong Authorization header");
        }
    }

    private static void verifyContractDigest() throws Exception {
        byte[] bytes = Files.readAllBytes(Path.of("docs", "contract.json"));
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder hex = new StringBuilder();
        for (byte b : digest) {
            hex.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        }
        require(CONTRACT_SHA256.equals(hex.toString()), "docs/contract.json does not match fixture contract");
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> parsed = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return parsed;
        }
        for (String pair : rawQuery.split("&")) {
            int equals = pair.indexOf('=');
            String rawKey = equals < 0 ? pair : pair.substring(0, equals);
            String rawValue = equals < 0 ? "" : pair.substring(equals + 1);
            String key = URLDecoder.decode(rawKey, StandardCharsets.UTF_8);
            String value = URLDecoder.decode(rawValue, StandardCharsets.UTF_8);
            if (parsed.put(key, value) != null) {
                throw new AssertionError("duplicate query key: " + key);
            }
        }
        return parsed;
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
