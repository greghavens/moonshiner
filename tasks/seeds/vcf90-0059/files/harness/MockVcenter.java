import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Loopback-only mock of the vCenter appliance-update endpoints.
 *
 * It is pinned to docs/contract.json: it serves ONLY the three operations the contract
 * names and answers every other request with 404. It never reaches the network -- it
 * binds 127.0.0.1 on an ephemeral port.
 *
 *   Appliance.Update.Pending_list      GET  /api/appliance/update/pending
 *   Appliance.Update.Pending_precheck  POST /api/appliance/update/pending/{version}?action=precheck
 *   Appliance.Update.Pending_install   POST /api/appliance/update/pending/{version}?action=install
 *
 * Every inbound request is appended to a JSONL request log so the verifier can assert the
 * exact wire shape the client produced.
 *
 * PROTECTED HARNESS FILE -- do not modify.
 */
public final class MockVcenter {

    static final String SESSION_HEADER = "vmware-api-session-id";
    private static final String LIST_PATH = "/api/appliance/update/pending";
    private static final String ITEM_PREFIX = "/api/appliance/update/pending/";

    private final HttpServer server;
    private final Path logFile;
    private final byte[] pendingList;
    private final byte[] precheckResult;
    private final int pendingListStatus;

    private int seq = 0;

    /** Mutable server-side state. Only a successful install may change it. */
    private String installedVersion = null;
    private int installCount = 0;

    public MockVcenter(Path scenarioDir, Path logFile) throws IOException {
        this.logFile = logFile;
        this.pendingList = Files.readAllBytes(scenarioDir.resolve("pending_list.json"));
        this.precheckResult = Files.readAllBytes(scenarioDir.resolve("precheck_result.json"));
        Path statusFile = scenarioDir.resolve("pending_list_status.txt");
        this.pendingListStatus = Files.isRegularFile(statusFile)
                ? Integer.parseInt(Files.readString(statusFile, StandardCharsets.UTF_8).trim())
                : 200;
        Files.deleteIfExists(logFile);
        Files.createDirectories(logFile.toAbsolutePath().getParent());
        Files.write(logFile, new byte[0]);

        this.server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.createContext("/", this::dispatch);
        this.server.setExecutor(null);
    }

    public void start() {
        server.start();
    }

    public void stop() {
        server.stop(0);
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort() + "/api";
    }

    public synchronized String installedVersion() {
        return installedVersion;
    }

    public synchronized int installCount() {
        return installCount;
    }

    // ---------------------------------------------------------------- routing

    private void dispatch(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String rawPath = ex.getRequestURI().getRawPath();
        String rawQuery = ex.getRequestURI().getRawQuery();
        String sessionHeader = ex.getRequestHeaders().getFirst(SESSION_HEADER);
        String contentType = ex.getRequestHeaders().getFirst("Content-Type");
        byte[] body = ex.getRequestBody().readAllBytes();
        String bodyText = new String(body, StandardCharsets.UTF_8);

        int status;
        try {
            status = route(ex, method, rawPath, rawQuery, sessionHeader, bodyText);
        } catch (RuntimeException e) {
            status = 500;
            send(ex, 500, error("com.vmware.vapi.std.errors.error", String.valueOf(e.getMessage())));
        }
        log(method, rawPath, rawQuery, sessionHeader, contentType, bodyText, status);
        ex.close();
    }

    private int route(HttpExchange ex, String method, String rawPath, String rawQuery,
                      String sessionHeader, String bodyText) throws IOException {

        if (sessionHeader == null || sessionHeader.isEmpty()) {
            return send(ex, 401, error("com.vmware.vapi.std.errors.unauthenticated",
                    "session is not authenticated"));
        }

        Map<String, List<String>> query = parseQuery(rawQuery);

        // Appliance.Update.Pending_list
        if (rawPath.equals(LIST_PATH)) {
            if (!method.equals("GET")) {
                return send(ex, 405, error("com.vmware.vapi.std.errors.not_allowed_in_current_state",
                        "method not allowed"));
            }
            List<String> sourceType = query.get("source_type");
            if (sourceType == null || sourceType.size() != 1 || sourceType.get(0).isEmpty()) {
                return send(ex, 400, error("com.vmware.vapi.std.errors.invalid_argument",
                        "required query parameter source_type is missing or empty"));
            }
            return send(ex, pendingListStatus, pendingList);
        }

        // Appliance.Update.Pending_precheck / Appliance.Update.Pending_install
        if (rawPath.startsWith(ITEM_PREFIX)) {
            String rawVersion = rawPath.substring(ITEM_PREFIX.length());
            if (rawVersion.isEmpty() || rawVersion.contains("/")) {
                return send(ex, 404, error("com.vmware.vapi.std.errors.not_found", "the update is not found"));
            }
            String version = URLDecoder.decode(rawVersion, StandardCharsets.UTF_8);

            List<String> action = query.get("action");
            String verb = (action != null && action.size() == 1) ? action.get(0) : null;

            // Operations outside the contract are deliberately not served.
            if (verb == null || !(verb.equals("precheck") || verb.equals("install"))) {
                return send(ex, 404, error("com.vmware.vapi.std.errors.not_found",
                        "operation is not served by this mock"));
            }
            if (!method.equals("POST")) {
                return send(ex, 405, error("com.vmware.vapi.std.errors.not_allowed_in_current_state",
                        "method not allowed"));
            }

            if (verb.equals("precheck")) {
                return send(ex, 200, precheckResult);
            }

            // install: user_data is a required body field per the specification.
            if (!bodyText.contains("\"user_data\"")) {
                return send(ex, 400, error("com.vmware.vapi.std.errors.invalid_argument",
                        "required body field user_data is missing"));
            }
            synchronized (this) {
                installedVersion = version;
                installCount++;
            }
            ex.sendResponseHeaders(204, -1);
            return 204;
        }

        return send(ex, 404, error("com.vmware.vapi.std.errors.not_found",
                "operation is not served by this mock"));
    }

    // ------------------------------------------------------------------- i/o

    private int send(HttpExchange ex, int status, byte[] payload) throws IOException {
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, payload.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(payload);
        }
        return status;
    }

    private static byte[] error(String id, String message) {
        String json = "{\"error_type\":\"" + id + "\",\"messages\":[{\"id\":\"" + id
                + "\",\"default_message\":" + quote(message) + ",\"args\":[]}]}";
        return json.getBytes(StandardCharsets.UTF_8);
    }

    private synchronized void log(String method, String rawPath, String rawQuery, String sessionHeader,
                                  String contentType, String bodyText, int status) throws IOException {
        seq++;
        StringBuilder sb = new StringBuilder(256);
        sb.append('{');
        sb.append("\"seq\":").append(seq);
        sb.append(",\"method\":").append(quote(method));
        sb.append(",\"raw_path\":").append(quote(rawPath));
        sb.append(",\"raw_query\":").append(rawQuery == null ? "null" : quote(rawQuery));
        sb.append(",\"session_header\":").append(sessionHeader == null ? "null" : quote(sessionHeader));
        sb.append(",\"content_type\":").append(contentType == null ? "null" : quote(contentType));
        sb.append(",\"body\":").append(quote(bodyText));
        sb.append(",\"status\":").append(status);
        sb.append('}').append('\n');
        Files.writeString(logFile, sb.toString(), StandardCharsets.UTF_8,
                java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String name = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            out.computeIfAbsent(URLDecoder.decode(name, StandardCharsets.UTF_8), k -> new ArrayList<>())
                    .add(URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return out;
    }

    static String quote(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 2);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        return sb.append('"').toString();
    }
}
