import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A loopback stand-in for an SDDC Manager appliance, pinned to {@code docs/contract.json}.
 *
 * <p>It builds its routing table from the contract, so it serves only the five operations the
 * contract names and answers anything else with 404. Request bodies and query strings are validated
 * against the contract's schemas: a member that is present but null or empty is rejected with 400
 * rather than quietly accepted. Every exchange is appended to a JSON Lines request log that the
 * harness reads back afterwards.
 *
 * <p>The access token minted by {@code createToken} expires at a fixed point in the run: the moment
 * the appliance is holding {@value #GATE_PARTIES} distinct {@code getCredentials} requests at once,
 * all of them are answered 401 and that token is dead. That is the only way the token ever expires,
 * which makes the expiry deterministic and puts it exactly where several requests are in flight.
 */
public final class MockSddcManager {

    // ------------------------------------------------------------- fixtures

    public static final String USERNAME = "administrator@vsphere.local";
    public static final String PASSWORD = "VMw@re1!SecureMe";

    public static final String REFRESH_TOKEN_ID = "0f3a5c71-8b24-4e69-9d05-7c1e2f8a4630";
    public static final String CREDENTIALS_TASK_ID = "c4e97a02-1b58-4d36-8f70-2a6b9c130d45";

    public static final List<String> HOSTS = List.of(
            "esxi-03.wld01.vcf.example.com",
            "esxi-01.wld01.vcf.example.com",
            "esxi-02.wld01.vcf.example.com");

    /** resource name -> resource id, as the appliance reports it. */
    public static final Map<String, String> RESOURCE_IDS = Map.of(
            HOSTS.get(0), "9c1f0a34-5d78-4b62-8e01-a3f7c2d94b15",
            HOSTS.get(1), "b7e42d18-0c93-4a5f-91d6-6e08f31c7a24",
            HOSTS.get(2), "4a8d63f0-e215-4c79-b3a8-52c9017de6b3");

    /** resource name -> id of the SSH credential whose accountType is USER. */
    public static final Map<String, String> USER_CREDENTIAL_IDS = Map.of(
            HOSTS.get(0), "3f8eb029-5164-4da3-97f2-1e95036a4826",
            HOSTS.get(1), "1d6c9e07-3f42-4b81-a5d0-9c73e18f2604",
            HOSTS.get(2), "2e7daf18-4053-4c92-b6e1-0d84f2903715");

    /** resource name -> id of another SSH USER credential that must not be rotated. */
    public static final Map<String, String> DECOY_USER_CREDENTIAL_IDS = Map.of(
            HOSTS.get(0), "8d3e4f50-91a2-43b4-c536-d7e8f9012a34",
            HOSTS.get(1), "9e4f5061-a2b3-44c5-d647-e8f9012a3b45",
            HOSTS.get(2), "af506172-b3c4-45d6-e758-f9012a3b4c56");

    /** resource name -> id of the SSH credential whose accountType is SERVICE. */
    public static final Map<String, String> SERVICE_CREDENTIAL_IDS = Map.of(
            HOSTS.get(0), "5a0b1c2d-6e7f-4081-9203-a4b5c6d7e8f1",
            HOSTS.get(1), "6b1c2d3e-7f80-4192-a314-b5c6d7e8f902",
            HOSTS.get(2), "7c2d3e4f-8091-42a3-b425-c6d7e8f90a13");

    public static final String USER_ACCOUNT_NAME = "root";
    public static final String DECOY_USER_ACCOUNT_NAME = "break-glass-admin";
    public static final String SERVICE_ACCOUNT_NAME = "svc-vcf-esxi";
    public static final String RESOURCE_TYPE = "ESXI";
    public static final String CREDENTIAL_TYPE = "SSH";

    /** How many distinct in-flight getCredentials requests trip the access-token expiry. */
    public static final int GATE_PARTIES = 3;

    /** How many polls of the credentials task return IN_PROGRESS before it settles. */
    public static final int POLLS_BEFORE_TERMINAL = 2;

    private static final long GATE_TIMEOUT_NANOS = 12_000_000_000L;
    private static final Set<String> OPERATION_TYPES =
            Set.of("UPDATE", "ROTATE", "REMEDIATE", "UPDATE_AUTO_ROTATE_POLICY");

    // ---------------------------------------------------------------- state

    private final Map<String, Object> contract;
    private final List<Route> routes = new ArrayList<>();
    private final Set<String> bearerOperations;
    private final Set<String> unauthenticatedOperations;
    private final Path logPath;

    private final HttpServer server;
    private final ExecutorService workers;

    private final AtomicInteger sequence = new AtomicInteger();
    private final AtomicInteger eventSequence = new AtomicInteger();
    private final AtomicInteger taskPolls = new AtomicInteger();
    private final Object logLock = new Object();

    private final List<String> issuedTokens = new ArrayList<>();
    private final Set<Integer> expiredGenerations = new LinkedHashSet<>();

    private final Object gateLock = new Object();
    private final Set<String> gateArrivals = new LinkedHashSet<>();
    private boolean gateReleased;
    private boolean gateTimedOut;

    public MockSddcManager(Path contractPath, Path logPath) throws IOException {
        this.contract = Json.asObject(Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8)));
        this.logPath = logPath;
        Files.writeString(logPath, "", StandardCharsets.UTF_8);

        for (Object raw : Json.asArray(contract.get("operations"))) {
            Map<String, Object> operation = Json.asObject(raw);
            routes.add(new Route(
                    Json.asString(operation.get("operationId")),
                    Json.asString(operation.get("method")),
                    Json.asString(operation.get("path"))));
        }
        Map<String, Object> transport = Json.asObject(contract.get("transport"));
        this.bearerOperations = new LinkedHashSet<>(stringList(transport.get("appliesTo")));
        this.unauthenticatedOperations = new LinkedHashSet<>(stringList(transport.get("omittedOn")));

        this.server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.workers = Executors.newFixedThreadPool(8, runnable -> {
            Thread thread = new Thread(runnable, "sddc-mock");
            thread.setDaemon(true);
            return thread;
        });
        this.server.setExecutor(workers);
        this.server.createContext("/", this::dispatch);
    }

    public void start() {
        server.start();
    }

    public void stop() {
        server.stop(0);
        workers.shutdownNow();
    }

    public String baseUrl() {
        return "http://" + server.getAddress().getAddress().getHostAddress() + ":" + server.getAddress().getPort();
    }

    public Path logPath() {
        return logPath;
    }

    /** True when the appliance gave up waiting for concurrent getCredentials requests. */
    public boolean gateTimedOut() {
        synchronized (gateLock) {
            return gateTimedOut;
        }
    }

    /** The distinct getCredentials query strings that were in flight when the gate gave up. */
    public List<String> gateArrivals() {
        synchronized (gateLock) {
            return List.copyOf(gateArrivals);
        }
    }

    // ------------------------------------------------------------ dispatch

    private void dispatch(HttpExchange exchange) throws IOException {
        Entry entry = new Entry(sequence.incrementAndGet(), eventSequence.incrementAndGet());
        try {
            entry.method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
            entry.path = exchange.getRequestURI().getPath();
            entry.rawQuery = exchange.getRequestURI().getRawQuery();
            entry.authorization = exchange.getRequestHeaders().getFirst("Authorization");
            entry.contentType = exchange.getRequestHeaders().getFirst("Content-Type");
            entry.accept = exchange.getRequestHeaders().getFirst("Accept");
            try (InputStream in = exchange.getRequestBody()) {
                entry.body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            }
            handle(exchange, entry);
        } catch (RuntimeException failure) {
            send(exchange, entry, 500, error("INTERNAL_ERROR", "SERVER_ERROR",
                    "The stand-in appliance failed: " + failure));
        } finally {
            exchange.close();
        }
    }

    private void handle(HttpExchange exchange, Entry entry) throws IOException {
        Route route = null;
        Map<String, String> pathValues = Map.of();
        for (Route candidate : routes) {
            Matcher matcher = candidate.pattern.matcher(entry.path);
            if (matcher.matches()) {
                if (!candidate.method.equals(entry.method)) {
                    continue;
                }
                route = candidate;
                Map<String, String> values = new LinkedHashMap<>();
                for (int i = 0; i < candidate.parameterNames.size(); i++) {
                    values.put(candidate.parameterNames.get(i), matcher.group(i + 1));
                }
                pathValues = values;
                break;
            }
        }
        if (route == null) {
            send(exchange, entry, 404, error("NOT_FOUND", "NOT_FOUND",
                    "docs/contract.json names no operation serving " + entry.method + " " + entry.path));
            return;
        }
        entry.operationId = route.operationId;

        Map<String, String> query;
        try {
            query = parseQuery(entry.rawQuery);
        } catch (IllegalArgumentException bad) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED", bad.getMessage()));
            return;
        }

        String authProblem = checkAuthorization(route.operationId, entry.authorization);
        if (authProblem != null) {
            int status = unauthenticatedOperations.contains(route.operationId) ? 400 : 401;
            send(exchange, entry, status, error(status == 401 ? "UNAUTHORIZED" : "BAD_REQUEST",
                    status == 401 ? "UNAUTHENTICATED" : "VALIDATION_FAILED", authProblem));
            return;
        }

        switch (route.operationId) {
            case "createToken" -> createToken(exchange, entry, query);
            case "refreshAccessToken" -> refreshAccessToken(exchange, entry, query);
            case "getCredentials" -> getCredentials(exchange, entry, query);
            case "updateOrRotatePasswords" -> updateOrRotatePasswords(exchange, entry, query);
            case "getCredentialsTask" -> getCredentialsTask(exchange, entry, query, pathValues.get("id"));
            default -> send(exchange, entry, 501, error("NOT_IMPLEMENTED", "SERVER_ERROR",
                    "The stand-in does not implement " + route.operationId));
        }
    }

    // ---------------------------------------------------------- operations

    private void createToken(HttpExchange exchange, Entry entry, Map<String, String> query) throws IOException {
        if (!query.isEmpty()) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "createToken takes no query parameters but received " + query.keySet()));
            return;
        }
        Object body = readJson(exchange, entry);
        if (body == NOT_JSON) {
            return;
        }
        List<String> problems = new ArrayList<>();
        validate(body, schema("TokenCreationSpec"), "body", problems);
        if (!problems.isEmpty()) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED", String.join("; ", problems)));
            return;
        }
        String username = Json.getString(body, "username");
        String password = Json.getString(body, "password");
        if (!USERNAME.equals(username) || !PASSWORD.equals(password)) {
            send(exchange, entry, 401, error("UNAUTHORIZED", "UNAUTHENTICATED",
                    "The supplied credentials were rejected by the appliance."));
            return;
        }
        String token = mintAccessToken();
        send(exchange, entry, 201, Json.write(Json.object(
                "accessToken", token,
                "refreshToken", Json.object("id", REFRESH_TOKEN_ID))));
    }

    private void refreshAccessToken(HttpExchange exchange, Entry entry, Map<String, String> query) throws IOException {
        if (!query.isEmpty()) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "refreshAccessToken takes no query parameters but received " + query.keySet()));
            return;
        }
        Object body = readJson(exchange, entry);
        if (body == NOT_JSON) {
            return;
        }
        if (!(body instanceof String supplied)) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "The refreshAccessToken body is a bare JSON string holding the refresh token id, "
                            + "but the request carried " + Json.describe(body) + "."));
            return;
        }
        if (!REFRESH_TOKEN_ID.equals(supplied)) {
            send(exchange, entry, 404, error("NOT_FOUND", "NOT_FOUND",
                    "No refresh token with id " + supplied + " is known to the appliance."));
            return;
        }
        String token;
        synchronized (issuedTokens) {
            expiredGenerations.add(issuedTokens.size());
            token = mintAccessToken();
        }
        send(exchange, entry, 200, Json.write(token));
    }

    private void getCredentials(HttpExchange exchange, Entry entry, Map<String, String> query) throws IOException {
        String parameterProblem = checkQueryParameters("getCredentials", query);
        if (parameterProblem != null) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED", parameterProblem));
            return;
        }
        if (isFirstGeneration(entry.authorization) && passThroughGate(entry.rawQuery)) {
            send(exchange, entry, 401, error("UNAUTHORIZED", "UNAUTHENTICATED",
                    "The access token has expired. Exchange the refresh token for a new access token."));
            return;
        }
        if (gateTimedOut()) {
            send(exchange, entry, 503, error("SERVICE_UNAVAILABLE", "TIMEOUT",
                    "The appliance waited " + (GATE_TIMEOUT_NANOS / 1_000_000_000L) + "s for "
                            + GATE_PARTIES + " concurrent getCredentials requests and saw only "
                            + gateArrivals().size() + ". Issue the lookups so that all of them are in "
                            + "flight at the same time."));
            return;
        }

        String resourceName = query.get("resourceName");
        String accountType = query.get("accountType");
        List<Object> elements = new ArrayList<>();
        for (String host : HOSTS) {
            if (resourceName != null && !resourceName.equals(host)) {
                continue;
            }
            if (accountType == null || accountType.equals("USER")) {
                elements.add(credential(host, DECOY_USER_CREDENTIAL_IDS.get(host),
                        "USER", DECOY_USER_ACCOUNT_NAME));
                elements.add(credential(host, USER_CREDENTIAL_IDS.get(host), "USER", USER_ACCOUNT_NAME));
            }
            if (accountType == null || accountType.equals("SERVICE")) {
                elements.add(credential(host, SERVICE_CREDENTIAL_IDS.get(host), "SERVICE", SERVICE_ACCOUNT_NAME));
            }
        }
        send(exchange, entry, 200, Json.write(Json.object(
                "elements", elements,
                "pageMetadata", Json.object(
                        "pageNumber", 0L,
                        "pageSize", (long) elements.size(),
                        "totalElements", (long) elements.size(),
                        "totalPages", 1L))));
    }

    private void updateOrRotatePasswords(HttpExchange exchange, Entry entry, Map<String, String> query)
            throws IOException {
        if (!query.isEmpty()) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "updateOrRotatePasswords takes no query parameters but received " + query.keySet()));
            return;
        }
        Object body = readJson(exchange, entry);
        if (body == NOT_JSON) {
            return;
        }
        List<String> problems = new ArrayList<>();
        validate(body, schema("CredentialsUpdateSpec"), "body", problems);
        if (!problems.isEmpty()) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED", String.join("; ", problems)));
            return;
        }
        String operationType = Json.getString(body, "operationType");
        if (!OPERATION_TYPES.contains(operationType)) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "operationType must be one among " + OPERATION_TYPES + " but was " + operationType));
            return;
        }
        for (Object rawElement : Json.asArray(Json.get(body, "elements"))) {
            Map<String, Object> element = Json.asObject(rawElement);
            String resourceId = Json.getString(element, "resourceId");
            String resourceName = Json.getString(element, "resourceName");
            if (resourceId == null && resourceName == null) {
                send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                        "Each element must identify its resource by resourceId or resourceName."));
                return;
            }
            if (resourceId != null && !RESOURCE_IDS.containsValue(resourceId)) {
                send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                        "No resource with id " + resourceId + " is managed by this appliance."));
                return;
            }
            for (Object rawCredential : Json.asArray(element.get("credentials"))) {
                Map<String, Object> credential = Json.asObject(rawCredential);
                boolean carriesPassword = credential.containsKey("password");
                if ("ROTATE".equals(operationType) && carriesPassword) {
                    send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                            "A ROTATE operation lets the appliance generate the new secret, so the "
                                    + "credential for " + credential.get("username")
                                    + " must not carry a password member."));
                    return;
                }
                if ("UPDATE".equals(operationType) && !carriesPassword) {
                    send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                            "An UPDATE operation supplies the new secret, so the credential for "
                                    + credential.get("username") + " must carry a password member."));
                    return;
                }
            }
        }
        send(exchange, entry, 202, Json.write(Json.object(
                "id", CREDENTIALS_TASK_ID,
                "name", "Rotating passwords for 3 resources",
                "type", "CREDENTIALS_ROTATE",
                "status", "IN_PROGRESS",
                "creationTimestamp", "2026-03-04T10:22:41.118Z",
                "isCancellable", Boolean.FALSE,
                "isRetryable", Boolean.FALSE)));
    }

    private void getCredentialsTask(HttpExchange exchange, Entry entry, Map<String, String> query, String id)
            throws IOException {
        if (!query.isEmpty()) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "getCredentialsTask takes no query parameters but received " + query.keySet()));
            return;
        }
        if (!CREDENTIALS_TASK_ID.equals(id)) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "No credentials task with id " + id + " exists on this appliance."));
            return;
        }
        boolean terminal = taskPolls.incrementAndGet() > POLLS_BEFORE_TERMINAL;
        String status = terminal ? "SUCCESSFUL" : "IN_PROGRESS";
        List<Object> subTasks = new ArrayList<>();
        for (String host : HOSTS) {
            Map<String, Object> subTask = Json.object(
                    "id", USER_CREDENTIAL_IDS.get(host),
                    "resourceName", host,
                    "name", "ROTATE_PASSWORD",
                    "description", "Rotate the " + CREDENTIAL_TYPE + " password for " + USER_ACCOUNT_NAME
                            + " on " + host,
                    "creationTimestamp", "2026-03-04T10:22:41.204Z",
                    "status", status,
                    "username", USER_ACCOUNT_NAME,
                    "credentialType", CREDENTIAL_TYPE,
                    "entityType", RESOURCE_TYPE);
            if (terminal) {
                subTask.put("completionTimestamp", "2026-03-04T10:23:06.771Z");
            }
            subTasks.add(subTask);
        }
        Map<String, Object> task = Json.object(
                "id", CREDENTIALS_TASK_ID,
                "name", "Rotating passwords for 3 resources",
                "type", "CREDENTIALS_ROTATE",
                "creationTimestamp", "2026-03-04T10:22:41.118Z",
                "status", status,
                "subTasks", subTasks,
                "isAutoRotate", Boolean.FALSE);
        if (terminal) {
            task.put("completionTimestamp", "2026-03-04T10:23:06.771Z");
        }
        send(exchange, entry, 200, Json.write(task));
    }

    // ------------------------------------------------------------ helpers

    private Map<String, Object> credential(String host, String id, String accountType, String username) {
        return Json.object(
                "id", id,
                "credentialType", CREDENTIAL_TYPE,
                "accountType", accountType,
                "username", username,
                "creationTimestamp", "2025-11-03T08:14:22.517Z",
                "modificationTimestamp", "2026-02-17T11:02:49.883Z",
                "expiry", Json.object(
                        "connectivityStatus", "REACHABLE",
                        "expiryDate", "2026-04-02T00:00:00.000Z",
                        "lastCheckedDate", "2026-03-04T02:00:00.000Z",
                        "status", "ACTIVE"),
                "resource", Json.object(
                        "resourceId", RESOURCE_IDS.get(host),
                        "resourceName", host,
                        "resourceType", RESOURCE_TYPE,
                        "domainNames", List.of("wld01")));
    }

    private String mintAccessToken() {
        synchronized (issuedTokens) {
            String token = "sddc-access-token-generation-" + (issuedTokens.size() + 1);
            issuedTokens.add(token);
            return token;
        }
    }

    private boolean isFirstGeneration(String authorization) {
        synchronized (issuedTokens) {
            return !issuedTokens.isEmpty()
                    && authorization != null
                    && authorization.equals("Bearer " + issuedTokens.get(0));
        }
    }

    /** Blocks until enough distinct lookups are in flight; returns true when the gate released. */
    private boolean passThroughGate(String rawQuery) {
        synchronized (gateLock) {
            if (gateReleased) {
                return !gateTimedOut;
            }
            gateArrivals.add(String.valueOf(rawQuery));
            if (gateArrivals.size() >= GATE_PARTIES) {
                gateReleased = true;
                synchronized (issuedTokens) {
                    expiredGenerations.add(1);
                }
                gateLock.notifyAll();
                return true;
            }
            long deadline = System.nanoTime() + GATE_TIMEOUT_NANOS;
            while (!gateReleased) {
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) {
                    gateTimedOut = true;
                    gateReleased = true;
                    gateLock.notifyAll();
                    return false;
                }
                try {
                    gateLock.wait(remaining / 1_000_000L, (int) (remaining % 1_000_000L));
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    gateTimedOut = true;
                    gateReleased = true;
                    gateLock.notifyAll();
                    return false;
                }
            }
            return !gateTimedOut;
        }
    }

    private String checkAuthorization(String operationId, String authorization) {
        if (unauthenticatedOperations.contains(operationId)) {
            return authorization == null ? null
                    : "The token operations mint or replace an access token, so they must be sent "
                    + "without an Authorization header, but this request carried one.";
        }
        if (!bearerOperations.contains(operationId)) {
            return null;
        }
        if (authorization == null) {
            return "This operation requires an Authorization: Bearer <accessToken> header.";
        }
        if (!authorization.startsWith("Bearer ")) {
            return "The Authorization header must use the Bearer scheme.";
        }
        String token = authorization.substring("Bearer ".length());
        synchronized (issuedTokens) {
            int index = issuedTokens.indexOf(token);
            if (index < 0) {
                return "The access token is not known to this appliance.";
            }
            if (expiredGenerations.contains(index + 1) || index != issuedTokens.size() - 1) {
                return "The access token has expired. Exchange the refresh token for a new access token.";
            }
        }
        return null;
    }

    private String checkQueryParameters(String operationId, Map<String, String> query) {
        Set<String> declared = new LinkedHashSet<>();
        for (Object raw : Json.asArray(contract.get("operations"))) {
            Map<String, Object> operation = Json.asObject(raw);
            if (!operationId.equals(operation.get("operationId"))) {
                continue;
            }
            Object parameters = operation.get("queryParameters");
            if (parameters != null) {
                for (Object parameter : Json.asArray(parameters)) {
                    declared.add(Json.asString(Json.get(parameter, "name")));
                }
            }
        }
        for (Map.Entry<String, String> parameter : query.entrySet()) {
            if (!declared.contains(parameter.getKey())) {
                return "docs/contract.json declares no query parameter named " + parameter.getKey()
                        + " on " + operationId + "; declared parameters are " + declared;
            }
            if (parameter.getValue().isEmpty()) {
                return "Query parameter " + parameter.getKey() + " was sent with an empty value. "
                        + "A parameter you have no value for is omitted, not sent empty.";
            }
        }
        return null;
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> query = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return query;
        }
        for (String pair : rawQuery.split("&", -1)) {
            int split = pair.indexOf('=');
            if (split < 0) {
                throw new IllegalArgumentException("Query parameter " + pair + " has no value.");
            }
            String name = URLDecoder.decode(pair.substring(0, split), StandardCharsets.UTF_8);
            String value = URLDecoder.decode(pair.substring(split + 1), StandardCharsets.UTF_8);
            if (query.put(name, value) != null) {
                throw new IllegalArgumentException("Query parameter " + name + " was sent more than once.");
            }
        }
        return query;
    }

    private static final Object NOT_JSON = new Object();

    private Object readJson(HttpExchange exchange, Entry entry) throws IOException {
        String contentType = entry.contentType == null ? "" : entry.contentType.toLowerCase(Locale.ROOT);
        if (!contentType.startsWith("application/json")) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "The request body must be sent as application/json but Content-Type was "
                            + (entry.contentType == null ? "absent" : entry.contentType) + "."));
            return NOT_JSON;
        }
        try {
            return Json.parse(entry.body);
        } catch (RuntimeException malformed) {
            send(exchange, entry, 400, error("BAD_REQUEST", "VALIDATION_FAILED",
                    "The request body is not valid JSON: " + malformed.getMessage()));
            return NOT_JSON;
        }
    }

    private Map<String, Object> schema(String name) {
        return Json.asObject(Json.get(contract.get("schemas"), name));
    }

    /** Validates a request payload against a contract schema, collecting every problem found. */
    private void validate(Object value, Map<String, Object> schema, String location, List<String> problems) {
        Object ref = schema.get("$ref");
        if (ref instanceof String refName) {
            validate(value, schema(refName), location, problems);
            return;
        }
        String type = Json.getString(schema, "type");
        if (value == null) {
            problems.add(location + " is null; a value you do not have is omitted, not sent as null");
            return;
        }
        if ("object".equals(type)) {
            if (!(value instanceof Map)) {
                problems.add(location + " must be an object but is " + Json.describe(value));
                return;
            }
            Map<String, Object> object = Json.asObject(value);
            Map<String, Object> properties = Json.asObject(schema.get("properties"));
            for (Object required : stringList(schema.get("required"))) {
                if (!object.containsKey(String.valueOf(required))) {
                    problems.add(location + " is missing the required member " + required);
                }
            }
            for (Map.Entry<String, Object> member : object.entrySet()) {
                Object property = properties.get(member.getKey());
                if (property == null) {
                    problems.add(location + " carries the member " + member.getKey()
                            + ", which the contract does not declare");
                    continue;
                }
                validate(member.getValue(), Json.asObject(property),
                        location + "." + member.getKey(), problems);
            }
            return;
        }
        if ("array".equals(type)) {
            if (!(value instanceof List)) {
                problems.add(location + " must be an array but is " + Json.describe(value));
                return;
            }
            List<Object> list = Json.asArray(value);
            if (list.isEmpty()) {
                problems.add(location + " is an empty array");
                return;
            }
            Object items = schema.get("items");
            if (items != null) {
                for (int i = 0; i < list.size(); i++) {
                    validate(list.get(i), Json.asObject(items), location + "[" + i + "]", problems);
                }
            }
            return;
        }
        if ("string".equals(type)) {
            if (!(value instanceof String text)) {
                problems.add(location + " must be a string but is " + Json.describe(value));
            } else if (text.isEmpty()) {
                problems.add(location + " is an empty string; a value you do not have is omitted, "
                        + "not sent empty");
            }
            return;
        }
        if ("integer".equals(type) && !(value instanceof Long || value instanceof Integer)) {
            problems.add(location + " must be an integer but is " + Json.describe(value));
        }
        if ("boolean".equals(type) && !(value instanceof Boolean)) {
            problems.add(location + " must be a boolean but is " + Json.describe(value));
        }
    }

    private static List<String> stringList(Object value) {
        List<String> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object element : list) {
                out.add(String.valueOf(element));
            }
        }
        return out;
    }

    private static String error(String code, String type, String message) {
        return Json.write(Json.object("errorCode", code, "errorType", type, "message", message));
    }

    private void send(HttpExchange exchange, Entry entry, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        // Record when the response starts. A client cannot react to this response before this event,
        // so causal checks use deterministic event order rather than wall-clock timestamps.
        entry.status = status;
        entry.responseBody = body;
        entry.respondedOrder = eventSequence.incrementAndGet();
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
        append(entry);
    }

    private void append(Entry entry) {
        Map<String, Object> record = Json.object(
                "seq", (long) entry.seq,
                "receivedOrder", entry.receivedOrder,
                "respondedOrder", entry.respondedOrder,
                "operationId", entry.operationId,
                "method", entry.method,
                "path", entry.path,
                "rawQuery", entry.rawQuery,
                "authorization", entry.authorization,
                "contentType", entry.contentType,
                "accept", entry.accept,
                "body", entry.body,
                "status", (long) entry.status,
                "responseBody", entry.responseBody);
        synchronized (logLock) {
            try {
                Files.writeString(logPath, Json.write(record) + System.lineSeparator(),
                        StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException failure) {
                throw new UncheckedIoFailure(failure);
            }
        }
    }

    static final class UncheckedIoFailure extends RuntimeException {
        UncheckedIoFailure(IOException cause) {
            super(cause);
        }
    }

    private static final class Entry {
        final int seq;
        final long receivedOrder;
        long respondedOrder;
        String operationId;
        String method;
        String path;
        String rawQuery;
        String authorization;
        String contentType;
        String accept;
        String body;
        int status;
        String responseBody;

        Entry(int seq, long receivedOrder) {
            this.seq = seq;
            this.receivedOrder = receivedOrder;
        }
    }

    private static final class Route {
        final String operationId;
        final String method;
        final Pattern pattern;
        final List<String> parameterNames = new ArrayList<>();

        Route(String operationId, String method, String template) {
            this.operationId = operationId;
            this.method = method;
            StringBuilder regex = new StringBuilder("^");
            Matcher matcher = Pattern.compile("\\{([^}]+)}").matcher(template);
            int cursor = 0;
            while (matcher.find()) {
                regex.append(Pattern.quote(template.substring(cursor, matcher.start())));
                regex.append("([^/]+)");
                parameterNames.add(matcher.group(1));
                cursor = matcher.end();
            }
            regex.append(Pattern.quote(template.substring(cursor))).append("$");
            this.pattern = Pattern.compile(regex.toString());
        }
    }
}
