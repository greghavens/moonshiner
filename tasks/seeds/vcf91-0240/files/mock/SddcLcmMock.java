import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Loopback fixture for the VCF 9.1 SDDC LCM service.
 *
 * It serves exactly the five operations named in docs/contract.json -- getComponents,
 * setDepot, resolveDepotComponents, performComponentAction and getTask -- under the
 * spec's server base path (/sddc-lcm). Every other route answers 404. Requests are
 * validated against the contract (auth, content type, query parameters, required and
 * permitted body properties) and appended to a JSON Lines request log.
 *
 * It binds to 127.0.0.1 only and reaches no network beyond the loopback interface.
 *
 * Usage: SddcLcmMock --port <p> --log <file> [--portfile <file>] [--token <bearer>]
 */
public final class SddcLcmMock {

    static final String BASE = "/sddc-lcm";

    // Fleet inventory ------------------------------------------------------
    static final String OPS_ID = "b7b2f1a4-3f4e-4a2c-9a4b-6c1d0e5f7a21";
    static final String AUTOMATION_ID = "d3c8e5f2-1b47-4e69-ac30-58f2a9b6c714";
    static final String VCENTER_ID = "6e1a9b03-c47d-4f52-8a16-93b2d0e7f458";
    static final String OPS_FQDN = "ops.vcf.lab.local";

    // Depot catalogue ------------------------------------------------------
    static final String DEPOT_FQDN = "depot.vcf.lab.local";
    static final Map<String, String> LATEST = new LinkedHashMap<>();
    static final Map<String, String> BUNDLE_SLUG = new LinkedHashMap<>();

    static {
        LATEST.put("VCF_OPERATIONS", "9.1.0.0.24010188");
        LATEST.put("VCF_AUTOMATION", "9.1.0.0.24010199");
        LATEST.put("VCF_VCENTER", "9.1.0.0.24010142");
        BUNDLE_SLUG.put("VCF_OPERATIONS", "vcf-operations");
        BUNDLE_SLUG.put("VCF_AUTOMATION", "vcf-automation");
        BUNDLE_SLUG.put("VCF_VCENTER", "vcf-vcenter");
    }

    // Tasks ----------------------------------------------------------------
    static final String TASK_DEPOT = "1f0b6c2e-8a41-4d1b-93b7-2c9f5a7e10d4";
    static final String TASK_PRECHECK = "5c93a7d1-0e26-4f83-b1aa-77d4e2c9f018";
    static final String TASK_APPLY = "9a4e1d70-63b8-4c25-8f19-be0c37a5d962";

    static final String T0 = "2026-05-19T08:14:02.000Z";
    static final String T1 = "2026-05-19T08:14:03.000Z";
    static final String T2 = "2026-05-19T08:27:41.000Z";

    // Mutable fixture state ------------------------------------------------
    private final Object lock = new Object();
    private final AtomicInteger seq = new AtomicInteger();
    private final Map<String, Integer> polls = new HashMap<>();
    private boolean depotRegistered;
    private String registeredDepotFqdn;
    private final Set<String> resolvedComponents = new LinkedHashSet<>();
    private boolean prechecked;
    private String precheckCorrelationId;
    private boolean applyStarted;
    private String applyCorrelationId;

    private final String token;
    private final Path logPath;
    private final boolean failPrecheck;

    private SddcLcmMock(String token, Path logPath, boolean failPrecheck) {
        this.token = token;
        this.logPath = logPath;
        this.failPrecheck = failPrecheck;
    }

    public static void main(String[] args) throws Exception {
        int port = 0;
        String log = "requests.jsonl";
        String portFile = null;
        String token = null;
        boolean failPrecheck = false;
        for (int i = 0; i < args.length - 1; i++) {
            switch (args[i]) {
                case "--port":
                    port = Integer.parseInt(args[++i]);
                    break;
                case "--log":
                    log = args[++i];
                    break;
                case "--portfile":
                    portFile = args[++i];
                    break;
                case "--token":
                    token = args[++i];
                    break;
                case "--fail-precheck":
                    failPrecheck = Boolean.parseBoolean(args[++i]);
                    break;
                default:
                    break;
            }
        }

        Path logPath = Paths.get(log).toAbsolutePath();
        Files.createDirectories(logPath.getParent());
        Files.write(logPath, new byte[0]);

        SddcLcmMock mock = new SddcLcmMock(token, logPath, failPrecheck);
        HttpServer server = HttpServer.create(
                new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 0);
        server.createContext("/", mock::handle);
        server.setExecutor(null);
        server.start();

        int bound = server.getAddress().getPort();
        if (portFile != null) {
            Files.write(Paths.get(portFile), String.valueOf(bound).getBytes(StandardCharsets.UTF_8));
        }
        System.out.println("sddc-lcm mock listening on http://127.0.0.1:" + bound + BASE);
        System.out.flush();

        Runtime.getRuntime().addShutdownHook(new Thread(() -> server.stop(0)));
    }

