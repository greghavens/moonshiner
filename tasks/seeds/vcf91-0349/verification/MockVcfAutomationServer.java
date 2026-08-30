import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;

/** Contract-pinned loopback fixture used only by TestMain. */
public final class MockVcfAutomationServer implements AutoCloseable {
    public record FixtureDeployment(String id, String name, String status, String projectId) {}

    public record RequestLogEntry(
            String method,
            String path,
            Map<String, List<String>> query,
            String authorization,
            Set<String> identifiersReturned,
            String identifierUsed,
            int responseStatus) {}

    private record Project(String id, String name) {}

    private static final String PROJECTS_PATH = "/iaas/api/projects";
    private static final String DEPLOYMENTS_PATH = "/deployment/api/deployments";

    private final HttpServer server;
    private final String token = "fixture-token-9-1";
    private final Project targetProject =
            new Project("project-target-9-1", "Platform O'Brien / DR");
    private final List<Project> projects;
    private final List<FixtureDeployment> deployments;
    private final CopyOnWriteArrayList<RequestLogEntry> requestLog = new CopyOnWriteArrayList<>();

    public MockVcfAutomationServer(Path contractPath) throws IOException {
        pinToContract(contractPath);
        Project decoyBefore = new Project("project-decoy-before", "Platform OBrien / DR");
        Project decoyAfter = new Project("project-decoy-after", "Platform O'Brien / DR ");
        projects = List.of(decoyBefore, targetProject, decoyAfter);
        deployments = List.of(
                deployment("deployment-zulu", "Zulu \\ site\b\f\r\t" + (char) 1,
                        "FAILED", targetProject.id()),
                deployment("deployment-z-alpha", "Alpha site",
                        "CREATE_SUCCESSFUL", targetProject.id()),
                deployment("deployment-quote", "Quote \"site\" – 東京",
                        "UPDATE_SUCCESSFUL", targetProject.id()),
                deployment("deployment-a-alpha", "Alpha site",
                        "UPDATE_SUCCESSFUL", targetProject.id()),
                deployment("deployment-line", "Line\nbreak",
                        "CREATE_SUCCESSFUL", targetProject.id()),
                deployment("deployment-decoy", "Decoy",
                        "CREATE_SUCCESSFUL", decoyBefore.id()));
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
    }

    private FixtureDeployment deployment(
            String id, String name, String status, String projectId) {
        return new FixtureDeployment(id, name, status, projectId);
    }

