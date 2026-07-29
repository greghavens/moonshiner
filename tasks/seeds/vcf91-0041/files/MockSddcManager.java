import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class MockSddcManager implements AutoCloseable {
    record RequestLogEntry(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {
        String header(String name) {
            return headers.entrySet().stream()
                    .filter(entry -> entry.getKey().equalsIgnoreCase(name))
                    .flatMap(entry -> entry.getValue().stream())
                    .findFirst()
                    .orElse(null);
        }
    }

    private static final String CONTRACT_SHA256 =
            "e268a778ce405fbd41863e3a39d9d71b9e7cc940d14a339551c4ec7ef2976663";
    private static final String CREATED = "2026-07-29T12:00:00Z";

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<RequestLogEntry> requests = new ArrayList<>();
    private int submittedChanges;
    private int vcenterPolls;

    MockSddcManager() throws IOException {
        verifyPinnedContract();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "mock-sddc-manager");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
    }

    synchronized List<RequestLogEntry> requestLog() {
        return List.copyOf(requests);
    }

    private void verifyPinnedContract() throws IOException {
        byte[] bytes = Files.readAllBytes(Path.of("docs", "contract.json"));
        String actual;
        try {
            actual = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
        if (!CONTRACT_SHA256.equals(actual)) {
            throw new IOException("docs/contract.json no longer matches the mock's pinned contract");
        }
        String contract = new String(bytes, StandardCharsets.UTF_8);
        requireContractFact(contract, "\"updateOrRotatePasswords\"");
        requireContractFact(contract, "\"getCredentialsTask\"");
        requireContractFact(contract, "\"path\": \"/v1/credentials\"");
        requireContractFact(contract, "\"path\": \"/v1/credentials/tasks/{id}\"");
    }

    private static void requireContractFact(String contract, String fact) throws IOException {
        if (!contract.contains(fact)) {
            throw new IOException("Pinned contract is missing " + fact);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] bodyBytes = exchange.getRequestBody().readAllBytes();
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        Map<String, List<String>> copiedHeaders = exchange.getRequestHeaders().entrySet().stream()
                .collect(java.util.stream.Collectors.toUnmodifiableMap(
                        Map.Entry::getKey, entry -> List.copyOf(entry.getValue())));
        synchronized (this) {
            requests.add(new RequestLogEntry(
                    exchange.getRequestMethod(),
                    rawPath,
                    rawQuery,
                    copiedHeaders,
                    new String(bodyBytes, StandardCharsets.UTF_8)));
        }

        if ("PATCH".equals(exchange.getRequestMethod())
                && "/v1/credentials".equals(rawPath)
                && rawQuery == null) {
            handleUpdate(exchange);
            return;
        }
        if ("GET".equals(exchange.getRequestMethod())
                && rawQuery == null
                && rawPath.startsWith("/v1/credentials/tasks/")) {
            handleTask(exchange, rawPath);
            return;
        }
        sendJson(exchange, 404,
                "{\"errorCode\":\"MOCK_ROUTE_NOT_IN_CONTRACT\","
                        + "\"message\":\"The loopback mock only serves its two pinned operations.\"}");
    }

    private synchronized void handleUpdate(HttpExchange exchange) throws IOException {
        submittedChanges++;
        if (submittedChanges == 1) {
            sendJson(exchange, 202, taskSubmission("task vcenter/1"));
        } else if (submittedChanges == 2) {
            sendJson(exchange, 202, taskSubmission("task nsx/2"));
        } else {
            sendJson(exchange, 500,
                    "{\"errorCode\":\"MOCK_UNEXPECTED_CHANGE\","
                            + "\"message\":\"A change was submitted after a failed task.\"}");
        }
    }

    private synchronized void handleTask(HttpExchange exchange, String rawPath) throws IOException {
        if ("/v1/credentials/tasks/task%20vcenter%2F1".equals(rawPath)) {
            vcenterPolls++;
            String status = vcenterPolls == 1 ? "IN_PROGRESS" : "SUCCESSFUL";
            sendJson(exchange, 200, credentialTask("task vcenter/1", status, null));
            return;
        }
        if ("/v1/credentials/tasks/task%20nsx%2F2".equals(rawPath)) {
            String error = "\"errors\":[{"
                    + "\"remediationMessage\":\"Choose a password not used recently.\","
                    + "\"message\":\"Password rejected by NSX \\\"history\\\" policy.\","
                    + "\"errorCode\":\"VCF_CREDENTIAL_0042\"}]";
            sendJson(exchange, 200, credentialTask("task nsx/2", "FAILED", error));
            return;
        }
        sendJson(exchange, 404,
                "{\"errorCode\":\"MOCK_TASK_NOT_FOUND\",\"message\":\"Unknown task.\"}");
    }

    private static String taskSubmission(String id) {
        return "{\"creationTimestamp\":\"" + CREATED + "\","
                + "\"name\":\"Update credentials\","
                + "\"status\":\"PENDING\","
                + "\"id\":\"" + id + "\"}";
    }

    private static String credentialTask(String id, String status, String extraField) {
        StringBuilder json = new StringBuilder()
                .append("{\"type\":\"UPDATE\",")
                .append("\"name\":\"Update credentials\",")
                .append("\"creationTimestamp\":\"").append(CREATED).append("\",")
                .append("\"id\":\"").append(id).append("\",");
        if (extraField != null) {
            json.append(extraField).append(',');
        } else {
            json.append("\"subTasks\":[],");
        }
        return json.append("\"status\":\"").append(status).append("\"}").toString();
    }

    private static void sendJson(HttpExchange exchange, int status, String body)
            throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        Headers headers = exchange.getResponseHeaders();
        headers.set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