    // ------------------------------------------------------------- dispatch

    private void handle(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        String rawQuery = ex.getRequestURI().getRawQuery();
        Map<String, String> query = parseQuery(rawQuery);
        byte[] raw = ex.getRequestBody().readAllBytes();
        String rawBody = new String(raw, StandardCharsets.UTF_8);

        Object parsedBody = null;
        String parseError = null;
        if (!rawBody.isEmpty()) {
            try {
                parsedBody = Json.parse(rawBody);
            } catch (RuntimeException e) {
                parseError = e.getMessage();
            }
        }

        Reply reply;
        String operationId = null;
        try {
            Route route = route(method, path);
            operationId = route == null ? null : route.operationId;
            reply = dispatch(route, ex.getRequestHeaders(), query, rawQuery, parsedBody, parseError);
        } catch (RuntimeException e) {
            reply = new Reply(500, error("LCM_INTERNAL_ERROR",
                    "Mock failure: " + e, "Report this fixture defect."));
        }

        log(seq.incrementAndGet(), method, path, rawQuery, query, operationId,
                ex.getRequestHeaders(), parsedBody, rawBody, reply.status);

        byte[] out = Json.writeIndented(reply.body).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(reply.status, out.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(out);
        }
    }

    private Reply dispatch(Route route, Headers headers, Map<String, String> query,
                           String rawQuery, Object body, String parseError) {
        if (route == null) {
            return new Reply(404, error("LCM_ROUTE_NOT_FOUND",
                    "No SDDC LCM operation is served at this path by this fixture.",
                    "Use one of the operations listed in docs/contract.json."));
        }
        if (route.methodMismatch) {
            return new Reply(405, error("LCM_METHOD_NOT_ALLOWED",
                    "Method not allowed for this path.",
                    "Check the method recorded for this operationId in docs/contract.json."));
        }

        String auth = headers.getFirst("Authorization");
        if (auth == null || !auth.startsWith("Bearer ") || auth.length() <= "Bearer ".length()
                || (token != null && !auth.equals("Bearer " + token))) {
            return new Reply(401, error("LCM_UNAUTHORIZED",
                    "Missing or invalid bearer token.",
                    "Send Authorization: Bearer <token> on every request."));
        }

        if (route.hasBody) {
            String ct = headers.getFirst("Content-Type");
            if (ct == null || !ct.toLowerCase().startsWith("application/json")) {
                return new Reply(400, error("LCM_UNSUPPORTED_MEDIA_TYPE",
                        "Request body must be sent as application/json.",
                        "Set the Content-Type request header to application/json."));
            }
            if (parseError != null) {
                return new Reply(400, error("LCM_MALFORMED_BODY",
                        "Request body is not valid JSON: " + parseError,
                        "Send a well formed JSON document."));
            }
            if (!(body instanceof Map)) {
                return new Reply(400, error("LCM_MALFORMED_BODY",
                        "Request body must be a JSON object.",
                        "Send the request schema named in docs/contract.json."));
            }
        }

        switch (route.operationId) {
            case "getComponents":
                return getComponents(query);
            case "setDepot":
                return setDepot(Json.map(body));
            case "resolveDepotComponents":
                return resolveDepotComponents(query, Json.map(body));
            case "performComponentAction":
                return performComponentAction(route.pathParam, query, headers, Json.map(body));
            case "getTask":
                return getTask(route.pathParam, query);
            default:
                return new Reply(404, error("LCM_ROUTE_NOT_FOUND", "Unknown operation.", "See docs/contract.json."));
        }
    }

    private static final class Route {
        final String operationId;
        final String pathParam;
        final boolean hasBody;
        final boolean methodMismatch;

        Route(String operationId, String pathParam, boolean hasBody, boolean methodMismatch) {
            this.operationId = operationId;
            this.pathParam = pathParam;
            this.hasBody = hasBody;
            this.methodMismatch = methodMismatch;
        }
    }

