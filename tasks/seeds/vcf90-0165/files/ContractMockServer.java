import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Contract-pinned loopback service used only by TestMain. */
public final class ContractMockServer implements AutoCloseable {
    public enum Scenario {
        NORMAL,
        INITIAL_LOGIN_FAILURE,
        PROJECT_FAILURE,
        REFRESH_FAILURE,
        RETRY_FAILURE
    }

    public record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {
        public String firstHeader(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name) && !entry.getValue().isEmpty()) {
                    return entry.getValue().get(0);
                }
            }
            return null;
        }
    }

    private static final Pattern OPERATION = Pattern.compile(
            "\\\"method\\\"\\s*:\\s*\\\"([A-Z]+)\\\"\\s*,\\s*\\\"path\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");

    private final HttpServer server;
    private final ExecutorService executor;
    private final Scenario scenario;
    private final Set<String> allowedOperations;
    private final List<LoggedRequest> requestLog = Collections.synchronizedList(new ArrayList<>());
    private final String initialToken;
    private final String refreshedToken;
    private final String projectId;
    private final String deploymentId;

    private int loginCalls;
    private int projectCreates;
    private int deploymentCreates;
    private int unexpectedRequests;

    private ContractMockServer(Path contractPath, Scenario scenario) throws IOException {
        this.scenario = scenario;
        this.allowedOperations = readOperations(contractPath);
        Set<String> expected = Set.of(
                "POST /iaas/api/login",
                "POST /iaas/api/projects",
                "POST /iaas/api/deployments");
        if (!allowedOperations.equals(expected)) {
            throw new IOException("contract operation set changed: " + allowedOperations);
        }

        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        int port = server.getAddress().getPort();
        initialToken = "access-expiring-" + port;
        refreshedToken = "access-refreshed-" + port;
        projectId = "project-" + port;
        deploymentId = "deployment-" + port;
        executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "vcf-contract-mock");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    public static ContractMockServer start(Path contractPath) throws IOException {
        return new ContractMockServer(contractPath, Scenario.NORMAL);
    }

    public static ContractMockServer start(Path contractPath, Scenario scenario) throws IOException {
        return new ContractMockServer(contractPath, scenario);
    }

    public URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
    }

    public String initialToken() {
        return initialToken;
    }

    public String refreshedToken() {
        return refreshedToken;
    }

    public String projectId() {
        return projectId;
    }

    public String deploymentId() {
        return deploymentId;
    }

    public synchronized int loginCalls() {
        return loginCalls;
    }

    public synchronized int projectCreates() {
        return projectCreates;
    }

    public synchronized int deploymentCreates() {
        return deploymentCreates;
    }

    public synchronized int unexpectedRequests() {
        return unexpectedRequests;
    }

    public List<LoggedRequest> requestLog() {
        synchronized (requestLog) {
            return List.copyOf(requestLog);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((name, values) -> headers.put(name, List.copyOf(values)));
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String path = exchange.getRequestURI().getRawPath();
        requestLog.add(new LoggedRequest(
                method,
                path,
                exchange.getRequestURI().getRawQuery(),
                Map.copyOf(headers),
                body));

        if (!allowedOperations.contains(method + " " + path)) {
            synchronized (this) {
                unexpectedRequests++;
            }
            respond(exchange, 404, "{\"message\":\"operation is not named by the contract\"}");
            return;
        }

        switch (path) {
            case "/iaas/api/login" -> handleLogin(exchange);
            case "/iaas/api/projects" -> handleProject(exchange);
            case "/iaas/api/deployments" -> handleDeployment(exchange);
            default -> throw new AssertionError("allowed operation has no fixture handler: " + path);
        }
    }

    private synchronized void handleLogin(HttpExchange exchange) throws IOException {
        loginCalls++;
        if ((scenario == Scenario.INITIAL_LOGIN_FAILURE && loginCalls == 1)
                || (scenario == Scenario.REFRESH_FAILURE && loginCalls == 2)) {
            respond(exchange, 503,
                    "{\"message\":\"authentication unavailable\",\"token\":\"error-token\"}");
            return;
        }
        String token = loginCalls == 1 ? initialToken : refreshedToken;
        respond(exchange, 200, "{\"tokenType\":\"Bearer\",\"token\":\"" + token + "\"}");
    }

    private synchronized void handleProject(HttpExchange exchange) throws IOException {
        if (!hasBearer(exchange, initialToken)) {
            respond(exchange, 401, "{\"message\":\"access token is not valid\"}");
            return;
        }
        if (scenario == Scenario.PROJECT_FAILURE) {
            respond(exchange, 503,
                    "{\"message\":\"project service unavailable\",\"id\":\"error-project\"}");
            return;
        }
        if (projectCreates != 0) {
            respond(exchange, 409, "{\"message\":\"project already exists\"}");
            return;
        }
        projectCreates++;
        respond(exchange, 201, "{\"id\":\"" + projectId + "\",\"name\":\"fixture project\"}");
    }

    private synchronized void handleDeployment(HttpExchange exchange) throws IOException {
        if (hasBearer(exchange, initialToken)) {
            respond(exchange, 401, "{\"message\":\"access token expired\"}");
            return;
        }
        if (!hasBearer(exchange, refreshedToken)) {
            respond(exchange, 401, "{\"message\":\"access token is not valid\"}");
            return;
        }
        if (scenario == Scenario.RETRY_FAILURE) {
            respond(exchange, 503,
                    "{\"message\":\"deployment service unavailable\","
                            + "\"id\":\"error-deployment\"}");
            return;
        }
        if (projectCreates != 1) {
            respond(exchange, 400, "{\"message\":\"project is missing\"}");
            return;
        }
        deploymentCreates++;
        respond(exchange, 201, "{\"id\":\"" + deploymentId + "\",\"projectId\":\"" + projectId + "\"}");
    }

    private static boolean hasBearer(HttpExchange exchange, String token) {
        return ("Bearer " + token).equals(exchange.getRequestHeaders().getFirst("Authorization"));
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static Set<String> readOperations(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        Matcher matcher = OPERATION.matcher(contract);
        Set<String> operations = new LinkedHashSet<>();
        while (matcher.find()) {
            operations.add(matcher.group(1) + " " + matcher.group(2));
        }
        return Set.copyOf(operations);
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
