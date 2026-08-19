import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Loopback mock for exactly the three operations pinned in docs/contract.json. */
public final class MockVcfAutomationServer implements AutoCloseable {
    private static final Pattern SUBMIT_PATH = Pattern.compile(
            "^/deployment/api/deployments/([^/]+)/requests$");
    private static final String REQUEST_PATH = "/deployment/api/requests/request-7";
    private static final String TOKEN_PATH = "/csp/gateway/am/api/auth/token";
    private static final String OLD_ACCESS_TOKEN = "access-before-expiry";
    private static final String NEW_ACCESS_TOKEN = "access-after-refresh";
    private static final String SECOND_ACCESS_TOKEN = "access-after-second-refresh";
    private static final String OLD_REFRESH_TOKEN = "refresh before+rotation&step=1";
    private static final String NEW_REFRESH_TOKEN = "refresh after+rotation&step=2";
    private static final String CLIENT_ID = "test-client";
    private static final String CLIENT_SECRET = "test-secret";

    private final HttpServer server;
    private final Mode mode;
    private final List<LogEntry> requestLog = Collections.synchronizedList(new ArrayList<>());
    private boolean actionSubmitted;
    private boolean oldAccessExpired;
    private boolean newAccessExpired;
    private boolean operationReachedTerminal;
    private int successfulPolls;
    private int refreshCount;
    private String deploymentId;
    private String actionId;

    public MockVcfAutomationServer() throws IOException {
        this(Mode.EXPIRY_AND_ROTATION);
    }

    public MockVcfAutomationServer(Mode mode) throws IOException {
        this.mode = mode;
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", this::handle);
        server.setExecutor(Runnable::run);
    }

    public void start() {
        server.start();
    }

    public URI baseUri() {
        return URI.create("http://" + loopbackHost() + ":" + server.getAddress().getPort() + "/");
    }

    private String loopbackHost() {
        String address = server.getAddress().getAddress().getHostAddress();
        return address.contains(":") ? "[" + address + "]" : address;
    }

    public String initialAccessToken() {
        return OLD_ACCESS_TOKEN;
    }

    public String initialRefreshToken() {
        return OLD_REFRESH_TOKEN;
    }

    public String clientId() {
        return CLIENT_ID;
    }

    public String clientSecret() {
        return CLIENT_SECRET;
    }

    public synchronized boolean operationReachedTerminal() {
        return operationReachedTerminal;
    }