    private Route route(String method, String path) {
        if (!path.startsWith(BASE + "/")) {
            return null;
        }
        String rest = path.substring(BASE.length());
        if (rest.equals("/v1/components")) {
            if (method.equals("GET")) {
                return new Route("getComponents", null, false, false);
            }
            return new Route("getComponents", null, false, true);
        }
        if (rest.equals("/v1/depot")) {
            if (method.equals("POST")) {
                return new Route("setDepot", null, true, false);
            }
            return new Route("setDepot", null, true, true);
        }
        if (rest.equals("/v1/depot/components")) {
            if (method.equals("POST")) {
                return new Route("resolveDepotComponents", null, true, false);
            }
            return new Route("resolveDepotComponents", null, true, true);
        }
        if (rest.startsWith("/v1/components/")) {
            String seg = rest.substring("/v1/components/".length());
            if (seg.isEmpty() || seg.contains("/")) {
                return null; // sub-resources of a component are outside this contract
            }
            if (method.equals("POST")) {
                return new Route("performComponentAction", decode(seg), true, false);
            }
            return null; // getComponent / updateComponent are outside this contract
        }
        if (rest.startsWith("/v1/tasks/")) {
            String seg = rest.substring("/v1/tasks/".length());
            if (seg.isEmpty() || seg.contains("/")) {
                return null;
            }
            if (method.equals("GET")) {
                return new Route("getTask", decode(seg), false, false);
            }
            return new Route("getTask", decode(seg), false, true);
        }
        return null;
    }

    // ----------------------------------------------------------- operations

    private Reply getComponents(Map<String, String> query) {
        Reply bad = onlyParams(query, "scope");
        if (bad != null) {
            return bad;
        }
        String scope = query.get("scope");
        if (scope != null && !scope.equals("FLEET") && !scope.equals("INSTANCE")) {
            return new Reply(400, error("LCM_INVALID_PARAMETER",
                    "Query parameter 'scope' must be one of [FLEET, INSTANCE], got '" + scope + "'.",
                    "See the getComponents parameters in docs/contract.json."));
        }

        List<Object> components = Json.arr();
        if (scope == null || scope.equals("FLEET")) {
            components.add(component(OPS_ID, "VCF_OPERATIONS", "9.0.2.0.23984011",
                    OPS_FQDN, "FLEET", "Medium"));
            components.add(component(AUTOMATION_ID, "VCF_AUTOMATION", "9.0.2.0.23984044",
                    "automation.vcf.lab.local", "FLEET", "Small"));
        }
        if (scope == null || scope.equals("INSTANCE")) {
            components.add(component(VCENTER_ID, "VCF_VCENTER", "9.0.2.0.23984007",
                    "vc01.vcf.lab.local", "INSTANCE", "Small"));
        }
        Map<String, Object> out = Json.obj();
        out.put("components", components);
        return new Reply(200, out);
    }

    private Reply setDepot(Map<String, Object> body) {
        Reply bad = shape(body, "FleetDepotSpec", set("fqdn", "certificate"), set(), typeMap(
                "fqdn", "string", "certificate", "string"), "");
        if (bad != null) {
            return bad;
        }
        synchronized (lock) {
            depotRegistered = true;
            registeredDepotFqdn = String.valueOf(body.get("fqdn"));
            polls.remove(TASK_DEPOT);
        }
        return new Reply(202, task(TASK_DEPOT, "fleet_depot_registration", "depot-registration",
                "PENDING", DEPOT_FQDN, "DEPOT", null, null, null));
    }

