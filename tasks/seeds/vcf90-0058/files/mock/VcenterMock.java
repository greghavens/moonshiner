import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A loopback stand-in for the vSphere Automation API endpoint of a VCF 9.0
 * vCenter Server.
 *
 * The server is pinned to the contract: it reads docs/contract.json at startup
 * and exposes exactly the operations that contract names. It knows how to
 * simulate a wider set of operations than any one contract is expected to use;
 * anything the contract does not name is not routed and answers 404
 * OPERATION_NOT_FOUND. If the contract names a method/path this server has no
 * simulated behaviour for, startup fails loudly rather than inventing one.
 *
 * Every inbound request -- routed or not -- is appended to the request log as a
 * single JSON object per line.
 *
 * Usage:
 *   java VcenterMock --contract docs/contract.json --config config/lab-vcenter.json \
 *                    --fixtures mock/fixtures/inventory.json \
 *                    --log build/requests.jsonl --port-file build/mock.port
 */
public final class VcenterMock {

    private static final String SESSION_HEADER = "vmware-api-session-id";

    private final Map<String, Object> fixtures;
    private final String username;
    private final String password;
    private final String sessionToken;
    private final BufferedWriter log;

    private final Map<String, Handler> behaviours = new LinkedHashMap<>();
    private final List<Route> routes = new ArrayList<>();

    private boolean sessionIssued;
    private int seq;

    // ------------------------------------------------------------------ types

    @FunctionalInterface
    private interface Handler {
        void handle(Ctx ctx) throws IOException;
    }

    private record Route(String operationId, String method, String pathTemplate,
                         Map<String, String> query, Pattern pattern, List<String> varNames) {
    }

    private static final class Ctx {
        final HttpExchange exchange;
        final String method;
        final String fullPath;
        final String rawQuery;
        final Map<String, String> query;
        final String body;
        final Map<String, String> pathVars = new LinkedHashMap<>();
        String operationId;
        int status;

        Ctx(HttpExchange exchange, String method, String fullPath, String rawQuery,
            Map<String, String> query, String body) {
            this.exchange = exchange;
            this.method = method;
            this.fullPath = fullPath;
            this.rawQuery = rawQuery;
            this.query = query;
            this.body = body;
        }

        String vm() {
            return pathVars.get("vm");
        }
    }

    private static final class Halt extends RuntimeException {
        Halt(String message) {
            super(message);
        }
    }

    // ------------------------------------------------------------------- main

    public static void main(String[] args) throws Exception {
        Map<String, String> opts = parseArgs(args);
        Path contractPath = required(opts, "--contract");
        Path configPath = required(opts, "--config");
        Path fixturesPath = required(opts, "--fixtures");
        Path logPath = required(opts, "--log");
        Path portFile = required(opts, "--port-file");

        try {
            VcenterMock mock = new VcenterMock(configPath, fixturesPath, logPath);
            String basePath = mock.loadContract(contractPath);
            mock.start(basePath, portFile);
        } catch (Halt h) {
            System.err.println("VcenterMock: " + h.getMessage());
            System.exit(2);
        }
    }

    private VcenterMock(Path configPath, Path fixturesPath, Path logPath) throws IOException {
        Map<String, Object> config = MiniJson.asObject(MiniJson.parse(Files.readString(configPath)));
        this.username = MiniJson.asString(config.get("username"));
        this.password = MiniJson.asString(config.get("password"));
        this.fixtures = MiniJson.asObject(MiniJson.parse(Files.readString(fixturesPath)));
        this.sessionToken = MiniJson.asString(fixtures.get("session_token"));
        if (logPath.getParent() != null) {
            Files.createDirectories(logPath.getParent());
        }
        this.log = Files.newBufferedWriter(logPath, StandardCharsets.UTF_8);
        registerBehaviours();
    }

    // -------------------------------------------------------------- contract

