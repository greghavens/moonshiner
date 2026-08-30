import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback test double for the VCF Automation provisioning (IaaS) API.
 *
 * The route table is loaded from docs/contract.json at construction time: the server answers the
 * five operations that contract names and nothing else. Every request is appended to an in-memory
 * log that the harness reads back after the client has run.
 *
 * All state transitions are driven by poll counts, never by wall-clock time, so a run is
 * reproducible. The server binds the loopback interface only; nothing outside this process is
 * contacted.
 */
public final class MockAutomationServer {

    /* ---------------------------------------------------------------- fixtures */

    public static final String CLOUD_ACCOUNT_ID = "ca-vsphere-7f31";
    public static final String INFLIGHT_REQUEST_ID = "req-provision-a1";
    public static final String SECOND_INFLIGHT_REQUEST_ID = "req-provision-c3";
    public static final String SETTLED_REQUEST_ID = "req-provision-b2";
    public static final String ROTATION_REQUEST_ID = "req-cred-rotate-1";

    /** Number of drain observations before all in-flight provisioning requests settle. */
    public static final int DRAIN_POLLS_REQUIRED = 3;
    /** Number of observations of the rotation tracker before it reports FINISHED. */
    public static final int ROTATION_POLLS_REQUIRED = 3;

    private static final String ACCOUNT_NAME = "vc01-sfo-prod";
    private static final String ACCOUNT_HOSTNAME = "vc01.sfo.rainpole.local";
    private static final String ACCOUNT_USERNAME = "svc-vcfa@vsphere.local";
    private static final String ACCOUNT_DCID = "dc-collector-01";
    private static final String CREATED_AT = "2026-02-14T09:12:44.318Z";
    private static final String UPDATED_AT = "2026-07-30T18:03:05.771Z";
    private static final String OWNER = "svc-automation@rainpole.local";
    private static final String ORG_ID = "org-4b2c";

    /** externalRegionId -> display name, in the order the account reports them. */
    private static final String[][] REGIONS = {
        {"Datacenter:datacenter-3", "SFO-DC-3", "rgn-3a"},
        {"Datacenter:datacenter-9", "SFO-DC-9", "rgn-9c"},
    };

    /* ---------------------------------------------------------------- recorded traffic */

    /** One request as the server saw it. */
    public static final class Recorded {
        public final int sequence;
        public final String method;
        public final String path;
        public final String rawQuery;
        public final Map<String, List<String>> query;
        public final Map<String, String> headers;
        public final String body;
        public int responseStatus;

        Recorded(int sequence, String method, String path, String rawQuery,
                 Map<String, List<String>> query, Map<String, String> headers, String body) {
            this.sequence = sequence;
            this.method = method;
            this.path = path;
            this.rawQuery = rawQuery;
            this.query = query;
            this.headers = headers;
            this.body = body;
        }

        public String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }

        /** Single value of a query parameter, or null when absent. Repeated values return the first. */
        public String param(String name) {
            List<String> values = query.get(name);
            return (values == null || values.isEmpty()) ? null : values.get(0);
        }

        public int paramCount(String name) {
            List<String> values = query.get(name);
            return values == null ? 0 : values.size();
        }

        public String line() {
            return method + " " + path + (rawQuery == null || rawQuery.isEmpty() ? "" : "?" + rawQuery);
        }

