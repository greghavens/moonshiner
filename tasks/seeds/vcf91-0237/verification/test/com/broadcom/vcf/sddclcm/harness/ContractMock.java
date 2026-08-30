package com.broadcom.vcf.sddclcm.harness;

import com.broadcom.vcf.sddclcm.Json;
import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
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
import java.util.Objects;

/**
 * Contract-pinned loopback SDDC LCM fixture.
 *
 * <p>The fixture binds an ephemeral {@code 127.0.0.1} port and serves only the
 * operations named in {@code docs/contract.json}: it loads the method, path
 * template, authentication requirement, declared parameters and request body
 * presence straight out of that projection, and answers everything else with the
 * projected {@code ErrorResponse} shape. No live VMware endpoint is contacted.
 *
 * <p>Every request is appended to a synchronized log the verifier reads.
 *
 * <p>This file is part of the protected harness. Do not modify it.
 */
public final class ContractMock implements AutoCloseable {

    /** Fleet component under lifecycle in this fixture. */
    public static final String COMPONENT_ID = "2f1c0f6a-9a3e-4e0e-8f2a-6f0d3a5c7b41";
    public static final String COMPONENT_TYPE = "VCF_OPERATIONS";
    public static final String COMPONENT_FQDN = "ops-a.vcf.example.com";
    public static final String CURRENT_VERSION = "9.1.0.0000.24000001";
    public static final String TARGET_VERSION = "9.1.1.0000.24500123";

    /** Instance-scoped component that a {@code scope=FLEET} listing must not return. */
    public static final String INSTANCE_COMPONENT_ID = "9d5e7f21-4b0c-4a6e-bb31-8c2d4e6f7a90";
    public static final String INSTANCE_COMPONENT_TYPE = "VCF_AUTOMATION";

    public static final String FLEET_DEPOT_FQDN = "fleet-depot.vcf.example.com";
    public static final String FLEET_DEPOT_CERTIFICATE =
            "-----BEGIN CERTIFICATE-----\nMIIBFleetDepotFixtureCertificateChain\n-----END CERTIFICATE-----\n";
    public static final String RESOLVED_BINARY_URL =
            "https://fleet-depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS/9.1.1.0000.24500123/upgrade-manifest.json";

    public static final String PRECHECK_TASK_ID = "b0d4d8b0-5c6a-4b2b-9d47-1a2f3c4d5e60";
    public static final String APPLY_TASK_ID = "7c9a2e51-33f4-4a0d-8b1e-5d6c7a8b9c02";
    public static final String CORRELATION_ID = "39ab89c8-a945-4290-9327-13c5bd3f595c";

    /** Protected fault selections layered on top of the pinned contract. */
    public enum Mode {
        /** The initial access token stops being accepted once the precheck task finishes. */
        EXPIRE_DURING_APPLY,
        /** Same expiry, but the replacement token is rejected on the resumed apply too. */
        SECOND_UNAUTHORIZED,
        /** The precheck task reaches a terminal FAILED status. */
        PRECHECK_FAILS,
        /** The precheck task reaches a terminal CANCELED status. */
        PRECHECK_CANCELED,
        /** The apply submission answers HTTP 500 rather than an authentication challenge. */
        APPLY_SERVER_ERROR,
        /** The health operation answers successfully but reports the service as down. */
        HEALTH_DOWN,
        /** A fleet-filtered listing improperly contains an instance-scoped component. */
        NON_FLEET_LISTING,
        /** A fleet-filtered listing contains two matches for the requested component type. */
        DUPLICATE_FLEET_COMPONENT
    }