    private Reply resolveDepotComponents(Map<String, String> query, Map<String, Object> body) {
        Reply bad = onlyParams(query);
        if (bad != null) {
            return bad;
        }
        bad = shape(body, "DepotComponentsSpec",
                set("fleetDepotSpec", "componentVersions"), set("version"),
                typeMap("fleetDepotSpec", "object", "componentVersions", "array", "version", "string"), "");
        if (bad != null) {
            return bad;
        }
        bad = shape(Json.map(body.get("fleetDepotSpec")), "FleetDepotSpec",
                set("fqdn", "certificate"), set(),
                typeMap("fqdn", "string", "certificate", "string"), "fleetDepotSpec.");
        if (bad != null) {
            return bad;
        }

        synchronized (lock) {
            if (!depotRegistered || !statusOf(TASK_DEPOT).equals("SUCCEEDED")) {
                return new Reply(400, error("LCM_DEPOT_REGISTRATION_INCOMPLETE",
                        "The fleet depot registration task has not reached a terminal successful state.",
                        "Register the depot and wait for its task to reach SUCCEEDED before resolving components."));
            }
            if (!String.valueOf(Json.path(body, "fleetDepotSpec", "fqdn")).equals(registeredDepotFqdn)) {
                return new Reply(400, error("LCM_DEPOT_MISMATCH",
                        "fleetDepotSpec.fqdn does not match the registered fleet depot.",
                        "Resolve components against the depot that was registered."));
            }
        }

        List<Object> requested = Json.list(body.get("componentVersions"));
        if (requested.isEmpty()) {
            return new Reply(400, error("LCM_INVALID_BODY",
                    "componentVersions must contain at least one entry.",
                    "See DepotComponentsSpec in docs/contract.json."));
        }
        String fleetVersion = body.containsKey("version") ? String.valueOf(body.get("version")) : null;

        List<Object> resolved = Json.arr();
        for (int i = 0; i < requested.size(); i++) {
            Object entry = requested.get(i);
            if (!(entry instanceof Map)) {
                return new Reply(400, error("LCM_INVALID_BODY",
                        "componentVersions[" + i + "] must be an object.",
                        "See ComponentVersionSpec in docs/contract.json."));
            }
            Map<String, Object> cv = Json.map(entry);
            Reply e = shape(cv, "ComponentVersionSpec", set("component"), set("version"),
                    typeMap("component", "string", "version", "string"),
                    "componentVersions[" + i + "].");
            if (e != null) {
                return e;
            }
            String comp = String.valueOf(cv.get("component"));
            if (!LATEST.containsKey(comp)) {
                return new Reply(400, error("LCM_UNKNOWN_COMPONENT",
                        "Component '" + comp + "' is not published in this depot.",
                        "Request a component that the depot publishes."));
            }
            String version = cv.containsKey("version") ? String.valueOf(cv.get("version"))
                    : (fleetVersion != null ? fleetVersion : LATEST.get(comp));
            if (!version.equals(LATEST.get(comp))) {
                return new Reply(400, error("LCM_VERSION_NOT_IN_DEPOT",
                        "Version '" + version + "' of '" + comp + "' is not present in this depot.",
                        "Request a version the depot publishes for that component."));
            }
            Map<String, Object> r = Json.obj();
            r.put("component", comp);
            r.put("version", version);
            r.put("binaryUrl", "https://" + DEPOT_FQDN + "/bundles/" + BUNDLE_SLUG.get(comp)
                    + "/" + version + "/bundle.manifest");
            resolved.add(r);
            synchronized (lock) {
                resolvedComponents.add(comp + "@" + version);
            }
        }
        Map<String, Object> out = Json.obj();
        out.put("componentVersions", resolved);
        return new Reply(200, out);
    }

    private Reply performComponentAction(String componentId, Map<String, String> query,
                                         Headers headers, Map<String, Object> body) {
        Reply bad = onlyParams(query, "action");
        if (bad != null) {
            return bad;
        }
        String componentType = componentTypeOf(componentId);
        if (componentType == null) {
            return new Reply(404, error("LCM_COMPONENT_NOT_FOUND",
                    "No component with id '" + componentId + "'.",
                    "Discover component identifiers with getComponents."));
        }
        String action = query.get("action");
        if (action == null) {
            return new Reply(400, error("LCM_MISSING_PARAMETER",
                    "Query parameter 'action' is required.",
                    "See the performComponentAction parameters in docs/contract.json."));
        }
        List<String> actions = Arrays.asList("shutdown", "restart", "start", "refresh", "precheck", "apply");
        if (!actions.contains(action)) {
            return new Reply(400, error("LCM_INVALID_PARAMETER",
                    "Query parameter 'action' must be one of " + actions + ", got '" + action + "'.",
                    "See the performComponentAction parameters in docs/contract.json."));
        }
        if (!action.equals("precheck") && !action.equals("apply")) {
            return new Reply(400, error("LCM_ACTION_NOT_AVAILABLE",
                    "Action '" + action + "' is not available for this component in its current state.",
                    "Only upgrade prechecks and applies are available on this fleet component."));
        }

        bad = shape(body, "ComponentUpgradeSpec", set("componentSpec"),
                set("lcmPlatformSpec", "correlationId"),
                typeMap("componentSpec", "object", "lcmPlatformSpec", "object",
                        "correlationId", "string"), "");
        if (bad != null) {
            return bad;
        }
        Map<String, Object> spec = Json.map(body.get("componentSpec"));
        bad = shape(spec, "ComponentDesiredSpec", set("software", "depot"),
                set("policy", "userInput", "additionalInput"),
                typeMap("software", "object", "depot", "object", "policy", "object",
                        "userInput", "object", "additionalInput", "object"), "componentSpec.");
        if (bad != null) {
            return bad;
        }
        bad = shape(Json.map(spec.get("software")), "ComponentSoftwareSpec", set("version"), set(),
                typeMap("version", "string"), "componentSpec.software.");
        if (bad != null) {
            return bad;
        }
        bad = shape(Json.map(spec.get("depot")), "DepotSpec", set("url"), set("certificate"),
                typeMap("url", "string", "certificate", "array"), "componentSpec.depot.");
        if (bad != null) {
            return bad;
        }
        if (body.containsKey("lcmPlatformSpec")) {
            bad = shape(Json.map(body.get("lcmPlatformSpec")), "LcmPlatformSpec",
                    set("performBackup"), set(), typeMap("performBackup", "boolean"),
                    "lcmPlatformSpec.");
            if (bad != null) {
                return bad;
            }
        }
        Object certs = Json.path(spec, "depot", "certificate");
        if (certs != null) {
            List<Object> list = Json.list(certs);
            for (int i = 0; i < list.size(); i++) {
                if (!(list.get(i) instanceof String) || ((String) list.get(i)).isEmpty()) {
                    return new Reply(400, error("LCM_INVALID_BODY",
                            "componentSpec.depot.certificate[" + i + "] must be a non-empty string.",
                            "Send the PEM certificate chain as an array of strings."));
                }
            }
        }

        String version = String.valueOf(Json.path(spec, "software", "version"));
        String correlationId = body.containsKey("correlationId")
                ? String.valueOf(body.get("correlationId")) : null;

        synchronized (lock) {
            if (!depotRegistered || !statusOf(TASK_DEPOT).equals("SUCCEEDED")) {
                return new Reply(400, error("LCM_DEPOT_REGISTRATION_INCOMPLETE",
                        "The fleet depot registration task has not reached a terminal successful state.",
                        "Register the depot and wait for its task to reach SUCCEEDED first."));
            }
            if (!resolvedComponents.contains(componentType + "@" + version)) {
                return new Reply(400, error("LCM_COMPONENT_VERSION_NOT_RESOLVED",
                        "Version '" + version + "' of '" + componentType
                                + "' has not been resolved against the fleet depot.",
                        "Resolve the target versions with resolveDepotComponents first."));
            }
            if (action.equals("precheck")) {
                prechecked = true;
                precheckCorrelationId = correlationId;
                polls.remove(TASK_PRECHECK);
                return new Reply(202, task(TASK_PRECHECK, "vcf_ops_91_upgrade_precheck", "precheck",
                        "PENDING", componentId, "COMPONENT", correlationId, null, null));
            }
            if (!prechecked || !statusOf(TASK_PRECHECK).equals("SUCCEEDED")) {
                return new Reply(400, error("LCM_PRECHECK_NOT_COMPLETED",
                        "An upgrade precheck must complete successfully before the apply action.",
                        "Run action=precheck and wait for its task to reach SUCCEEDED."));
            }
            applyStarted = true;
            applyCorrelationId = correlationId;
            polls.remove(TASK_APPLY);
            return new Reply(202, task(TASK_APPLY, "vcf_ops_91_upgrade_apply", "apply",
                    "PENDING", componentId, "COMPONENT", correlationId, null, null));
        }
    }