        @Override
        public String toString() {
            return "#" + sequence + " " + line() + " -> " + responseStatus;
        }
    }

    private static final class Route {
        final String operationId;
        final String method;
        final Pattern pattern;
        final List<String> knownParams = new ArrayList<>();
        final List<String> requiredParams = new ArrayList<>();

        Route(String operationId, String method, Pattern pattern) {
            this.operationId = operationId;
            this.method = method;
            this.pattern = pattern;
        }
    }

    /* ---------------------------------------------------------------- state */

    private final String latestApiVersion;
    private final List<String> supportedApiVersions;
    private final String bearerToken;
    private final boolean rotationShouldFail;
    private final List<Route> routes = new ArrayList<>();
    private final List<Recorded> log = Collections.synchronizedList(new ArrayList<>());

    private HttpServer server;
    private String baseUrl;

    private String accountPassword;
    private int drainObservations;
    private int rotationObservations;
    private boolean rotationRequested;
    private String pendingPassword;
    private boolean rotationApplied;

    public MockAutomationServer(Path contractFile, String bearerToken, String latestApiVersion,
                                List<String> supportedApiVersions, String initialPassword) throws IOException {
        this(contractFile, bearerToken, latestApiVersion, supportedApiVersions, initialPassword, false);
    }

    public MockAutomationServer(Path contractFile, String bearerToken, String latestApiVersion,
                                List<String> supportedApiVersions, String initialPassword,
                                boolean rotationShouldFail) throws IOException {
        this.bearerToken = bearerToken;
        this.latestApiVersion = latestApiVersion;
        this.supportedApiVersions = List.copyOf(supportedApiVersions);
        this.accountPassword = initialPassword;
        this.rotationShouldFail = rotationShouldFail;
        loadRoutes(contractFile);
    }

    private void loadRoutes(Path contractFile) throws IOException {
        Map<String, Object> contract = Json.object(Json.parse(Files.readString(contractFile)));
        for (Object entry : Json.arr(contract, "operations")) {
            Map<String, Object> operation = Json.object(entry);
            String path = Json.str(operation, "path");
            String regex = "^" + path.replaceAll("\\{[^}]+\\}", "([^/]+)") + "$";
            Route route = new Route(Json.str(operation, "operationId"), Json.str(operation, "method"),
                    Pattern.compile(regex));
            for (Object parameter : Json.arr(operation, "queryParameters")) {
                Map<String, Object> p = Json.object(parameter);
                String name = Json.str(p, "name");
                route.knownParams.add(name);
                if (Boolean.TRUE.equals(p.get("required"))) {
                    route.requiredParams.add(name);
                }
            }
            routes.add(route);
        }
        if (routes.isEmpty()) {
            throw new IOException("contract declared no operations: " + contractFile);
        }
    }

    /* ---------------------------------------------------------------- lifecycle */

    public void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.setExecutor(null);
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
        }
    }

    public String baseUrl() {
        return baseUrl;
    }

    public String latestApiVersion() {
        return latestApiVersion;
    }

    /** The request log, oldest first. */
    public List<Recorded> requestLog() {
        synchronized (log) {
            return new ArrayList<>(log);
        }
    }

    /** The password the cloud account authenticates with right now. */
    public String currentPassword() {
        return accountPassword;
    }

    public String accountName() {
        return ACCOUNT_NAME;
    }

    public String accountHostName() {
        return ACCOUNT_HOSTNAME;
    }

    public String accountUsername() {
        return ACCOUNT_USERNAME;
    }

    public String accountDcid() {
        return ACCOUNT_DCID;
    }

    /** The regions the client is expected to carry forward, as {name, externalRegionId} pairs. */
    public List<Map<String, Object>> expectedRegionSpecifications() {
        List<Map<String, Object>> result = new ArrayList<>();
        for (String[] region : REGIONS) {
            result.add(Json.map("name", region[1], "externalRegionId", region[0]));
        }
        return result;
    }

    public void writeRequestLog(Path file) throws IOException {
        List<Object> entries = new ArrayList<>();
        for (Recorded r : requestLog()) {
            entries.add(Json.map(
                    "sequence", (long) r.sequence,
                    "method", r.method,
                    "path", r.path,
                    "query", r.rawQuery == null ? "" : r.rawQuery,
                    "contentType", r.header("content-type") == null ? "" : r.header("content-type"),
                    "body", r.body == null ? "" : r.body,
                    "responseStatus", (long) r.responseStatus));
        }
        Files.createDirectories(file.getParent());
        Files.writeString(file, Json.write(Json.map("requests", entries)));
    }

    /* ---------------------------------------------------------------- dispatch */

    private void handle(HttpExchange exchange) throws IOException {
        Recorded recorded = record(exchange);
        try {
            respond(exchange, recorded);
        } catch (RuntimeException failure) {
            send(exchange, recorded, 500, error(500, "mock failure: " + failure));
        } finally {
            exchange.close();
        }
    }

    private Recorded record(HttpExchange exchange) throws IOException {
        Map<String, String> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((name, values) ->
                headers.put(name.toLowerCase(Locale.ROOT), String.join(",", values)));
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String body;
        try (InputStream in = exchange.getRequestBody()) {
            body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
        Recorded recorded = new Recorded(log.size() + 1, exchange.getRequestMethod(),
                exchange.getRequestURI().getPath(), rawQuery, parseQuery(rawQuery), headers, body);
        log.add(recorded);
        return recorded;
    }

    private void respond(HttpExchange exchange, Recorded r) throws IOException {
        Route route = null;
        Matcher match = null;
        boolean pathKnown = false;
        for (Route candidate : routes) {
            Matcher m = candidate.pattern.matcher(r.path);
            if (m.matches()) {
                pathKnown = true;
                if (candidate.method.equalsIgnoreCase(r.method)) {
                    route = candidate;
                    match = m;
                    break;
                }
            }
        }
        if (route == null) {
            send(exchange, r, 404, error(404, pathKnown
                    ? "method " + r.method + " is not defined for " + r.path + " by this API"
                    : "no operation is defined at " + r.path + " by this API"));
            return;
        }

        boolean patch = "PATCH".equalsIgnoreCase(route.method);
        int violation = patch ? 403 : 404;

        if (!("Bearer " + bearerToken).equals(r.header("authorization"))) {
            send(exchange, r, violation, error(violation,
                    "the Authorization header must carry the bearer access token"));
            return;
        }
        for (String name : r.query.keySet()) {
            if (!route.knownParams.contains(name)) {
                send(exchange, r, violation, error(violation, "operation " + route.operationId
                        + " declares no query parameter named '" + name + "'"));
                return;
            }
        }
        for (String name : route.requiredParams) {
            if (r.param(name) == null) {
                send(exchange, r, violation, error(violation, "operation " + route.operationId
                        + " requires the '" + name + "' query parameter"));
                return;
            }
        }
        String apiVersion = r.param("apiVersion");
        if (apiVersion != null && !supportedApiVersions.contains(apiVersion)) {
            send(exchange, r, violation, error(violation,
                    "apiVersion '" + apiVersion + "' is not supported by this deployment"));
            return;
        }

        switch (route.operationId) {
            case "getAbout" -> send(exchange, r, 200, about());
            case "getVSphereCloudAccount" -> getCloudAccount(exchange, r, match.group(1));
            case "updateVSphereCloudAccountAsync" -> patchCloudAccount(exchange, r, match.group(1));
            case "getRequestTrackers" -> getRequestTrackers(exchange, r);
            case "getRequestTracker" -> getRequestTracker(exchange, r, match.group(1));
            default -> send(exchange, r, 404, error(404, "unroutable operation " + route.operationId));
        }
    }

    /* ---------------------------------------------------------------- operations */

    private Map<String, Object> about() {
        List<Object> supported = new ArrayList<>();
        for (String version : supportedApiVersions) {
            supported.add(Json.map(
                    "apiVersion", version,
                    "documentationLink", "https://" + "vcfa.rainpole.local/iaas/api/docs/" + version));
        }
        return Json.map("supportedApis", supported, "latestApiVersion", latestApiVersion);
    }

    private void getCloudAccount(HttpExchange exchange, Recorded r, String id) throws IOException {
        if (!CLOUD_ACCOUNT_ID.equals(id)) {
            send(exchange, r, 404, error(404, "no vSphere cloud account with id " + id));
            return;
        }
        List<Object> enabledRegions = new ArrayList<>();
        for (String[] region : REGIONS) {
            enabledRegions.add(Json.map(
                    "id", region[2],
                    "createdAt", CREATED_AT,
                    "updatedAt", UPDATED_AT,
                    "owner", OWNER,
                    "ownerType", "user",
                    "orgId", ORG_ID,
                    "_links", Json.map("self", Json.map("href", "/iaas/api/regions/" + region[2])),
                    "externalRegionId", region[0],
                    "name", region[1],
                    "cloudAccountId", CLOUD_ACCOUNT_ID));
        }
        send(exchange, r, 200, Json.map(
                "id", CLOUD_ACCOUNT_ID,
                "createdAt", CREATED_AT,
                "updatedAt", UPDATED_AT,
                "owner", OWNER,
                "ownerType", "user",
                "orgId", ORG_ID,
                "_links", Json.map("self",
                        Json.map("href", "/iaas/api/cloud-accounts-vsphere/" + CLOUD_ACCOUNT_ID)),
                "name", ACCOUNT_NAME,
                "hostName", ACCOUNT_HOSTNAME,
                "username", ACCOUNT_USERNAME,
                "dcid", ACCOUNT_DCID,
                "customProperties", Json.map(
                        "isExternal", "false",
                        "privateKeyId", ACCOUNT_USERNAME),
                "enabledRegions", enabledRegions));
    }

    private void patchCloudAccount(HttpExchange exchange, Recorded r, String id) throws IOException {
        if (!CLOUD_ACCOUNT_ID.equals(id)) {
            send(exchange, r, 404, error(404, "no vSphere cloud account with id " + id));
            return;
        }
        String contentType = r.header("content-type");
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            send(exchange, r, 403, error(403, "the request body must be sent as application/json"));
            return;
        }
        Map<String, Object> body;
        try {
            body = Json.object(Json.parse(r.body));
        } catch (RuntimeException malformed) {
            send(exchange, r, 403, error(403, "the request body is not a JSON object: " + malformed.getMessage()));
            return;
        }
        for (String required : List.of("name", "hostName", "regions")) {
            if (!body.containsKey(required)) {
                send(exchange, r, 403, error(403,
                        "UpdateCloudAccountVsphereSpecification requires the '" + required + "' field"));
                return;
            }
        }
        if (!ACCOUNT_HOSTNAME.equals(Json.str(body, "hostName"))) {
            send(exchange, r, 403, error(403, "hostName does not match the endpoint this cloud account "
                    + "was registered against (" + ACCOUNT_HOSTNAME + ")"));
            return;
        }
        if (Json.arr(body, "regions").isEmpty()) {
            send(exchange, r, 403, error(403, "regions must list the regions provisioning stays enabled on; "
                    + "an empty set would disable the cloud account"));
            return;
        }
        int inflight = inflightRequestCount();
        if (inflight > 0) {
            send(exchange, r, 403, error(403, "credential update rejected: " + inflight
                    + " request(s) are still in progress against this cloud account and would be "
                    + "stranded on the previous secret; drain them before rotating"));
            return;
        }
        if (rotationRequested) {
            send(exchange, r, 403, error(403,
                    "a credential update is already tracked as " + ROTATION_REQUEST_ID));
            return;
        }
        rotationRequested = true;
        pendingPassword = Json.str(body, "password");
        send(exchange, r, 202, tracker(ROTATION_REQUEST_ID, "INPROGRESS", 0,
                "Update cloud account " + ACCOUNT_NAME, null));
    }

    private void getRequestTrackers(HttpExchange exchange, Recorded r) throws IOException {
        observeDrain();
        List<Object> content = new ArrayList<>();
        content.add(inflightTracker());
        content.add(secondInflightTracker());
        content.add(tracker(SETTLED_REQUEST_ID, "FINISHED", 100, "Provision machine web-02",
                Json.list("/iaas/api/machines/mcm-8812")));
        if (rotationRequested) {
            content.add(rotationTracker());
        }
        send(exchange, r, 200, Json.map(
                "content", content,
                "totalElements", (long) content.size(),
                "numberOfElements", (long) content.size()));
    }

    private void getRequestTracker(HttpExchange exchange, Recorded r, String id) throws IOException {
        switch (id) {
            case INFLIGHT_REQUEST_ID -> {
                observeDrain();
                send(exchange, r, 200, inflightTracker());
            }
            case SECOND_INFLIGHT_REQUEST_ID -> {
                observeDrain();
                send(exchange, r, 200, secondInflightTracker());
            }
            case SETTLED_REQUEST_ID -> send(exchange, r, 200,
                    tracker(SETTLED_REQUEST_ID, "FINISHED", 100, "Provision machine web-02",
                            Json.list("/iaas/api/machines/mcm-8812")));
            case ROTATION_REQUEST_ID -> {
                if (!rotationRequested) {
                    send(exchange, r, 404, error(404, "no request with id " + id));
                    return;
                }
                send(exchange, r, 200, rotationTracker());
            }
            default -> send(exchange, r, 404, error(404, "no request with id " + id));
        }
    }

    /* ---------------------------------------------------------------- state machine */

    /**
     * Counts one observation of the in-flight provisioning request, whether it was made through the
     * collection or through the single-request operation.
     */
    private void observeDrain() {
        if (drainObservations < DRAIN_POLLS_REQUIRED) {
            drainObservations++;
        }
    }

    private boolean firstInflightSettled() {
        return drainObservations >= DRAIN_POLLS_REQUIRED - 1;
    }

    private boolean secondInflightSettled() {
        return drainObservations >= DRAIN_POLLS_REQUIRED;
    }

    private int inflightRequestCount() {
        int count = 0;
        if (!firstInflightSettled()) {
            count++;
        }
        if (!secondInflightSettled()) {
            count++;
        }
        return count;
    }

    private Map<String, Object> inflightTracker() {
        return firstInflightSettled()
                ? tracker(INFLIGHT_REQUEST_ID, "FINISHED", 100, "Provision machine web-01",
                        Json.list("/iaas/api/machines/mcm-4471"))
                : tracker(INFLIGHT_REQUEST_ID, "INPROGRESS", 40, "Provision machine web-01", null);
    }

    private Map<String, Object> secondInflightTracker() {
        return secondInflightSettled()
                ? tracker(SECOND_INFLIGHT_REQUEST_ID, "FINISHED", 100, "Provision machine web-03",
                        Json.list("/iaas/api/machines/mcm-9983"))
                : tracker(SECOND_INFLIGHT_REQUEST_ID, "INPROGRESS", 70,
                        "Provision machine web-03", null);
    }

    /**
     * Any observation of the rotation request advances it, whether it was read through the single
     * request operation or found in the collection. The credential is applied when it settles.
     */
    private Map<String, Object> rotationTracker() {
        if (rotationObservations < ROTATION_POLLS_REQUIRED) {
            rotationObservations++;
        }
        if (rotationObservations >= ROTATION_POLLS_REQUIRED) {
            if (rotationShouldFail) {
                Map<String, Object> failed = tracker(ROTATION_REQUEST_ID, "FAILED", 100,
                        "Update cloud account " + ACCOUNT_NAME, null);
                failed.put("message", "The replacement credential was rejected by vSphere.");
                return failed;
            }
            if (!rotationApplied) {
                rotationApplied = true;
                if (pendingPassword != null) {
                    accountPassword = pendingPassword;
                }
            }
            return tracker(ROTATION_REQUEST_ID, "FINISHED", 100, "Update cloud account " + ACCOUNT_NAME,
                    Json.list("/iaas/api/cloud-accounts-vsphere/" + CLOUD_ACCOUNT_ID));
        }
        int progress = rotationObservations <= 1 ? 0 : 60;
        return tracker(ROTATION_REQUEST_ID, "INPROGRESS", progress,
                "Update cloud account " + ACCOUNT_NAME, null);
    }

    /* ---------------------------------------------------------------- plumbing */

    private Map<String, Object> tracker(String id, String status, int progress, String name,
                                        List<Object> resources) {
        Map<String, Object> body = Json.map(
                "progress", (long) progress,
                "status", status,
                "name", name,
                "id", id,
                "selfLink", "/iaas/api/request-tracker/" + id);
        if (resources != null) {
            body.put("resources", resources);
        }
        if ("INPROGRESS".equals(status)) {
            body.put("message", "The request is being processed.");
        }
        return body;
    }

    private Map<String, Object> error(int status, String message) {
        return Json.map(
                "message", message,
                "statusCode", (long) status,
                "errorCode", (long) (status * 100),
                "documentKind", "com:vmware:pallas:common:ServiceErrorResponse",
                "serverErrorId", "mock-" + status);
    }

    private void send(HttpExchange exchange, Recorded r, int status, Map<String, Object> body)
            throws IOException {
        byte[] payload = Json.write(body).getBytes(StandardCharsets.UTF_8);
        r.responseStatus = status;
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, payload.length);
        exchange.getResponseBody().write(payload);
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> result = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return result;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String name = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            result.computeIfAbsent(decode(name), k -> new ArrayList<>()).add(decode(value));
        }
        return result;
    }

    private static String decode(String value) {
        return URLDecoder.decode(value, StandardCharsets.UTF_8);
    }
}
