import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class ContractMock implements AutoCloseable {
    enum Scenario {
        FAILED_ATTESTATION,
        RUNNING_TASK,
        EVENT_SERVICE_UNAVAILABLE
    }

    record LoggedRequest(
            String operationId,
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body) {
        String bodyUtf8() {
            return new String(body, StandardCharsets.UTF_8);
        }

        String firstHeader(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name) && !entry.getValue().isEmpty()) {
                    return entry.getValue().get(0);
                }
            }
            return null;
        }
    }

    static final String EVENT_EVIDENCE =
            "PCR7 event=EV_EFI_VARIABLE_DRIVER_CONFIG result=SECURE_BOOT_DISABLED";
    static final String SUPPORT_TASK_ID = "support bundle/task#77";

    private static final String TASK_OPERATION = "Cis.Tasks_get";
    private static final String EVENT_OPERATION =
            "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get";
    private static final String SUPPORT_OPERATION = "Appliance.SupportBundle_create$Task";
    private static final Pattern TASK_ROUTE = Pattern.compile("^/api/cis/tasks/([^/]+)$");
    private static final Pattern EVENT_ROUTE = Pattern.compile(
            "^/api/vcenter/trusted-infrastructure/hosts/([^/]+)"
                    + "/hardware/tpm/([^/]+)/event-log$");
    private static final Pattern OPERATION_IDS =
            Pattern.compile("\"operationId\"\\s*:\\s*\"([^\"]+)\"");

    private final HttpServer server;
    private final ExecutorService executor;
    private final Scenario scenario;
    private final List<LoggedRequest> requests = new ArrayList<>();

    ContractMock(Path contractPath, Scenario scenario) throws IOException {
        assertPinnedContract(Files.readString(contractPath, StandardCharsets.UTF_8));
        this.scenario = scenario;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        executor = Executors.newSingleThreadExecutor();
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    synchronized List<LoggedRequest> requests() {
        return List.copyOf(requests);
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        String method = exchange.getRequestMethod();
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String operationId = identifyOperation(method, rawPath, rawQuery);

        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach(
                (name, values) -> headers.put(name, List.copyOf(values)));
        synchronized (this) {
            requests.add(new LoggedRequest(
                    operationId,
                    method,
                    rawPath,
                    rawQuery,
                    Map.copyOf(headers),
                    requestBody.clone()));
        }

        if (TASK_OPERATION.equals(operationId)) {
            Matcher matcher = TASK_ROUTE.matcher(rawPath);
            matcher.matches();
            String decodedTask = URLDecoder.decode(matcher.group(1), StandardCharsets.UTF_8);
            if (scenario == Scenario.RUNNING_TASK) {
                respond(exchange, 200, runningTask(decodedTask));
            } else {
                respond(exchange, 200, failedTask(decodedTask));
            }
            return;
        }

        if (EVENT_OPERATION.equals(operationId)) {
            if (scenario == Scenario.EVENT_SERVICE_UNAVAILABLE) {
                respond(exchange, 503,
                        "{\"error_type\":\"SERVICE_UNAVAILABLE\","
                                + "\"messages\":[{\"id\":\"mock.event.unavailable\","
                                + "\"default_message\":\"event service unavailable\","
                                + "\"args\":[]}]}");
            } else {
                respond(exchange, 200, eventLog());
            }
            return;
        }

        if (SUPPORT_OPERATION.equals(operationId)) {
            respond(exchange, 202, jsonString(SUPPORT_TASK_ID));
            return;
        }

        respond(exchange, 404,
                "{\"error_type\":\"NOT_FOUND\","
                        + "\"messages\":[{\"id\":\"mock.route\","
                        + "\"default_message\":\"operation is outside the pinned contract\","
                        + "\"args\":[]}]}");
    }

    private static String identifyOperation(String method, String rawPath, String rawQuery) {
        if ("GET".equals(method) && rawQuery == null && TASK_ROUTE.matcher(rawPath).matches()) {
            return TASK_OPERATION;
        }
        if ("GET".equals(method) && rawQuery == null && EVENT_ROUTE.matcher(rawPath).matches()) {
            return EVENT_OPERATION;
        }
        if ("POST".equals(method)
                && "/api/appliance/support-bundle".equals(rawPath)
                && "vmw-task=true".equals(rawQuery)) {
            return SUPPORT_OPERATION;
        }
        return null;
    }

    private static String failedTask(String decodedTask) {
        return "{"
                + "\"description\":{\"id\":\"mock.attestation\","
                + "\"default_message\":\"Attestation task failed\","
                + "\"args\":[" + jsonString(decodedTask) + "]},"
                + "\"service\":\"com.vmware.vcenter.trusted_infrastructure\","
                + "\"operation\":\"attest\","
                + "\"status\":\"FAILED\","
                + "\"cancelable\":false,"
                + "\"error\":{\"error_type\":\"FAILED_ATTESTATION\","
                + "\"messages\":[{\"id\":\"mock.attestation.failed\","
                + "\"default_message\":\"Host trust check failed; inspect TPM events\","
                + "\"args\":[]}]}"
                + "}";
    }

    private static String runningTask(String decodedTask) {
        return "{"
                + "\"description\":{\"id\":\"mock.attestation\","
                + "\"default_message\":\"Attestation task is running\","
                + "\"args\":[" + jsonString(decodedTask) + "]},"
                + "\"service\":\"com.vmware.vcenter.trusted_infrastructure\","
                + "\"operation\":\"attest\","
                + "\"status\":\"RUNNING\","
                + "\"cancelable\":true"
                + "}";
    }

    private static String eventLog() {
        String data = Base64.getEncoder().encodeToString(
                EVENT_EVIDENCE.getBytes(StandardCharsets.UTF_8));
        return "{"
                + "\"type\":\"EFI_TCG2_EVENT_LOG_FORMAT_TCG_2\","
                + "\"data\":" + jsonString(data) + ","
                + "\"truncated\":false,"
                + "\"banks\":[]"
                + "}";
    }

    private static void respond(HttpExchange exchange, int status, String body)
            throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static String jsonString(String value) {
        StringBuilder out = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            switch (ch) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\f' -> out.append("\\f");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (ch < 0x20) {
                        out.append(String.format("\\u%04x", (int) ch));
                    } else {
                        out.append(ch);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    private static void assertPinnedContract(String contract) {
        List<String> ids = new ArrayList<>();
        Matcher matcher = OPERATION_IDS.matcher(contract);
        while (matcher.find()) {
            ids.add(matcher.group(1));
        }
        List<String> expected = List.of(
                TASK_OPERATION,
                EVENT_OPERATION,
                SUPPORT_OPERATION);
        if (!ids.equals(expected)) {
            throw new IllegalArgumentException(
                    "contract operationIds are not the focused pinned set: " + ids);
        }
        requireContains(contract,
                "\"commitSha\": \"3949fc33339fc5ea1b77eadb258f1cf49aa88e26\"");
        requireContains(contract,
                "\"specBlobSha\": \"8028b0824c4ff3503d05f44814f967938a795c40\"");
        requireContains(contract,
                "\"specPath\": \"specifications/vsphere/openapi/automation/vcenter.yaml\"");
        requireContains(contract, "\"name\": \"vmware-api-session-id\"");
        requireContains(contract, "\"path\": \"/api/cis/tasks/{task}\"");
        requireContains(contract,
                "\"path\": \"/api/vcenter/trusted-infrastructure/hosts/{host}/hardware/tpm/{tpm}/event-log\"");
        requireContains(contract,
                "\"path\": \"/api/appliance/support-bundle?vmw-task=true\"");
    }

    private static void requireContains(String text, String expected) {
        if (!text.contains(expected)) {
            throw new IllegalArgumentException("contract is missing: " + expected);
        }
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
        try {
            executor.awaitTermination(2, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }
}