    private Reply getTask(String taskId, Map<String, String> query) {
        Reply bad = onlyParams(query);
        if (bad != null) {
            return bad;
        }
        synchronized (lock) {
            boolean known = (taskId.equals(TASK_DEPOT) && depotRegistered)
                    || (taskId.equals(TASK_PRECHECK) && prechecked)
                    || (taskId.equals(TASK_APPLY) && applyStarted);
            if (!known) {
                return new Reply(404, error("LCM_TASK_NOT_FOUND",
                        "No task with id '" + taskId + "'.",
                        "Use the task id returned by the operation that created it."));
            }
            polls.merge(taskId, 1, Integer::sum);
            String status = statusOf(taskId);
            if (taskId.equals(TASK_DEPOT)) {
                return new Reply(200, task(TASK_DEPOT, "fleet_depot_registration", "depot-registration",
                        status, DEPOT_FQDN, "DEPOT", null, depotStages(status), null));
            }
            if (taskId.equals(TASK_PRECHECK)) {
                return new Reply(200, task(TASK_PRECHECK, "vcf_ops_91_upgrade_precheck", "precheck",
                        status, OPS_ID, "COMPONENT", precheckCorrelationId,
                        precheckStages(status), null));
            }
            return new Reply(200, task(TASK_APPLY, "vcf_ops_91_upgrade_apply", "apply",
                    status, OPS_ID, "COMPONENT", applyCorrelationId,
                    applyStages(status), applyMessages(status)));
        }
    }



    /** Tasks advance one notch per poll, so a caller has to follow them to a terminal state. */
    private String statusOf(String taskId) {
        int n = polls.getOrDefault(taskId, 0);
        if (taskId.equals(TASK_DEPOT)) {
            return n == 0 ? "PENDING" : n == 1 ? "RUNNING" : "SUCCEEDED";
        }
        if (taskId.equals(TASK_PRECHECK)) {
            return n == 0 ? "PENDING" : n <= 2 ? "RUNNING"
                    : failPrecheck ? "FAILED" : "SUCCEEDED";
        }
        return n == 0 ? "PENDING" : n <= 2 ? "RUNNING" : "FAILED";
    }

    // ------------------------------------------------------------- payloads