    private static void pinToContract(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        require(contract.contains("\"source_statement\"")
                        && contract.contains("reference documentation, not a published API specification"),
                "contract must identify its reference-documentation provenance");
        require(contract.contains("\"product_version\": \"9.1\""),
                "contract must be pinned to VCF Automation 9.1");
        require(contract.contains("\"path\": \"" + PROJECTS_PATH + "\""),
                "contract is missing Get Projects");
        require(contract.contains("\"path\": \"" + DEPLOYMENTS_PATH + "\""),
                "contract is missing Get Deployments");
        int operationCount = contract.split("\\\"operation\\\"", -1).length - 1;
        require(operationCount == 2, "fixture expects exactly the two named operations");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new IllegalArgumentException(message);
        }
    }

    public void start() {
        server.start();
    }

    public URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    public String token() {
        return token;
    }

    public String targetProjectName() {
        return targetProject.name();
    }

    public List<FixtureDeployment> expectedDeployments() {
        return deployments.stream()
                .filter(item -> item.projectId().equals(targetProject.id()))
                .sorted(Comparator.comparing(FixtureDeployment::name)
                        .thenComparing(FixtureDeployment::id))
                .toList();
    }

    public List<RequestLogEntry> requestLog() {
        return List.copyOf(requestLog);
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getPath();
        Map<String, List<String>> query = parseQuery(exchange.getRequestURI().getRawQuery());
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");

        if (!method.equals("GET") || (!path.equals(PROJECTS_PATH) && !path.equals(DEPLOYMENTS_PATH))) {
            sendAndLog(exchange, query, authorization, Set.of(), null, 404,
                    "{\"message\":\"operation is not in the pinned contract\"}");
            return;
        }
        if (!String.valueOf(authorization).equals("Bearer " + token)) {
            // Keep error bodies shape-compatible with successful responses so clients must
            // recognize the HTTP status instead of failing incidentally while decoding JSON.
            String body = path.equals(PROJECTS_PATH)
                    ? "{\"content\":[{\"id\":" + json(targetProject.id())
                            + ",\"name\":" + json(targetProject.name())
                            + "}],\"totalElements\":1,\"numberOfElements\":1}"
                    : "{\"content\":[],\"first\":true,\"last\":true,\"number\":0,"
                            + "\"numberOfElements\":0,\"size\":2,\"totalElements\":0,"
                            + "\"totalPages\":0}";
            sendAndLog(exchange, query, authorization, Set.of(), null, 401, body);
            return;
        }
        if (path.equals(PROJECTS_PATH)) {
            handleProjects(exchange, query, authorization);
        } else {
            handleDeployments(exchange, query, authorization);
        }
    }

    private void handleProjects(
            HttpExchange exchange, Map<String, List<String>> query, String authorization) throws IOException {
        Set<String> allowed = Set.of("apiVersion", "$top", "$skip", "$orderBy", "$count", "$filter");
        if (!allowed.containsAll(query.keySet())) {
            sendAndLog(exchange, query, authorization, Set.of(), null, 400,
                    "{\"message\":\"unsupported projects query parameter\"}");
            return;
        }

        List<Project> filtered = new ArrayList<>(projects);
        String filter = first(query, "$filter");
        if (filter != null) {
            String prefix = "name eq '";
            if (!filter.startsWith(prefix) || !filter.endsWith("'")) {
                sendAndLog(exchange, query, authorization, Set.of(), null, 400,
                        "{\"message\":\"unsupported project filter\"}");
                return;
            }
            String wanted = filter.substring(prefix.length(), filter.length() - 1).replace("''", "'");
            filtered.removeIf(project -> !project.name().equals(wanted));
        }

        int skip = nonNegativeInt(first(query, "$skip"), 0);
        int top = positiveInt(first(query, "$top"), Math.max(1, filtered.size()));
        int from = Math.min(skip, filtered.size());
        int to = Math.min(from + top, filtered.size());
        List<Project> page = filtered.subList(from, to);
        Set<String> returned = new LinkedHashSet<>();
        StringBuilder body = new StringBuilder("{\"content\":[");
        for (int i = 0; i < page.size(); i++) {
            if (i > 0) body.append(',');
            Project project = page.get(i);
            returned.add(project.id());
            body.append("{\"id\":").append(json(project.id()))
                    .append(",\"name\":").append(json(project.name())).append('}');
        }
        body.append("],\"totalElements\":").append(filtered.size())
                .append(",\"numberOfElements\":").append(page.size()).append('}');
        sendAndLog(exchange, query, authorization, returned, null, 200, body.toString());
    }

    private void handleDeployments(
            HttpExchange exchange, Map<String, List<String>> query, String authorization) throws IOException {
        Set<String> allowed = Set.of("page", "size", "sort", "projects");
        if (!allowed.containsAll(query.keySet())) {
            sendAndLog(exchange, query, authorization, Set.of(), null, 400,
                    "{\"message\":\"unsupported deployments query parameter\"}");
            return;
        }
        String projectId = first(query, "projects");
        if (projectId == null || projectId.contains(",")) {
            sendAndLog(exchange, query, authorization, Set.of(), projectId, 400,
                    "{\"message\":\"one project identifier is required\"}");
            return;
        }

        int pageNumber = nonNegativeInt(first(query, "page"), 0);
        int pageSize = positiveInt(first(query, "size"), 20);
        List<FixtureDeployment> filtered = deployments.stream()
                .filter(item -> item.projectId().equals(projectId))
                .toList();
        int totalPages = filtered.isEmpty() ? 0 : (filtered.size() + pageSize - 1) / pageSize;
        int from = Math.min(pageNumber * pageSize, filtered.size());
        int to = Math.min(from + pageSize, filtered.size());
        List<FixtureDeployment> page = filtered.subList(from, to);

        StringBuilder body = new StringBuilder("{\"content\":[");
        for (int i = 0; i < page.size(); i++) {
            if (i > 0) body.append(',');
            FixtureDeployment item = page.get(i);
            body.append("{\"id\":").append(json(item.id()))
                    .append(",\"name\":").append(json(item.name()))
                    .append(",\"status\":").append(json(item.status()))
                    .append(",\"projectId\":").append(json(item.projectId())).append('}');
        }
        body.append("],\"first\":").append(pageNumber == 0)
                .append(",\"last\":").append(totalPages == 0 || pageNumber + 1 >= totalPages)
                .append(",\"number\":").append(pageNumber)
                .append(",\"numberOfElements\":").append(page.size())
                .append(",\"size\":").append(pageSize)
                .append(",\"totalElements\":").append(filtered.size())
                .append(",\"totalPages\":").append(totalPages).append('}');
        sendAndLog(exchange, query, authorization, Set.of(), projectId, 200, body.toString());
    }

    private void sendAndLog(
            HttpExchange exchange,
            Map<String, List<String>> query,
            String authorization,
            Set<String> returned,
            String used,
            int status,
            String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
        Map<String, List<String>> frozenQuery = new LinkedHashMap<>();
        query.forEach((key, value) -> frozenQuery.put(key, List.copyOf(value)));
        requestLog.add(new RequestLogEntry(
                exchange.getRequestMethod(), exchange.getRequestURI().getPath(),
                Collections.unmodifiableMap(frozenQuery), authorization, Set.copyOf(returned), used, status));
    }

    private static int nonNegativeInt(String value, int defaultValue) {
        int parsed = value == null ? defaultValue : Integer.parseInt(value);
        if (parsed < 0) throw new IllegalArgumentException("negative integer query value");
        return parsed;
    }

    private static int positiveInt(String value, int defaultValue) {
        int parsed = value == null ? defaultValue : Integer.parseInt(value);
        if (parsed < 1) throw new IllegalArgumentException("non-positive integer query value");
        return parsed;
    }

    private static String first(Map<String, List<String>> query, String key) {
        List<String> values = query.get(key);
        return values == null || values.isEmpty() ? null : values.get(0);
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> result = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) return result;
        for (String pair : rawQuery.split("&")) {
            int equals = pair.indexOf('=');
            String rawKey = equals < 0 ? pair : pair.substring(0, equals);
            String rawValue = equals < 0 ? "" : pair.substring(equals + 1);
            String key = URLDecoder.decode(rawKey, StandardCharsets.UTF_8);
            String value = URLDecoder.decode(rawValue, StandardCharsets.UTF_8);
            result.computeIfAbsent(key, ignored -> new ArrayList<>()).add(value);
        }
        return result;
    }

    static String json(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
                }
            }
        }
        return out.append('"').toString();
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