    /** Reads the contract, wires up only the operations it names, returns its base path. */
    private String loadContract(Path contractPath) throws IOException {
        if (!Files.isRegularFile(contractPath)) {
            throw new Halt("contract not found at " + contractPath);
        }
        Map<String, Object> contract;
        try {
            contract = MiniJson.asObject(MiniJson.parse(Files.readString(contractPath)));
        } catch (RuntimeException e) {
            throw new Halt(contractPath + " is not readable as a JSON object: " + e.getMessage());
        }

        Object basePathValue = contract.get("base_path");
        if (!(basePathValue instanceof String basePath) || !basePath.startsWith("/")) {
            throw new Halt("contract is missing a \"base_path\" string beginning with '/'");
        }

        Object opsValue = contract.get("operations");
        if (!(opsValue instanceof List<?> ops) || ops.isEmpty()) {
            throw new Halt("contract is missing a non-empty \"operations\" array");
        }

        List<String> problems = new ArrayList<>();
        Set<String> seenIds = new LinkedHashSet<>();
        for (Object entry : ops) {
            Map<String, Object> op;
            try {
                op = MiniJson.asObject(entry);
            } catch (RuntimeException e) {
                problems.add("operations[] contains a non-object entry");
                continue;
            }
            String operationId = op.get("operation_id") instanceof String s ? s : null;
            String method = op.get("method") instanceof String s ? s.toUpperCase() : null;
            String path = op.get("path") instanceof String s ? s : null;
            if (operationId == null || method == null || path == null) {
                problems.add("an operations[] entry is missing \"operation_id\", \"method\" or \"path\"");
                continue;
            }
            if (!seenIds.add(operationId)) {
                problems.add("operation_id \"" + operationId + "\" appears more than once");
                continue;
            }
            if (!path.startsWith("/")) {
                problems.add(operationId + ": \"path\" must begin with '/' and must not repeat the base path");
                continue;
            }
            if (path.contains("?")) {
                problems.add(operationId + ": \"path\" must not contain a query string; use the \"query\" object");
                continue;
            }

            Map<String, String> query = new TreeMap<>();
            Object queryValue = op.get("query");
            if (queryValue instanceof Map<?, ?> qm) {
                for (Map.Entry<?, ?> e : qm.entrySet()) {
                    query.put(String.valueOf(e.getKey()), String.valueOf(e.getValue()));
                }
            } else if (queryValue != null) {
                problems.add(operationId + ": \"query\" must be an object when present");
                continue;
            }

            String key = routeKey(method, path, query);
            if (!behaviours.containsKey(key)) {
                problems.add(operationId + ": this endpoint has no simulated behaviour for \"" + key + "\"");
                continue;
            }
            routes.add(compile(operationId, method, path, query));
        }

        if (!problems.isEmpty()) {
            throw new Halt("cannot serve " + contractPath + ":\n  - " + String.join("\n  - ", problems));
        }
        return basePath.endsWith("/") ? basePath.substring(0, basePath.length() - 1) : basePath;
    }

    private static String routeKey(String method, String path, Map<String, String> query) {
        StringBuilder sb = new StringBuilder(method).append(' ').append(path);
        if (!query.isEmpty()) {
            sb.append('?');
            boolean first = true;
            for (Map.Entry<String, String> e : new TreeMap<>(query).entrySet()) {
                if (!first) {
                    sb.append('&');
                }
                first = false;
                sb.append(e.getKey()).append('=').append(e.getValue());
            }
        }
        return sb.toString();
    }

    private static Route compile(String operationId, String method, String path, Map<String, String> query) {
        List<String> varNames = new ArrayList<>();
        StringBuilder regex = new StringBuilder("^");
        Matcher m = Pattern.compile("\\{([^}]+)}").matcher(path);
        int last = 0;
        while (m.find()) {
            regex.append(Pattern.quote(path.substring(last, m.start())));
            regex.append("([^/]+)");
            varNames.add(m.group(1));
            last = m.end();
        }
        regex.append(Pattern.quote(path.substring(last))).append("$");
        return new Route(operationId, method, path, query, Pattern.compile(regex.toString()), varNames);
    }