    private Map<String, Object> component(String id, String type, String version,
                                          String fqdn, String scope, String size) {
        Map<String, Object> c = Json.obj();
        c.put("id", id);
        c.put("componentType", type);
        c.put("deploymentType", "OVA");
        c.put("version", version);
        c.put("size", size);
        c.put("fqdn", fqdn);
        c.put("scope", scope);
        List<Object> nodes = Json.arr();
        Map<String, Object> node = Json.obj();
        node.put("nodeType", "PRIMARY");
        node.put("id", id.substring(0, 8) + "-0000-4000-8000-000000000001");
        node.put("version", version);
        node.put("fqdn", fqdn);
        node.put("ipAddress", "10.20.30." + (Math.abs(id.hashCode()) % 200 + 20));
        node.put("status", "ACTIVE");
        node.put("name", fqdn.split("\\.")[0]);
        node.put("size", size);
        nodes.add(node);
        c.put("nodes", nodes);
        return c;
    }

    private Map<String, Object> task(String id, String name, String type, String status,
                                     String resourceId, String resourceType, String correlationId,
                                     List<Object> stages, List<Object> messages) {
        Map<String, Object> t = Json.obj();
        t.put("id", id);
        t.put("name", name);
        Map<String, Object> desc = Json.obj();
        desc.put("id", "com.broadcom.lcm.task." + type.replace('-', '.'));
        desc.put("defaultMessage", describe(type, resourceId));
        desc.put("localizedMessage", describe(type, resourceId));
        t.put("description", desc);
        t.put("status", status);
        t.put("type", type);
        t.put("createdBy", "svc-lcm-automation");
        t.put("updatedBy", "svc-lcm-automation");
        t.put("resourceId", resourceId);
        t.put("resourceType", resourceType);
        t.put("createTime", T0);
        t.put("startTime", T1);
        t.put("updateTime", terminal(status) ? T2 : T1);
        if (terminal(status)) {
            t.put("endTime", T2);
        }
        if (correlationId != null) {
            t.put("correlationId", correlationId);
        }
        t.put("retriable", status.equals("FAILED"));
        t.put("cancellable", !terminal(status));
        if (stages != null) {
            Map<String, Object> summary = Json.obj();
            summary.put("totalSubTasks", 0);
            summary.put("totalSteps", stages.size());
            t.put("taskSummary", summary);
            t.put("stages", stages);
        }
        if (messages != null) {
            t.put("messages", messages);
        }
        return t;
    }

    private static String describe(String type, String resourceId) {
        switch (type) {
            case "depot-registration":
                return "Registering fleet depot " + resourceId;
            case "precheck":
                return "Running upgrade prechecks for component " + resourceId;
            default:
                return "Applying upgrade to component " + resourceId;
        }
    }

    private static boolean terminal(String status) {
        return status.equals("SUCCEEDED") || status.equals("FAILED") || status.equals("CANCELED");
    }

    private List<Object> depotStages(String status) {
        List<Object> stages = Json.arr();
        stages.add(stage("stage-depot-connectivity", "depot-connectivity",
                terminal(status) ? "SUCCEEDED" : "RUNNING", null));
        stages.add(stage("stage-depot-index-sync", "depot-index-sync",
                terminal(status) ? "SUCCEEDED" : "PENDING", null));
        return stages;
    }

    private List<Object> precheckStages(String status) {
        boolean done = status.equals("SUCCEEDED");
        boolean failed = status.equals("FAILED");
        List<Object> stages = Json.arr();
        stages.add(stage("stage-inventory-scan", "inventory-scan",
                done || failed ? "SUCCEEDED" : "RUNNING", null));
        List<Object> warn = Json.arr();
        warn.add(message("WARN", "com.broadcom.lcm.precheck.capacity.headroom",
                "Datastore headroom on " + OPS_FQDN + " is 18%, below the recommended 25%.",
                "stage-capacity-check"));
        stages.add(stage("stage-capacity-check", "capacity-check",
                done || failed ? "SUCCEEDED" : "PENDING", done || failed ? warn : null));
        List<Object> errors = Json.arr();
        errors.add(message("INFO", "com.broadcom.lcm.precheck.compatibility.started",
                "Validating component compatibility for " + OPS_FQDN + ".",
                "stage-compatibility-matrix"));
        errors.add(message("ERROR", "com.broadcom.lcm.precheck.compatibility.failed",
                "Compatibility validation failed for " + OPS_FQDN
                        + " (correlationId=" + precheckCorrelationId + ")",
                "stage-compatibility-matrix"));
        stages.add(stage("stage-compatibility-matrix", "compatibility-matrix",
                done ? "SUCCEEDED" : failed ? "FAILED" : "PENDING", failed ? errors : null));
        return stages;
    }