    /** One logged inbound request and the status the fixture answered with. */
    public record RecordedRequest(
            int sequence,
            String operationId,
            String method,
            String rawTarget,
            String rawPath,
            String rawQuery,
            boolean queryDelimiterPresent,
            Map<String, List<String>> headers,
            String body,
            int responseStatus) {

        /** All values sent for {@code name}, case-insensitively, in wire order. */
        public List<String> headerValues(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue();
                }
            }
            return List.of();
        }

        /** The single value sent for {@code name}, or {@code null} when it was absent. */
        public String header(String name) {
            List<String> values = headerValues(name);
            return values.size() == 1 ? values.get(0) : null;
        }

        public boolean hasHeader(String name) {
            return !headerValues(name).isEmpty();
        }

        @Override
        public String toString() {
            return "#" + sequence + " " + method + " " + rawTarget + " -> " + responseStatus
                    + " [" + (operationId == null ? "unmatched" : operationId) + "]";
        }
    }

    private record ContractOperation(
            String operationId,
            String method,
            String path,
            List<String> segments,
            boolean authenticated,
            boolean hasRequestBody,
            Map<String, Boolean> queryParameters,
            Map<String, Boolean> headerParameters,
            List<String> declaredEnum) {
    }

    private final HttpServer server;
    private final TokenAuthority tokens;
    private final Mode mode;
    private final List<ContractOperation> operations;
    private final List<RecordedRequest> log = Collections.synchronizedList(new ArrayList<>());

    private int precheckPolls;
    private int applyPolls;
    private boolean precheckTerminalServed;

    private ContractMock(HttpServer server, TokenAuthority tokens, Mode mode, List<ContractOperation> operations) {
        this.server = server;
        this.tokens = tokens;
        this.mode = mode;
        this.operations = operations;
    }

    /** Starts the fixture on a loopback ephemeral port using the routes named in {@code contractPath}. */
    public static ContractMock start(Path contractPath, TokenAuthority tokens, Mode mode) throws IOException {
        List<ContractOperation> operations = loadContract(contractPath);
        InetAddress ipv4Loopback = InetAddress.getByAddress(new byte[] {127, 0, 0, 1});
        HttpServer server = HttpServer.create(new InetSocketAddress(ipv4Loopback, 0), 0);
        ContractMock mock = new ContractMock(server, tokens, mode, operations);
        server.createContext("/", mock::dispatch);
        server.setExecutor(null);
        server.start();
        return mock;
    }

    /** Loopback service root, for example {@code http://127.0.0.1:54321}. */
    public String serviceRootUrl() {
        return "http://" + server.getAddress().getAddress().getHostAddress() + ":" + server.getAddress().getPort();
    }

    /** Immutable snapshot of the request log in wire order. */
    public List<RecordedRequest> requestLog() {
        synchronized (log) {
            return List.copyOf(log);
        }
    }

    /** Writes the request log to {@code target} so a failing run can be inspected. */
    public void writeRequestLog(Path target) throws IOException {
        List<Object> entries = Json.array();
        for (RecordedRequest request : requestLog()) {
            Map<String, Object> entry = Json.object();
            entry.put("sequence", (long) request.sequence());
            entry.put("operationId", request.operationId());
            entry.put("method", request.method());
            entry.put("rawTarget", request.rawTarget());
            entry.put("responseStatus", (long) request.responseStatus());
            Map<String, Object> headers = Json.object();
            request.headers().forEach((name, values) -> headers.put(name, List.copyOf(values)));
            entry.put("headers", headers);
            entry.put("body", request.body());
            entries.add(entry);
        }
        Files.createDirectories(target.getParent());
        Files.writeString(target, Json.write(entries), StandardCharsets.UTF_8);
    }

    @Override
    public void close() {
        server.stop(0);
    }

    // ---------------------------------------------------------------- routing

    private static List<ContractOperation> loadContract(Path contractPath) throws IOException {
        Map<String, Object> contract = Json.parseObject(Files.readString(contractPath, StandardCharsets.UTF_8));
        Object rawOperations = contract.get("operations");
        if (!(rawOperations instanceof List<?> list)) {
            throw new IOException("docs/contract.json declares no operations array");
        }
        List<ContractOperation> operations = new ArrayList<>();
        for (Object rawOperation : list) {
            if (!(rawOperation instanceof Map<?, ?> operation)) {
                throw new IOException("docs/contract.json holds a malformed operation entry");
            }
            String operationId = String.valueOf(operation.get("operationId"));
            String method = String.valueOf(operation.get("method")).toUpperCase(Locale.ROOT);
            String path = String.valueOf(operation.get("path"));
            boolean authenticated = Boolean.TRUE.equals(operation.get("authenticated"));
            boolean hasRequestBody = operation.get("requestBody") instanceof Map;
            Map<String, Boolean> queryParameters = new LinkedHashMap<>();
            Map<String, Boolean> headerParameters = new LinkedHashMap<>();
            List<String> declaredEnum = new ArrayList<>();
            if (operation.get("parameters") instanceof List<?> parameters) {
                for (Object rawParameter : parameters) {
                    if (!(rawParameter instanceof Map<?, ?> parameter)) {
                        continue;
                    }
                    String name = String.valueOf(parameter.get("name"));
                    boolean required = Boolean.TRUE.equals(parameter.get("required"));
                    String in = String.valueOf(parameter.get("in"));
                    if ("query".equals(in)) {
                        queryParameters.put(name, required);
                        if (parameter.get("schema") instanceof Map<?, ?> schema
                                && schema.get("enum") instanceof List<?> values) {
                            for (Object value : values) {
                                declaredEnum.add(name + "=" + value);
                            }
                        }
                    } else if ("header".equals(in)) {
                        headerParameters.put(name, required);
                    }
                }
            }
            operations.add(new ContractOperation(operationId, method, path, splitSegments(path),
                    authenticated, hasRequestBody, queryParameters, headerParameters, declaredEnum));
        }
        return List.copyOf(operations);
    }

    private static List<String> splitSegments(String path) {
        List<String> segments = new ArrayList<>();
        for (String segment : path.split("/")) {
            if (!segment.isEmpty()) {
                segments.add(segment);
            }
        }
        return segments;
    }

    private void dispatch(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String rawTarget = exchange.getRequestURI().toString();
        boolean queryDelimiterPresent = rawTarget.indexOf('?') >= 0;
        Map<String, List<String>> headers = snapshotHeaders(exchange.getRequestHeaders());
        String body = readBody(exchange);

        int status;
        String payload;
        String operationId = null;
        try {
            ContractOperation operation = match(method, rawPath);
            if (operation == null) {
                status = 404;
                payload = errorResponse("SDDC_LCM_UNKNOWN_ROUTE",
                        "No contract operation serves " + method + " " + rawPath + ".");
            } else {
                operationId = operation.operationId();
                Response response = serve(operation, rawPath, rawQuery, headers, body);
                status = response.status();
                payload = response.payload();
            }
        } catch (RuntimeException failure) {
            status = 500;
            payload = errorResponse("SDDC_LCM_FIXTURE_FAILURE", String.valueOf(failure.getMessage()));
        }

        synchronized (log) {
            log.add(new RecordedRequest(log.size(), operationId, method, rawTarget, rawPath, rawQuery,
                    queryDelimiterPresent, headers, body, status));
        }

        if ("getTask".equals(operationId) && status == 200 && precheckTerminalServed
                && !tokens.initialAccessTokenExpired() && mode != Mode.APPLY_SERVER_ERROR) {
            tokens.expireInitialAccessToken();
        }

        // Publish all state caused by this request before releasing the response
        // to the client. This keeps the next request and the verifier's final log
        // snapshot from racing the fixture handler.
        byte[] encoded = payload.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, encoded.length);
        exchange.getResponseBody().write(encoded);
        exchange.close();
    }

    private ContractOperation match(String method, String rawPath) {
        List<String> requested = splitSegments(rawPath);
        for (ContractOperation operation : operations) {
            if (!operation.method().equalsIgnoreCase(method)) {
                continue;
            }
            List<String> template = operation.segments();
            if (template.size() != requested.size()) {
                continue;
            }
            boolean matches = true;
            for (int index = 0; index < template.size(); index++) {
                String templateSegment = template.get(index);
                if (templateSegment.startsWith("{") && templateSegment.endsWith("}")) {
                    if (requested.get(index).isEmpty()) {
                        matches = false;
                        break;
                    }
                    continue;
                }
                if (!templateSegment.equals(requested.get(index))) {
                    matches = false;
                    break;
                }
            }
            if (matches) {
                return operation;
            }
        }
        return null;
    }

    private record Response(int status, String payload) {
    }

    private Response serve(ContractOperation operation, String rawPath, String rawQuery,
                           Map<String, List<String>> headers, String body) {
        List<String> authorization = valuesOf(headers, "Authorization");
        if (operation.authenticated()) {
            if (authorization.size() != 1) {
                return new Response(401, errorResponse("SDDC_LCM_UNAUTHORIZED",
                        "Expected exactly one Authorization header, saw " + authorization.size() + "."));
            }
            String value = authorization.get(0);
            if (!value.startsWith("Bearer ")) {
                return new Response(401, errorResponse("SDDC_LCM_UNAUTHORIZED",
                        "Authorization must use the Bearer scheme declared by bearerToken."));
            }
            if (!tokens.accepts(value.substring("Bearer ".length()))) {
                return new Response(401, errorResponse("SDDC_LCM_TOKEN_EXPIRED",
                        "The access token presented for " + operation.operationId() + " is no longer valid."));
            }
        } else if (!authorization.isEmpty()) {
            return new Response(400, errorResponse("SDDC_LCM_UNEXPECTED_AUTHORIZATION",
                    operation.operationId() + " declares an empty security list and takes no Authorization header."));
        }

        Map<String, List<String>> query = parseQuery(rawQuery);
        for (String name : query.keySet()) {
            if (!operation.queryParameters().containsKey(name)) {
                return new Response(400, errorResponse("SDDC_LCM_UNDECLARED_QUERY_PARAMETER",
                        operation.operationId() + " does not declare the query parameter '" + name + "'."));
            }
        }
        for (Map.Entry<String, Boolean> declared : operation.queryParameters().entrySet()) {
            if (Boolean.TRUE.equals(declared.getValue()) && !query.containsKey(declared.getKey())) {
                return new Response(400, errorResponse("SDDC_LCM_MISSING_QUERY_PARAMETER",
                        operation.operationId() + " requires the query parameter '" + declared.getKey() + "'."));
            }
            List<String> values = query.getOrDefault(declared.getKey(), List.of());
            if (values.size() > 1) {
                return new Response(400, errorResponse("SDDC_LCM_REPEATED_QUERY_PARAMETER",
                        "The query parameter '" + declared.getKey() + "' was sent " + values.size() + " times."));
            }
            for (String value : values) {
                if (!operation.declaredEnum().isEmpty()
                        && operation.declaredEnum().stream().anyMatch(entry -> entry.startsWith(declared.getKey() + "="))
                        && !operation.declaredEnum().contains(declared.getKey() + "=" + value)) {
                    return new Response(400, errorResponse("SDDC_LCM_INVALID_QUERY_PARAMETER",
                            "'" + value + "' is not an enumerated value of '" + declared.getKey() + "'."));
                }
            }
        }
        for (String name : headers.keySet()) {
            if (name.toLowerCase(Locale.ROOT).startsWith("x-")
                    && operation.headerParameters().keySet().stream().noneMatch(name::equalsIgnoreCase)) {
                return new Response(400, errorResponse("SDDC_LCM_UNDECLARED_HEADER_PARAMETER",
                        operation.operationId() + " does not declare the header parameter '" + name + "'."));
            }
        }

        List<String> contentType = valuesOf(headers, "Content-Type");
        if (operation.hasRequestBody()) {
            if (contentType.size() != 1 || !contentType.get(0).toLowerCase(Locale.ROOT).startsWith("application/json")) {
                return new Response(400, errorResponse("SDDC_LCM_UNSUPPORTED_MEDIA_TYPE",
                        operation.operationId() + " expects exactly one application/json Content-Type header."));
            }
        } else {
            if (!contentType.isEmpty()) {
                return new Response(400, errorResponse("SDDC_LCM_UNEXPECTED_CONTENT_TYPE",
                        operation.operationId() + " takes no request body and must not send Content-Type."));
            }
            if (!body.isEmpty()) {
                return new Response(400, errorResponse("SDDC_LCM_UNEXPECTED_BODY",
                        operation.operationId() + " takes no request body."));
            }
        }

        return switch (operation.operationId()) {
            case "getHealth" -> serveHealth();
            case "getComponents" -> serveComponents(query);
            case "resolveDepotComponents" -> serveDepotResolution(body);
            case "performComponentAction" -> serveComponentAction(rawPath, query, headers, body);
            case "getTask" -> serveTask(rawPath);
            default -> new Response(404, errorResponse("SDDC_LCM_UNSERVED_OPERATION",
                    "The fixture serves no behaviour for " + operation.operationId() + "."));
        };
    }

    // ------------------------------------------------------------- behaviours

    private Response serveHealth() {
        Map<String, Object> health = Json.object();
        health.put("up", mode != Mode.HEALTH_DOWN);
        return new Response(200, Json.write(health));
    }

    private Response serveComponents(Map<String, List<String>> query) {
        String scope = query.containsKey("scope") ? query.get("scope").get(0) : null;
        List<Object> components = Json.array();
        if (scope == null || "FLEET".equals(scope)) {
            if (mode == Mode.NON_FLEET_LISTING) {
                components.add(instanceComponent());
            } else {
                components.add(fleetComponent());
                if (mode == Mode.DUPLICATE_FLEET_COMPONENT) {
                    components.add(fleetComponent());
                }
            }
        }
        if (scope == null || "INSTANCE".equals(scope)) {
            components.add(instanceComponent());
        }
        Map<String, Object> payload = Json.object();
        payload.put("components", components);
        return new Response(200, Json.write(payload));
    }

    private Map<String, Object> fleetComponent() {
        Map<String, Object> component = Json.object();
        component.put("id", COMPONENT_ID);
        component.put("componentType", COMPONENT_TYPE);
        component.put("deploymentType", "OVA");
        component.put("version", CURRENT_VERSION);
        component.put("size", "Medium");
        component.put("fqdn", COMPONENT_FQDN);
        component.put("scope", "FLEET");
        return component;
    }

    private Map<String, Object> instanceComponent() {
        Map<String, Object> component = Json.object();
        component.put("id", INSTANCE_COMPONENT_ID);
        component.put("componentType", INSTANCE_COMPONENT_TYPE);
        component.put("deploymentType", "VSP");
        component.put("version", "9.1.0.0000.24000004");
        component.put("size", "Small");
        component.put("fqdn", "automation-a.vcf.example.com");
        component.put("scope", "INSTANCE");
        return component;
    }

    private Response serveDepotResolution(String body) {
        Map<String, Object> request;
        try {
            request = Json.parseObject(body);
        } catch (RuntimeException failure) {
            return new Response(400, errorResponse("SDDC_LCM_MALFORMED_BODY", "DepotComponentsSpec is not valid JSON."));
        }
        for (String member : request.keySet()) {
            if (!List.of("fleetDepotSpec", "version", "componentVersions").contains(member)) {
                return new Response(400, errorResponse("SDDC_LCM_UNDECLARED_MEMBER",
                        "DepotComponentsSpec does not declare the member '" + member + "'."));
            }
        }
        if (!(request.get("fleetDepotSpec") instanceof Map<?, ?> depot)) {
            return new Response(400, errorResponse("SDDC_LCM_MISSING_MEMBER",
                    "DepotComponentsSpec requires the member 'fleetDepotSpec'."));
        }
        if (!FLEET_DEPOT_FQDN.equals(depot.get("fqdn"))) {
            return new Response(400, errorResponse("SDDC_LCM_UNKNOWN_DEPOT",
                    "No Fleet Depot is registered at '" + depot.get("fqdn") + "'."));
        }
        if (!(depot.get("certificate") instanceof String certificate) || certificate.isBlank()) {
            return new Response(400, errorResponse("SDDC_LCM_MISSING_MEMBER",
                    "FleetDepotSpec requires a PEM encoded 'certificate'."));
        }
        if (!(request.get("componentVersions") instanceof List<?> componentVersions) || componentVersions.isEmpty()) {
            return new Response(400, errorResponse("SDDC_LCM_MISSING_MEMBER",
                    "DepotComponentsSpec requires a non-empty 'componentVersions'."));
        }
        List<Object> resolved = Json.array();
        for (Object rawEntry : componentVersions) {
            if (!(rawEntry instanceof Map<?, ?> entry)) {
                return new Response(400, errorResponse("SDDC_LCM_MALFORMED_BODY",
                        "componentVersions holds a non-object entry."));
            }
            if (!COMPONENT_TYPE.equals(entry.get("component")) || !TARGET_VERSION.equals(entry.get("version"))) {
                return new Response(400, errorResponse("SDDC_LCM_UNRESOLVABLE_VERSION",
                        "The depot publishes no binary for " + entry.get("component") + " " + entry.get("version") + "."));
            }
            Map<String, Object> resolution = Json.object();
            resolution.put("component", COMPONENT_TYPE);
            resolution.put("version", TARGET_VERSION);
            resolution.put("binaryUrl", RESOLVED_BINARY_URL);
            resolved.add(resolution);
        }
        Map<String, Object> payload = Json.object();
        payload.put("componentVersions", resolved);
        return new Response(200, Json.write(payload));
    }

    private Response serveComponentAction(String rawPath, Map<String, List<String>> query,
                                          Map<String, List<String>> headers, String body) {
        String componentId = lastSegment(rawPath);
        if (!COMPONENT_ID.equals(componentId)) {
            return new Response(404, errorResponse("SDDC_LCM_COMPONENT_NOT_FOUND",
                    "No component is registered with identifier '" + componentId + "'."));
        }
        String action = query.get("action").get(0);
        Map<String, Object> request;
        try {
            request = Json.parseObject(body);
        } catch (RuntimeException failure) {
            return new Response(400, errorResponse("SDDC_LCM_MALFORMED_BODY", "ComponentUpgradeSpec is not valid JSON."));
        }
        for (String member : request.keySet()) {
            if (!List.of("componentSpec", "lcmPlatformSpec", "correlationId").contains(member)) {
                return new Response(400, errorResponse("SDDC_LCM_UNDECLARED_MEMBER",
                        "ComponentUpgradeSpec does not declare the member '" + member + "'."));
            }
        }
        if (!(request.get("componentSpec") instanceof Map<?, ?> componentSpec)) {
            return new Response(400, errorResponse("SDDC_LCM_MISSING_MEMBER",
                    "ComponentUpgradeSpec requires the member 'componentSpec'."));
        }
        if (!(componentSpec.get("software") instanceof Map<?, ?> software)
                || !TARGET_VERSION.equals(software.get("version"))) {
            return new Response(400, errorResponse("SDDC_LCM_UNSUPPORTED_TARGET_VERSION",
                    "SoftwareSpec.version must name a version the depot resolved."));
        }
        if (!(componentSpec.get("depot") instanceof Map<?, ?> depot)
                || !RESOLVED_BINARY_URL.equals(depot.get("url"))) {
            return new Response(400, errorResponse("SDDC_LCM_UNRESOLVED_DEPOT_URL",
                    "DepotSpec.url must carry the binary url resolved for the target version."));
        }
        String correlationHeader = singleValue(headers, "X-Correlation-Id");
        Object correlationMember = request.get("correlationId");
        if (correlationMember != null && !correlationMember.equals(correlationHeader)) {
            return new Response(400, errorResponse("SDDC_LCM_CORRELATION_MISMATCH",
                    "ComponentUpgradeSpec.correlationId and X-Correlation-Id disagree."));
        }

        if ("precheck".equals(action)) {
            return new Response(202, Json.write(taskDocument(PRECHECK_TASK_ID, "precheck", "PENDING",
                    correlationMember)));
        }
        if ("apply".equals(action)) {
            if (mode == Mode.APPLY_SERVER_ERROR) {
                return new Response(500, errorResponse("SDDC_LCM_INTERNAL_ERROR",
                        "The lifecycle orchestrator is temporarily unavailable."));
            }
            if (mode == Mode.SECOND_UNAUTHORIZED) {
                tokens.expireReplacementAccessToken();
                return new Response(401, errorResponse("SDDC_LCM_TOKEN_EXPIRED",
                        "The access token presented for performComponentAction is no longer valid."));
            }
            if (!(request.get("lcmPlatformSpec") instanceof Map<?, ?> platform)
                    || !(platform.get("performBackup") instanceof Boolean)) {
                return new Response(400, errorResponse("SDDC_LCM_MISSING_MEMBER",
                        "An apply action requires LcmPlatformSpec.performBackup."));
            }
            return new Response(202, Json.write(taskDocument(APPLY_TASK_ID, "apply", "PENDING", correlationMember)));
        }
        return new Response(400, errorResponse("SDDC_LCM_UNSUPPORTED_ACTION",
                "The fixture serves no behaviour for the '" + action + "' action."));
    }

    private Response serveTask(String rawPath) {
        String taskId = lastSegment(rawPath);
        if (PRECHECK_TASK_ID.equals(taskId)) {
            precheckPolls++;
            if (precheckPolls == 1) {
                return new Response(200, Json.write(taskDocument(PRECHECK_TASK_ID, "precheck", "RUNNING", null)));
            }
            String terminal = switch (mode) {
                case PRECHECK_FAILS -> "FAILED";
                case PRECHECK_CANCELED -> "CANCELED";
                default -> "SUCCEEDED";
            };
            precheckTerminalServed = true;
            return new Response(200, Json.write(taskDocument(PRECHECK_TASK_ID, "precheck", terminal, null)));
        }
        if (APPLY_TASK_ID.equals(taskId)) {
            applyPolls++;
            String status = applyPolls == 1 ? "RUNNING" : "SUCCEEDED";
            return new Response(200, Json.write(taskDocument(APPLY_TASK_ID, "apply", status, CORRELATION_ID)));
        }
        return new Response(404, errorResponse("SDDC_LCM_TASK_NOT_FOUND",
                "No task is registered with identifier '" + taskId + "'."));
    }

    private Map<String, Object> taskDocument(String taskId, String type, String status, Object correlationId) {
        boolean terminal = List.of("SUCCEEDED", "FAILED", "CANCELED").contains(status);
        Map<String, Object> task = Json.object();
        task.put("id", taskId);
        task.put("name", "vcf_operations_910_to_911_" + type);
        task.put("description", localizableMessage(
                "com.broadcom.lcm.sddc.component." + type,
                "Component " + type + " for " + COMPONENT_FQDN));
        task.put("status", status);
        task.put("type", type);
        task.put("resourceId", COMPONENT_ID);
        task.put("resourceType", "COMPONENT");
        task.put("createTime", "2026-05-20T09:14:02.000Z");
        task.put("startTime", "2026-05-20T09:14:03.000Z");
        task.put("updateTime", "2026-05-20T09:16:41.000Z");
        if (terminal) {
            task.put("endTime", "2026-05-20T09:16:41.000Z");
        }
        if (correlationId != null) {
            task.put("correlationId", correlationId);
        }
        task.put("retriable", "FAILED".equals(status));
        task.put("cancellable", !terminal);
        return task;
    }

    private static Map<String, Object> localizableMessage(String id, String message) {
        Map<String, Object> localizable = Json.object();
        localizable.put("id", id);
        localizable.put("defaultMessage", message);
        localizable.put("localizedMessage", message);
        return localizable;
    }

    private String errorResponse(String code, String detail) {
        Map<String, Object> error = Json.object();
        error.put("code", code);
        error.put("message", localizableMessage("com.broadcom.lcm.sddc.error", detail));
        error.put("resolution", localizableMessage("com.broadcom.lcm.sddc.error.resolution",
                "Correct the request and retry."));
        error.put("referenceId", "fixture-" + Integer.toHexString(Objects.hash(code, detail)));
        error.put("timestamp", "2026-05-20T09:14:02.000Z");
        error.put("detail", detail);
        return Json.write(error);
    }

    // ------------------------------------------------------------- primitives

    private static String lastSegment(String rawPath) {
        List<String> segments = splitSegments(rawPath);
        return segments.isEmpty() ? "" : URLDecoder.decode(segments.get(segments.size() - 1), StandardCharsets.UTF_8);
    }

    private static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> query = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return query;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                query.computeIfAbsent("", key -> new ArrayList<>()).add("");
                continue;
            }
            int separator = pair.indexOf('=');
            String name = separator < 0 ? pair : pair.substring(0, separator);
            String value = separator < 0 ? "" : pair.substring(separator + 1);
            query.computeIfAbsent(URLDecoder.decode(name, StandardCharsets.UTF_8), key -> new ArrayList<>())
                    .add(URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return query;
    }

    private static Map<String, List<String>> snapshotHeaders(Headers headers) {
        Map<String, List<String>> snapshot = new LinkedHashMap<>();
        headers.forEach((name, values) -> snapshot.put(name, List.copyOf(values)));
        return Collections.unmodifiableMap(snapshot);
    }

    private static List<String> valuesOf(Map<String, List<String>> headers, String name) {
        for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
            if (entry.getKey().equalsIgnoreCase(name)) {
                return entry.getValue();
            }
        }
        return List.of();
    }

    private static String singleValue(Map<String, List<String>> headers, String name) {
        List<String> values = valuesOf(headers, name);
        return values.size() == 1 ? values.get(0) : null;
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream stream = exchange.getRequestBody()) {
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }
}