    // ------------------------------------------------------------------ serve

    private void start(String basePath, Path portFile) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        // Single-threaded on purpose: the request log must be ordered deterministically.
        server.setExecutor(Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "vcenter-mock");
            t.setDaemon(true);
            return t;
        }));
        server.createContext("/", exchange -> {
            try {
                dispatch(basePath, exchange);
            } finally {
                exchange.close();
            }
        });
        server.start();

        int port = server.getAddress().getPort();
        Path tmp = portFile.resolveSibling(portFile.getFileName() + ".tmp");
        if (portFile.getParent() != null) {
            Files.createDirectories(portFile.getParent());
        }
        Files.writeString(tmp, Integer.toString(port));
        Files.move(tmp, portFile, StandardCopyOption.REPLACE_EXISTING);

        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            try {
                log.flush();
                log.close();
            } catch (IOException ignored) {
                // best effort
            }
            server.stop(0);
        }));

        System.out.println("VcenterMock listening on http://127.0.0.1:" + port + basePath
                + " serving " + routes.size() + " contracted operation(s)");
        System.out.flush();
    }

    private void dispatch(String basePath, HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase();
        String fullPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        Map<String, String> query = parseQuery(rawQuery);
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);

        Ctx ctx = new Ctx(exchange, method, fullPath, rawQuery == null ? "" : rawQuery, query, body);
        try {
            Route route = fullPath.startsWith(basePath + "/") || fullPath.equals(basePath)
                    ? match(fullPath.substring(basePath.length()), method, query, ctx)
                    : null;
            if (route == null) {
                notFoundOperation(ctx);
            } else {
                ctx.operationId = route.operationId();
                behaviours.get(routeKey(route.method(), route.pathTemplate(), route.query())).handle(ctx);
            }
        } catch (RuntimeException e) {
            error(ctx, 500, "INTERNAL_SERVER_ERROR", "com.vmware.vapi.std.errors.internal_server_error",
                    "The mock endpoint failed while handling the request: " + e, List.of());
        } finally {
            writeLogLine(ctx);
        }
    }

    private Route match(String relativePath, String method, Map<String, String> query, Ctx ctx) {
        for (Route route : routes) {
            if (!route.method().equals(method)) {
                continue;
            }
            if (!query.equals(new TreeMap<>(route.query()))) {
                continue;
            }
            Matcher m = route.pattern().matcher(relativePath);
            if (!m.matches()) {
                continue;
            }
            for (int i = 0; i < route.varNames().size(); i++) {
                ctx.pathVars.put(route.varNames().get(i), m.group(i + 1));
            }
            return route;
        }
        return null;
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> map = new TreeMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return map;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            if (eq < 0) {
                map.put(java.net.URLDecoder.decode(pair, StandardCharsets.UTF_8), "");
            } else {
                map.put(java.net.URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8),
                        java.net.URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
            }
        }
        return map;
    }

    // ------------------------------------------------------------- behaviours

    private void registerBehaviours() {
        // The simulated surface is deliberately wider than any single contract.
        behaviours.put("POST /session", this::sessionCreate);
        behaviours.put("GET /session", this::sessionGet);
        behaviours.put("GET /vcenter/vm/{vm}/power", this::powerGet);
        behaviours.put("POST /vcenter/vm/{vm}/power?action=stop", this::powerStop);
        behaviours.put("POST /vcenter/vm/{vm}/power?action=start", this::powerStart);
        behaviours.put("GET /vcenter/vm/{vm}/hardware/cpu", this::cpuGet);
        behaviours.put("PATCH /vcenter/vm/{vm}/hardware/cpu", this::cpuUpdate);
        behaviours.put("GET /vcenter/vm/{vm}/hardware/memory", this::memoryGet);
        behaviours.put("PATCH /vcenter/vm/{vm}/hardware/memory", this::memoryUpdate);
        behaviours.put("GET /vcenter/vm/{vm}/hardware/disk", this::diskList);
        behaviours.put("POST /vcenter/vm/{vm}/hardware/disk", this::diskCreate);
    }

    private void sessionCreate(Ctx ctx) throws IOException {
        String header = ctx.exchange.getRequestHeaders().getFirst("Authorization");
        if (header == null || !header.regionMatches(true, 0, "Basic ", 0, 6)) {
            unauthenticated(ctx, "This operation requires HTTP Basic credentials.");
            return;
        }
        String decoded;
        try {
            decoded = new String(Base64.getDecoder().decode(header.substring(6).trim()), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            unauthenticated(ctx, "The Authorization header is not valid base64.");
            return;
        }
        int colon = decoded.indexOf(':');
        String user = colon < 0 ? decoded : decoded.substring(0, colon);
        String pass = colon < 0 ? "" : decoded.substring(colon + 1);
        if (!username.equals(user) || !password.equals(pass)) {
            unauthenticated(ctx, "Cannot complete login due to an incorrect user name or password.");
            return;
        }
        sessionIssued = true;
        json(ctx, 201, sessionToken);
    }

    private void sessionGet(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> info = MiniJson.obj();
        info.put("user", MiniJson.asString(fixtures.get("session_user")));
        info.put("created_time", "2026-08-12T09:00:00.000Z");
        info.put("last_accessed_time", "2026-08-12T09:00:00.000Z");
        json(ctx, 200, info);
    }

    private void powerGet(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm == null) {
            return;
        }
        String state = (String) vm.get("power_state");
        Map<String, Object> info = MiniJson.obj();
        info.put("state", state);
        if ("POWERED_OFF".equals(state)) {
            info.put("clean_power_off", Boolean.TRUE);
        }
        json(ctx, 200, info);
    }

    private void powerStop(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm == null) {
            return;
        }
        if ("POWERED_OFF".equals(vm.get("power_state"))) {
            error(ctx, 400, "ALREADY_IN_DESIRED_STATE",
                    "com.vmware.vcenter.vm.power.already_powered_off",
                    "Virtual machine " + ctx.vm() + " is already powered off.",
                    List.of(ctx.vm()));
            return;
        }
        vm.put("power_state", "POWERED_OFF");
        noContent(ctx);
    }

    private void powerStart(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm == null) {
            return;
        }
        if ("POWERED_ON".equals(vm.get("power_state"))) {
            error(ctx, 400, "ALREADY_IN_DESIRED_STATE",
                    "com.vmware.vcenter.vm.power.already_powered_on",
                    "Virtual machine " + ctx.vm() + " is already powered on.",
                    List.of(ctx.vm()));
            return;
        }
        vm.put("power_state", "POWERED_ON");
        noContent(ctx);
    }

    private void cpuGet(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm != null) {
            json(ctx, 200, MiniJson.asObject(vm.get("cpu")));
        }
    }

    private void cpuUpdate(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm == null) {
            return;
        }
        Map<String, Object> spec = bodyObject(ctx, Set.of("count", "cores_per_socket", "hot_add_enabled", "hot_remove_enabled"));
        if (spec == null) {
            return;
        }
        Map<String, Object> cpu = MiniJson.asObject(vm.get("cpu"));
        boolean poweredOff = "POWERED_OFF".equals(vm.get("power_state"));

        // Per the specification these two may only be modified while powered off.
        if ((spec.get("hot_add_enabled") != null || spec.get("hot_remove_enabled") != null) && !poweredOff) {
            notAllowedInCurrentState(ctx, "The CPU hot-add and hot-remove settings of virtual machine "
                    + ctx.vm() + " may only be changed while it is powered off.");
            return;
        }
        if (spec.get("count") != null) {
            long count = MiniJson.asLong(spec.get("count"));
            if (count <= 0) {
                invalidArgument(ctx, "CPU count must be a positive integer, got " + count + ".");
                return;
            }
            long current = MiniJson.asLong(cpu.get("count"));
            if (!poweredOff && count > current && !Boolean.TRUE.equals(cpu.get("hot_add_enabled"))) {
                notAllowedInCurrentState(ctx, "CPU hot-add is not enabled on virtual machine " + ctx.vm()
                        + "; the CPU count cannot be raised from " + current + " to " + count + " while it is powered on.");
                return;
            }
            if (!poweredOff && count < current && !Boolean.TRUE.equals(cpu.get("hot_remove_enabled"))) {
                notAllowedInCurrentState(ctx, "CPU hot-remove is not enabled on virtual machine " + ctx.vm()
                        + "; the CPU count cannot be lowered from " + current + " to " + count + " while it is powered on.");
                return;
            }
            cpu.put("count", count);
        }
        if (spec.get("cores_per_socket") != null) {
            cpu.put("cores_per_socket", MiniJson.asLong(spec.get("cores_per_socket")));
        }
        if (spec.get("hot_add_enabled") != null) {
            cpu.put("hot_add_enabled", spec.get("hot_add_enabled"));
        }
        if (spec.get("hot_remove_enabled") != null) {
            cpu.put("hot_remove_enabled", spec.get("hot_remove_enabled"));
        }
        noContent(ctx);
    }

    private void memoryGet(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm != null) {
            json(ctx, 200, MiniJson.asObject(vm.get("memory")));
        }
    }

    private void memoryUpdate(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm == null) {
            return;
        }
        Map<String, Object> spec = bodyObject(ctx, Set.of("size_mib", "hot_add_enabled"));
        if (spec == null) {
            return;
        }
        Map<String, Object> memory = MiniJson.asObject(vm.get("memory"));
        boolean poweredOff = "POWERED_OFF".equals(vm.get("power_state"));

        if (spec.get("hot_add_enabled") != null && !poweredOff) {
            notAllowedInCurrentState(ctx, "The memory hot-add setting of virtual machine " + ctx.vm()
                    + " may only be changed while it is powered off.");
            return;
        }
        if (spec.get("size_mib") != null) {
            long size = MiniJson.asLong(spec.get("size_mib"));
            if (size <= 0) {
                invalidArgument(ctx, "Memory size must be a positive number of mebibytes, got " + size + ".");
                return;
            }
            if (!poweredOff && !Boolean.TRUE.equals(memory.get("hot_add_enabled"))) {
                notAllowedInCurrentState(ctx, "Memory hot-add is not enabled on virtual machine " + ctx.vm()
                        + "; the memory size cannot be changed while it is powered on.");
                return;
            }
            memory.put("size_mib", size);
        }
        if (spec.get("hot_add_enabled") != null) {
            memory.put("hot_add_enabled", spec.get("hot_add_enabled"));
        }
        noContent(ctx);
    }

    private void diskList(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm != null) {
            json(ctx, 200, vm.get("disks"));
        }
    }

    private void diskCreate(Ctx ctx) throws IOException {
        if (!authorised(ctx)) {
            return;
        }
        Map<String, Object> vm = vmOrNotFound(ctx);
        if (vm == null) {
            return;
        }
        Map<String, Object> spec = bodyObject(ctx,
                Set.of("type", "ide", "scsi", "sata", "nvme", "backing", "new_vmdk"));
        if (spec == null) {
            return;
        }
        if (spec.get("backing") != null && spec.get("new_vmdk") != null) {
            invalidArgument(ctx, "Exactly one of backing or new_vmdk must be specified.");
            return;
        }

        long bus = 0;
        if (spec.get("scsi") instanceof Map<?, ?>) {
            Object busValue = MiniJson.asObject(spec.get("scsi")).get("bus");
            if (busValue != null) {
                bus = MiniJson.asLong(busValue);
            }
        }
        for (Object adapterValue : MiniJson.asArray(vm.get("scsi_adapters"))) {
            Map<String, Object> adapter = MiniJson.asObject(adapterValue);
            if (MiniJson.asLong(adapter.get("bus")) == bus && MiniJson.asLong(adapter.get("free_units")) == 0) {
                String address = String.valueOf(adapter.get("occupied_by"));
                error(ctx, 400, "RESOURCE_IN_USE",
                        "com.vmware.vcenter.vm.hardware.disk.storage_address_in_use",
                        "The storage address " + address + " on " + adapter.get("label")
                                + " is already in use by an existing virtual disk of virtual machine "
                                + ctx.vm() + ".",
                        List.of(address, String.valueOf(adapter.get("label")), ctx.vm()));
                return;
            }
        }
        json(ctx, 201, "2001");
    }

    // ------------------------------------------------------------- primitives

    private boolean authorised(Ctx ctx) throws IOException {
        String token = ctx.exchange.getRequestHeaders().getFirst(SESSION_HEADER);
        if (token == null || token.isEmpty()) {
            unauthenticated(ctx, "This operation requires a session token in the "
                    + SESSION_HEADER + " header.");
            return false;
        }
        if (!sessionIssued || !sessionToken.equals(token)) {
            unauthenticated(ctx, "The session token is not valid or has expired.");
            return false;
        }
        return true;
    }

    private Map<String, Object> vmOrNotFound(Ctx ctx) throws IOException {
        Map<String, Object> vms = MiniJson.asObject(fixtures.get("vms"));
        Object vm = vms.get(ctx.vm());
        if (vm == null) {
            error(ctx, 404, "NOT_FOUND", "com.vmware.vcenter.vm.not_found",
                    "Virtual machine " + ctx.vm() + " was not found.", List.of(String.valueOf(ctx.vm())));
            return null;
        }
        return MiniJson.asObject(vm);
    }

    /**
     * Parses a request body as a JSON object and rejects properties the schema does
     * not define. Properties explicitly set to null are accepted -- the real API
     * treats an explicit null the same as an absent property.
     */
    private Map<String, Object> bodyObject(Ctx ctx, Set<String> allowed) throws IOException {
        if (ctx.body == null || ctx.body.isBlank()) {
            invalidArgument(ctx, "A JSON request body is required for this operation.");
            return null;
        }
        Map<String, Object> spec;
        try {
            spec = MiniJson.asObject(MiniJson.parse(ctx.body));
        } catch (RuntimeException e) {
            invalidArgument(ctx, "The request body is not a JSON object: " + e.getMessage());
            return null;
        }
        List<String> unknown = new ArrayList<>();
        for (String key : spec.keySet()) {
            if (!allowed.contains(key)) {
                unknown.add(key);
            }
        }
        if (!unknown.isEmpty()) {
            invalidArgument(ctx, "The request body contains properties that are not part of this operation's "
                    + "schema: " + String.join(", ", unknown) + ".");
            return null;
        }
        return spec;
    }

    private void unauthenticated(Ctx ctx, String message) throws IOException {
        error(ctx, 401, "UNAUTHENTICATED", "com.vmware.vapi.std.errors.unauthenticated", message, List.of());
    }

    private void invalidArgument(Ctx ctx, String message) throws IOException {
        error(ctx, 400, "INVALID_ARGUMENT", "com.vmware.vapi.std.errors.invalid_argument", message, List.of());
    }

    private void notAllowedInCurrentState(Ctx ctx, String message) throws IOException {
        error(ctx, 400, "NOT_ALLOWED_IN_CURRENT_STATE",
                "com.vmware.vapi.std.errors.not_allowed_in_current_state", message, List.of());
    }

    private void notFoundOperation(Ctx ctx) throws IOException {
        error(ctx, 404, "OPERATION_NOT_FOUND", "com.vmware.vapi.rest.operation_not_found",
                "No operation is exposed at " + ctx.method + " " + ctx.fullPath
                        + (ctx.rawQuery.isEmpty() ? "" : "?" + ctx.rawQuery)
                        + " by this endpoint. The pinned contract exposes " + routes.size()
                        + " operation(s); requests outside it are not routed.", List.of());
    }

    private void error(Ctx ctx, int status, String errorType, String id, String defaultMessage, List<String> args)
            throws IOException {
        Map<String, Object> message = MiniJson.obj();
        message.put("id", id);
        message.put("default_message", defaultMessage);
        message.put("args", args);
        Map<String, Object> body = MiniJson.obj();
        body.put("error_type", errorType);
        body.put("messages", List.of(message));
        json(ctx, status, body);
    }

    private void json(Ctx ctx, int status, Object value) throws IOException {
        byte[] payload = MiniJson.write(value).getBytes(StandardCharsets.UTF_8);
        ctx.status = status;
        ctx.exchange.getResponseHeaders().set("Content-Type", "application/json");
        ctx.exchange.sendResponseHeaders(status, payload.length);
        try (OutputStream out = ctx.exchange.getResponseBody()) {
            out.write(payload);
        }
    }

    private void noContent(Ctx ctx) throws IOException {
        ctx.status = 204;
        ctx.exchange.sendResponseHeaders(204, -1);
    }

    // ------------------------------------------------------------ request log

    private void writeLogLine(Ctx ctx) {
        String authHeader = ctx.exchange.getRequestHeaders().getFirst("Authorization");
        String authScheme = null;
        String authUser = null;
        if (authHeader != null) {
            int space = authHeader.indexOf(' ');
            authScheme = space < 0 ? authHeader : authHeader.substring(0, space);
            if (space > 0 && authScheme.equalsIgnoreCase("Basic")) {
                try {
                    String decoded = new String(Base64.getDecoder().decode(authHeader.substring(space + 1).trim()),
                            StandardCharsets.UTF_8);
                    int colon = decoded.indexOf(':');
                    authUser = colon < 0 ? decoded : decoded.substring(0, colon);
                } catch (IllegalArgumentException e) {
                    authUser = null;
                }
            }
        }
        String sessionHeader = ctx.exchange.getRequestHeaders().getFirst(SESSION_HEADER);

        Map<String, Object> entry = MiniJson.obj();
        entry.put("seq", (long) ++seq);
        entry.put("method", ctx.method);
        entry.put("path", ctx.fullPath);
        entry.put("query", ctx.rawQuery);
        entry.put("operation_id", ctx.operationId);
        entry.put("status", (long) ctx.status);
        entry.put("content_type", ctx.exchange.getRequestHeaders().getFirst("Content-Type"));
        entry.put("accept", ctx.exchange.getRequestHeaders().getFirst("Accept"));
        entry.put("auth_scheme", authScheme);
        entry.put("auth_user", authUser);
        entry.put("session_header_present", sessionHeader != null && !sessionHeader.isEmpty());
        entry.put("session_header_matches_issued_token", sessionToken.equals(sessionHeader));
        entry.put("body", ctx.body);
        entry.put("body_length", (long) ctx.body.getBytes(StandardCharsets.UTF_8).length);

        try {
            log.write(MiniJson.write(entry));
            log.write('\n');
            log.flush();
        } catch (IOException e) {
            System.err.println("VcenterMock: could not append to the request log: " + e);
        }
    }

    // ------------------------------------------------------------------ utils

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> opts = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i++) {
            if (!args[i].startsWith("--") || i + 1 >= args.length) {
                throw new IllegalArgumentException("expected --name value pairs, got " + args[i]);
            }
            opts.put(args[i], args[++i]);
        }
        return opts;
    }

    private static Path required(Map<String, String> opts, String name) {
        String value = opts.get(name);
        if (value == null) {
            throw new IllegalArgumentException("missing required option " + name);
        }
        return Path.of(value);
    }
}