    private List<Object> applyStages(String status) {
        boolean failed = status.equals("FAILED");
        List<Object> stages = Json.arr();
        stages.add(stage("stage-precheck-revalidate", "precheck-revalidate",
                failed ? "SUCCEEDED" : "RUNNING", null));

        List<Object> transferMessages = Json.arr();
        transferMessages.add(message("INFO", "com.broadcom.lcm.apply.transfer.complete",
                "Transferred upgrade bundle for " + OPS_FQDN + " from " + DEPOT_FQDN + ".",
                "stage-binary-transfer"));
        transferMessages.add(message("WARN", "com.broadcom.lcm.apply.transfer.retried",
                "Bundle transfer was retried once after a transient depot read timeout.",
                "stage-binary-transfer"));
        stages.add(stage("stage-binary-transfer", "binary-transfer",
                failed ? "SUCCEEDED" : "PENDING", failed ? transferMessages : null));

        List<Object> applianceMessages = Json.arr();
        if (failed) {
            applianceMessages.add(message("INFO", "com.broadcom.lcm.apply.appliance.started",
                    "Appliance upgrade started on " + OPS_FQDN + ".", "stage-appliance-upgrade"));
            applianceMessages.add(message("ERROR", "com.broadcom.lcm.apply.appliance.startup.timeout",
                    "Appliance upgrade failed on " + OPS_FQDN
                            + ": post-upgrade service startup did not complete within 1800s"
                            + " (correlationId=" + correlationRef() + ")",
                    "stage-appliance-upgrade"));
        }
        stages.add(stage("stage-appliance-upgrade", "appliance-upgrade",
                failed ? "FAILED" : "PENDING", failed ? applianceMessages : null));

        stages.add(stage("stage-post-upgrade-validation", "post-upgrade-validation",
                failed ? "SKIPPED" : "PENDING", null));
        return stages;
    }

    /** The apply task's correlation reference: the caller's correlationId when it sent one. */
    private String correlationRef() {
        return applyCorrelationId != null ? applyCorrelationId : TASK_APPLY;
    }

    private List<Object> applyMessages(String status) {
        List<Object> messages = Json.arr();
        messages.add(message("INFO", "com.broadcom.lcm.apply.started",
                "Upgrade apply started for component " + OPS_ID + ".", null));
        if (status.equals("FAILED")) {
            messages.add(message("INFO", "com.broadcom.lcm.apply.rollback.available",
                    "A pre-upgrade snapshot is available for rollback.", null));
        }
        return messages;
    }

    private Map<String, Object> stage(String id, String name, String status, List<Object> messages) {
        Map<String, Object> s = Json.obj();
        s.put("id", id);
        s.put("name", name);
        Map<String, Object> desc = Json.obj();
        desc.put("id", "com.broadcom.lcm.stage." + name.replace('-', '.'));
        desc.put("defaultMessage", "Stage " + name);
        desc.put("localizedMessage", "Stage " + name);
        s.put("description", desc);
        s.put("status", status);
        s.put("startTime", T1);
        s.put("updateTime", T2);
        if (messages != null) {
            s.put("messages", messages);
        }
        return s;
    }

    private Map<String, Object> message(String level, String messageId, String text, String stageId) {
        Map<String, Object> m = Json.obj();
        m.put("timestamp", T2);
        m.put("level", level);
        Map<String, Object> lm = Json.obj();
        lm.put("id", messageId);
        lm.put("defaultMessage", text);
        lm.put("localizedMessage", text);
        m.put("message", lm);
        if (stageId != null) {
            m.put("stageId", stageId);
        }
        return m;
    }

    private String componentTypeOf(String id) {
        if (OPS_ID.equals(id)) {
            return "VCF_OPERATIONS";
        }
        if (AUTOMATION_ID.equals(id)) {
            return "VCF_AUTOMATION";
        }
        if (VCENTER_ID.equals(id)) {
            return "VCF_VCENTER";
        }
        return null;
    }

    // ----------------------------------------------------------- validation

    private Reply onlyParams(Map<String, String> query, String... allowed) {
        Set<String> ok = new HashSet<>(Arrays.asList(allowed));
        for (String name : query.keySet()) {
            if (!ok.contains(name)) {
                return new Reply(400, error("LCM_UNKNOWN_PARAMETER",
                        "Query parameter '" + name + "' is not defined for this operation.",
                        "Send only the parameters listed for this operationId in docs/contract.json."));
            }
        }
        return null;
    }