    public List<LogEntry> requestLog() {
        synchronized (requestLog) {
            return List.copyOf(requestLog);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getRawPath();
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);

        Response response;
        synchronized (this) {
            Matcher submit = SUBMIT_PATH.matcher(path);
            if ("POST".equals(method) && submit.matches()) {
                response = submitAction(submit.group(1), authorization, contentType, body);
            } else if ("GET".equals(method) && REQUEST_PATH.equals(path)) {
                response = getRequest(authorization);
            } else if ("POST".equals(method) && TOKEN_PATH.equals(path)) {
                response = refreshToken(authorization, contentType, body);
            } else {
                response = new Response(404, "{\"error\":\"operation is not in pinned contract\"}");
            }
            requestLog.add(new LogEntry(method, path, authorization, contentType, body,
                    response.status, operationReachedTerminal));
        }
        byte[] bytes = response.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private Response submitAction(
            String encodedDeploymentId, String authorization, String contentType, String body) {
        Response denied = authorizeProtected(authorization);
        if (denied != null) {
            return denied;
        }
        if (contentType == null || !contentType.startsWith("application/json")) {
            return new Response(415, "{\"error\":\"application/json required\"}");
        }
        String parsedAction = jsonString(body, "actionId");
        if (parsedAction == null || !body.matches("(?s).*\\\"inputs\\\"\\s*:\\s*\\{\\s*}.*")) {
            return new Response(400, "{\"error\":\"actionId and inputs are required by mock profile\"}");
        }
        if (actionSubmitted) {
            return new Response(409, "{\"error\":\"deployment action was already submitted\"}");
        }
        deploymentId = URLDecoder.decode(encodedDeploymentId, StandardCharsets.UTF_8);
        actionId = parsedAction;
        actionSubmitted = true;
        return new Response(200, requestJson("INPROGRESS"));
    }

    private Response getRequest(String authorization) {
        Response denied = authorizeProtected(authorization);
        if (denied != null) {
            return denied;
        }
        if (!actionSubmitted) {
            return new Response(404, "{\"error\":\"request not found\"}");
        }
        if (mode == Mode.POLL_HTTP_ERROR) {
            return new Response(503, "{\"error\":\"request service unavailable\"}");
        }
        if (mode == Mode.POLL_MALFORMED) {
            return new Response(200, "{\"id\":\"request-7\",\"status\":23}");
        }

        successfulPolls++;
        String status;
        if (successfulPolls == 1) {
            status = "INPROGRESS";
            oldAccessExpired = true;
        } else if (successfulPolls == 2) {
            status = "COMPLETION";
            newAccessExpired = true;
        } else {
            status = "SUCCESSFUL";
            operationReachedTerminal = true;
        }
        return new Response(200, requestJson(status));
    }

    private Response refreshToken(String authorization, String contentType, String body) {
        String expectedBasic = "Basic " + Base64.getEncoder().encodeToString(
                (CLIENT_ID + ":" + CLIENT_SECRET).getBytes(StandardCharsets.UTF_8));
        if (!expectedBasic.equals(authorization)) {
            return new Response(400, "{\"error\":\"invalid client authorization\"}");
        }
        if (contentType == null || !contentType.startsWith("application/x-www-form-urlencoded")) {
            return new Response(415, "{\"error\":\"form content type required\"}");
        }
        Map<String, String> form = parseForm(body);
        String expectedRefreshToken = refreshCount == 0 ? OLD_REFRESH_TOKEN : NEW_REFRESH_TOKEN;
        if (!"refresh_token".equals(form.get("grant_type"))
                || !expectedRefreshToken.equals(form.get("refresh_token"))
                || refreshCount >= 2) {
            return new Response(400, "{\"error\":\"invalid refresh grant\"}");
        }
        refreshCount++;
        if (refreshCount == 1) {
            return new Response(200, "{\"token_type\":\"Bearer\",\"expires_in\":3600,"
                    + "\"access_token\":\"" + NEW_ACCESS_TOKEN + "\","
                    + "\"refresh_token\":\"" + NEW_REFRESH_TOKEN + "\"}");
        }
        return new Response(200, "{\"token_type\":\"Bearer\",\"expires_in\":3600,"
                + "\"access_token\":\"" + SECOND_ACCESS_TOKEN + "\"}");
    }

    private Response authorizeProtected(String authorization) {
        if (("Bearer " + OLD_ACCESS_TOKEN).equals(authorization) && !oldAccessExpired) {
            return null;
        }
        if (("Bearer " + NEW_ACCESS_TOKEN).equals(authorization)
                && refreshCount >= 1 && !newAccessExpired) {
            return null;
        }
        if (("Bearer " + SECOND_ACCESS_TOKEN).equals(authorization) && refreshCount >= 2) {
            return null;
        }
        return new Response(401, "{\"error\":\"access token expired or invalid\"}");
    }

    private String requestJson(String status) {
        return "{\n"
                + "  \"requestedBy\": \"contract-test\",\n"
                + "  \"status\" : \"" + status + "\",\n"
                + "  \"actionId\": \"" + actionId + "\",\n"
                + "  \"deploymentId\": \"" + deploymentId + "\",\n"
                + "  \"id\" : \"request-7\",\n"
                + "  \"completedTasks\": 0,\n"
                + "  \"totalTasks\": 1\n"
                + "}";
    }

    private static Map<String, String> parseForm(String body) {
        Map<String, String> values = new LinkedHashMap<>();
        for (String pair : body.split("&")) {
            int equals = pair.indexOf('=');
            String key = equals < 0 ? pair : pair.substring(0, equals);
            String value = equals < 0 ? "" : pair.substring(equals + 1);
            values.put(URLDecoder.decode(key, StandardCharsets.UTF_8),
                    URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return values;
    }

    private static String jsonString(String json, String field) {
        Pattern pattern = Pattern.compile("\\\"" + Pattern.quote(field)
                + "\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\"\\\\])*)\\\"");
        Matcher matcher = pattern.matcher(json);
        return matcher.find() ? matcher.group(1).replace("\\\"", "\"").replace("\\\\", "\\") : null;
    }

    @Override
    public void close() {
        server.stop(0);
    }

    public enum Mode {
        EXPIRY_AND_ROTATION,
        POLL_HTTP_ERROR,
        POLL_MALFORMED
    }

    private static final class Response {
        private final int status;
        private final String body;

        private Response(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    public static final class LogEntry {
        public final String method;
        public final String path;
        public final String authorization;
        public final String contentType;
        public final String body;
        public final int responseStatus;
        public final boolean terminalAfterResponse;

        private LogEntry(
                String method,
                String path,
                String authorization,
                String contentType,
                String body,
                int responseStatus,
                boolean terminalAfterResponse) {
            this.method = method;
            this.path = path;
            this.authorization = authorization;
            this.contentType = contentType;
            this.body = body;
            this.responseStatus = responseStatus;
            this.terminalAfterResponse = terminalAfterResponse;
        }

        @Override
        public String toString() {
            return method + " " + path + " -> " + responseStatus
                    + " terminal=" + terminalAfterResponse;
        }
    }
}