    /**
     * Enforces the contract's property set: every required property present with the right
     * JSON type, every other property drawn from the optional set, and no nulls.
     */
    private Reply shape(Map<String, Object> body, String schema, Set<String> required,
                        Set<String> optional, Map<String, String> types, String prefix) {
        for (String name : required) {
            if (!body.containsKey(name)) {
                return new Reply(400, error("LCM_MISSING_PROPERTY",
                        "Required property '" + prefix + name + "' is missing from " + schema + ".",
                        "See " + schema + " in docs/contract.json."));
            }
        }
        for (Map.Entry<String, Object> e : body.entrySet()) {
            String name = e.getKey();
            if (!required.contains(name) && !optional.contains(name)) {
                return new Reply(400, error("LCM_UNKNOWN_PROPERTY",
                        "Property '" + prefix + name + "' is not defined by " + schema + ".",
                        "Send only the properties " + schema + " defines in docs/contract.json."));
            }
            Object v = e.getValue();
            if (v == null) {
                return new Reply(400, error("LCM_NULL_PROPERTY",
                        "Property '" + prefix + name + "' was sent as null.",
                        "Omit optional properties that have no value instead of sending null."));
            }
            String want = types.get(name);
            if (!typeOk(want, v)) {
                return new Reply(400, error("LCM_INVALID_PROPERTY_TYPE",
                        "Property '" + prefix + name + "' must be of type " + want + ".",
                        "See " + schema + " in docs/contract.json."));
            }
            if (want.equals("string") && ((String) v).isEmpty()) {
                return new Reply(400, error("LCM_EMPTY_PROPERTY",
                        "Property '" + prefix + name + "' was sent as an empty string.",
                        "Omit optional properties that have no value instead of sending an empty string."));
            }
        }
        return null;
    }

    private static boolean typeOk(String want, Object v) {
        if (want == null) {
            return false;
        }
        switch (want) {
            case "string":
                return v instanceof String;
            case "object":
                return v instanceof Map;
            case "array":
                return v instanceof List;
            case "boolean":
                return v instanceof Boolean;
            case "number":
                return v instanceof Number;
            default:
                return false;
        }
    }

    private static Set<String> set(String... names) {
        return new LinkedHashSet<>(Arrays.asList(names));
    }

    private static Map<String, String> typeMap(String... pairs) {
        Map<String, String> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < pairs.length; i += 2) {
            m.put(pairs[i], pairs[i + 1]);
        }
        return m;
    }

    private Map<String, Object> error(String code, String message, String resolution) {
        Map<String, Object> e = Json.obj();
        e.put("code", code);
        e.put("message", localizable("com.broadcom.lcm.error." + code.toLowerCase(), message));
        e.put("resolution", localizable("com.broadcom.lcm.resolution." + code.toLowerCase(), resolution));
        e.put("referenceId", "ref-" + Integer.toHexString(Math.abs((code + message).hashCode())));
        e.put("timestamp", T2);
        return e;
    }

    private Map<String, Object> localizable(String id, String text) {
        Map<String, Object> m = Json.obj();
        m.put("id", id);
        m.put("defaultMessage", text);
        m.put("localizedMessage", text);
        return m;
    }

    // ------------------------------------------------------------- plumbing

    private static final class Reply {
        final int status;
        final Map<String, Object> body;

        Reply(int status, Map<String, Object> body) {
            this.status = status;
            this.body = body;
        }
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> q = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return q;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int i = pair.indexOf('=');
            if (i < 0) {
                q.put(decode(pair), "");
            } else {
                q.put(decode(pair.substring(0, i)), decode(pair.substring(i + 1)));
            }
        }
        return q;
    }

    private static String decode(String s) {
        return URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    private static final List<String> LOGGED_HEADERS = Arrays.asList(
            "Authorization", "Content-Type", "Accept", "X-Correlation-Id");

    private void log(int n, String method, String path, String rawQuery, Map<String, String> query,
                     String operationId, Headers headers, Object body, String rawBody, int status) {
        Map<String, Object> entry = Json.obj();
        entry.put("seq", n);
        entry.put("operationId", operationId);
        entry.put("method", method);
        entry.put("path", path);
        entry.put("rawQuery", rawQuery);
        Map<String, Object> q = Json.obj();
        q.putAll(query);
        entry.put("query", q);
        Map<String, Object> h = Json.obj();
        for (String name : LOGGED_HEADERS) {
            List<String> values = headers.get(name);
            h.put(name, values == null || values.isEmpty() ? null : values.get(0));
        }
        entry.put("headers", h);
        entry.put("headerNames", new ArrayList<Object>(headers.keySet()));
        entry.put("rawBody", rawBody);
        entry.put("body", body);
        entry.put("responseStatus", status);
        try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(logPath,
                StandardCharsets.UTF_8, StandardOpenOption.APPEND))) {
            w.println(Json.write(entry));
        } catch (IOException e) {
            throw new IllegalStateException("cannot append to request log", e);
        }
    }
}
