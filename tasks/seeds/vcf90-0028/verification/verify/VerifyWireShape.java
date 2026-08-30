import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Deterministic verifier. It contacts nothing: every assertion is made against the files the
 * harness wrote ({@code out/requests.json}, {@code out/state.json}, {@code out/result.json}), the
 * pinned contract and the client source.
 *
 * <p>Exit code 0 means every check passed; 1 means at least one failed.
 *
 * <p>Harness file. Do not modify.
 */
public final class VerifyWireShape {

    private static final List<String> checks = new ArrayList<>();
    private static int failures;

    public static void main(String[] args) throws Exception {
        Path root = Paths.get(".").toAbsolutePath().normalize();
        System.out.println("VMware Cloud Foundation 9.0 network pool reconciler - verification");
        System.out.println("working directory: " + root);
        System.out.println();

        verifyProtectedFiles();
        verifyClientSourceShape();

        Map<String, Object> result = readObject("out/result.json");
        Map<String, Object> state = readObject("out/state.json");
        List<Object> log = readArray("out/requests.json");

        verifyOutcome(result, state);
        verifyApplianceState(state, result);
        verifyOnlyContractOperations(log);
        verifyNoRejectedRequests(log);
        verifyTokenRequests(log);
        verifyCreateRequest(log);
        verifyRetrySafety(log);
        verifyHeaders(log);
        verifyAdditionalFailureScenarios();

        System.out.println();
        for (String line : checks) {
            System.out.println(line);
        }
        System.out.println();
        if (failures == 0) {
            System.out.println("RESULT: PASS (" + checks.size() + " checks)");
            return;
        }
        System.out.println("RESULT: FAIL (" + failures + " of " + checks.size()
                + " checks failed)");
        System.exit(1);
    }

    // ---------------------------------------------------------------- checks

    private static void verifyProtectedFiles() throws Exception {
        Path list = Paths.get("harness", "protected.sha256");
        if (!Files.exists(list)) {
            fail("protected files", "harness/protected.sha256 is missing");
            return;
        }
        List<String> broken = new ArrayList<>();
        for (String line : Files.readAllLines(list, StandardCharsets.UTF_8)) {
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith("#")) {
                continue;
            }
            int split = trimmed.indexOf(' ');
            String expected = trimmed.substring(0, split);
            String file = trimmed.substring(split).trim();
            Path path = Paths.get(file);
            if (!Files.exists(path)) {
                broken.add(file + " (missing)");
                continue;
            }
            if (!expected.equals(sha256(path))) {
                broken.add(file + " (modified)");
            }
        }
        check("protected harness, contract and verifier files are unmodified", broken.isEmpty(),
                "these protected files no longer match the seed: " + broken);
    }

    private static void verifyClientSourceShape() throws IOException {
        Path clientPath = Paths.get("src", "VcfNetworkPoolClient.java");
        if (!Files.exists(clientPath)) {
            fail("client source", "src/VcfNetworkPoolClient.java is missing");
            return;
        }
        List<Path> sources;
        try (Stream<Path> walk = Files.walk(Paths.get("src"))) {
            sources = walk.filter(p -> p.toString().endsWith(".java")).sorted().toList();
        }
        check("the client is a single source file", sources.size() == 1,
                "expected only src/VcfNetworkPoolClient.java under src/ but found " + sources);

        String source = Files.readString(clientPath, StandardCharsets.UTF_8);
        List<String> foreignImports = new ArrayList<>();
        for (String line : source.lines().map(String::strip).toList()) {
            if (!line.startsWith("import ")) {
                continue;
            }
            String imported = line.substring("import ".length()).replaceFirst("^static ", "");
            if (!imported.startsWith("java.") && !imported.startsWith("javax.")) {
                foreignImports.add(line);
            }
        }
        check("the client imports only the Java standard library", foreignImports.isEmpty(),
                "unexpected imports: " + foreignImports);

        List<String> borrowed = new ArrayList<>();
        for (String type : List.of("MiniJson", "Fixture", "MockSddcManager", "TestMain",
                "VerifyWireShape")) {
            if (source.contains(type)) {
                borrowed.add(type);
            }
        }
        check("the client does not reuse harness types", borrowed.isEmpty(),
                "the client references harness-owned types " + borrowed
                        + "; it must serialize and parse JSON on its own");
    }

    private static void verifyOutcome(Map<String, Object> result, Map<String, Object> state) {
        boolean ok = "ok".equals(result.get("status"));
        check("the reconciliation completed without propagating the 502", ok,
                "the client failed with " + result.get("errorClass") + ": " + result.get("error"));
        if (!ok) {
            return;
        }
        Object first = result.get("firstId");
        Object second = result.get("secondId");
        check("both reconciliations returned the same network pool id",
                first != null && first.equals(second),
                "first call returned " + MiniJson.render(first) + " and the second returned "
                        + MiniJson.render(second));
    }

    @SuppressWarnings("unchecked")
    private static void verifyApplianceState(Map<String, Object> state, Map<String, Object> result) {
        List<Object> pools = (List<Object>) state.get("networkPools");
        List<String> names = new ArrayList<>();
        for (Object pool : pools) {
            names.add(String.valueOf(((Map<String, Object>) pool).get("name")));
        }
        check("the appliance holds exactly one network pool", pools.size() == 1,
                pools.isEmpty()
                        ? "the appliance holds no network pool at all"
                        : "the appliance holds " + pools.size() + " network pools named " + names
                                + "; a retried create duplicated the effect of the operation");
        if (pools.size() != 1) {
            return;
        }
        Map<String, Object> pool = (Map<String, Object>) pools.get(0);
        check("the surviving pool is the requested one",
                Fixture.POOL_NAME.equals(pool.get("name")),
                "expected a pool named " + Fixture.POOL_NAME + " but found "
                        + MiniJson.render(pool.get("name")));
        Object id = pool.get("id");
        check("the id returned to the caller is the id of the pool that exists",
                id != null && id.equals(result.get("firstId")),
                "the appliance stored id " + MiniJson.render(id) + " but the client returned "
                        + MiniJson.render(result.get("firstId")));
        List<Object> networks = (List<Object>) pool.get("networks");
        check("the stored pool carries both requested networks", networks.size() == 2,
                "expected 2 networks but the appliance stored " + networks.size());
    }

    private static void verifyOnlyContractOperations(List<Object> log) throws IOException {
        Map<String, Object> contract = readObject("docs/contract.json");
        @SuppressWarnings("unchecked")
        Map<String, Object> operations = (Map<String, Object>) contract.get("operations");
        List<String> stray = new ArrayList<>();
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            Object op = entry.get("matchedOperationId");
            if (op == null || !operations.containsKey(String.valueOf(op))) {
                stray.add("#" + entry.get("seq") + " " + entry.get("method") + " "
                        + entry.get("path"));
            }
        }
        check("every request targeted an operation named by the contract", stray.isEmpty(),
                "these requests hit no contract operation: " + stray);

        List<String> withQuery = new ArrayList<>();
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            if (entry.get("query") != null) {
                withQuery.add("#" + entry.get("seq") + " ?" + entry.get("query"));
            }
        }
        check("no request carried query parameters the contract does not define",
                withQuery.isEmpty(), "unexpected query strings: " + withQuery);
    }

    private static void verifyNoRejectedRequests(List<Object> log) {
        List<String> rejected = new ArrayList<>();
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            long status = ((Number) entry.get("responseStatus")).longValue();
            boolean injected = status == 502 && "createNetworkPool".equals(
                    entry.get("matchedOperationId"));
            if (status >= 300 && !injected) {
                rejected.add("#" + entry.get("seq") + " " + entry.get("method") + " "
                        + entry.get("path") + " -> " + status + " " + describeError(entry));
            }
        }
        check("the appliance rejected no request", rejected.isEmpty(),
                "the appliance answered: " + rejected);
    }

    private static void verifyTokenRequests(List<Object> log) {
        List<Map<String, Object>> tokenRequests = byOperation(log, "createToken");
        check("the client authenticated through createToken", !tokenRequests.isEmpty(),
                "no request reached POST /v1/tokens");
        for (Map<String, Object> entry : tokenRequests) {
            Object body = parsedBody(entry);
            String diff = MiniJson.firstDifference(Fixture.expectedTokenBody(), body, "$");
            check("createToken request #" + entry.get("seq") + " body is exactly "
                            + "{username, password}", diff == null,
                    "TokenCreationSpec leaves apiKey and idToken optional, so they must be "
                            + "omitted, not sent empty. " + diff);
        }
    }

    private static void verifyCreateRequest(List<Object> log) {
        List<Map<String, Object>> creates = byOperation(log, "createNetworkPool");
        check("exactly one createNetworkPool request was sent", creates.size() == 1,
                "the client sent " + creates.size() + " create request(s); the operation has no "
                        + "server side idempotency key, so a second create duplicates the pool");
        if (creates.isEmpty()) {
            return;
        }
        Map<String, Object> entry = creates.get(0);
        Object body = parsedBody(entry);

        String diff = MiniJson.firstDifference(Fixture.expectedCreateNetworkPoolBody(), body, "$");
        check("the createNetworkPool body matches the expected wire shape exactly", diff == null,
                String.valueOf(diff));

        List<String> nulls = new ArrayList<>();
        List<String> foreign = new ArrayList<>();
        List<String> readOnly = new ArrayList<>();
        List<String> emptyContainers = new ArrayList<>();
        scan(body, "$", nulls, foreign, readOnly, emptyContainers);
        check("no property was sent as JSON null", nulls.isEmpty(),
                "null-valued properties must be omitted instead: " + nulls);
        check("no property was sent as an empty array or object", emptyContainers.isEmpty(),
                "an optional property with nothing to say must be omitted, not sent empty: "
                        + emptyContainers);
        check("no appliance-owned (read-only) property was sent", readOnly.isEmpty(),
                "these properties are read-only in the 9.0.0.0 schemas: " + readOnly);
        check("no property from a different specification revision was sent", foreign.isEmpty(),
                "these properties exist only in the 9.1.0.0 revision of "
                        + "sddc-manager-openapi.json: " + foreign);

        Object networks = asMap(body).get("networks");
        if (networks instanceof List<?> list && list.size() == 2) {
            Map<String, Object> vmotion = asMap(list.get(1));
            check("the network with no IP ranges omits ipPools entirely",
                    !vmotion.containsKey("ipPools"),
                    "networks[1].ipPools was sent as " + MiniJson.render(vmotion.get("ipPools")));
            Map<String, Object> vsan = asMap(list.get(0));
            check("vlanId and mtu are JSON numbers",
                    vsan.get("vlanId") instanceof Number && vsan.get("mtu") instanceof Number,
                    "vlanId is " + MiniJson.typeName(vsan.get("vlanId")) + " and mtu is "
                            + MiniJson.typeName(vsan.get("mtu")) + "; both are integer/int32");
        }
    }

    private static void verifyRetrySafety(List<Object> log) {
        List<Map<String, Object>> pools = new ArrayList<>();
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            String op = String.valueOf(entry.get("matchedOperationId"));
            if (op.equals("getNetworkPool") || op.equals("createNetworkPool")) {
                pools.add(entry);
            }
        }
        int createIndex = -1;
        for (int i = 0; i < pools.size(); i++) {
            if ("createNetworkPool".equals(pools.get(i).get("matchedOperationId"))) {
                createIndex = i;
                break;
            }
        }
        check("current state was read before the pool was created", createIndex > 0,
                createIndex < 0
                        ? "no create request was sent"
                        : "createNetworkPool was the first /v1/network-pools request; the client "
                                + "must read state with getNetworkPool before mutating");
        if (createIndex < 0) {
            return;
        }
        boolean hasFollowUp = createIndex + 1 < pools.size();
        check("the client re-read state after the create failed", hasFollowUp
                        && "getNetworkPool".equals(
                                pools.get(createIndex + 1).get("matchedOperationId")),
                hasFollowUp
                        ? "the request after the failed create was "
                                + pools.get(createIndex + 1).get("method") + " "
                                + pools.get(createIndex + 1).get("path")
                        : "the client stopped after the failed create instead of recovering");

        long gets = pools.stream()
                .filter(e -> "getNetworkPool".equals(e.get("matchedOperationId"))).count();
        check("the reconciliation is not chattier than the scenario needs",
                gets >= 2 && gets <= 8,
                "getNetworkPool was called " + gets + " time(s); between 2 and 8 is expected for "
                        + "two reconciliations with one recovery");
    }

    @SuppressWarnings("unchecked")
    private static void verifyAdditionalFailureScenarios() throws IOException {
        Map<String, Object> retryResult = readObject("out/retryable/result.json");
        Map<String, Object> retryState = readObject("out/retryable/state.json");
        List<Object> retryLog = readArray("out/retryable/requests.json");

        verifyOnlyContractOperations(retryLog);
        verifyTokenRequests(retryLog);
        verifyHeaders(retryLog);
        verifyExpectedCreateBodies("retryable scenario", retryLog, 2);

        List<Object> retryPools = (List<Object>) retryState.get("networkPools");
        Object retryId = retryPools.size() == 1
                ? asMap(retryPools.get(0)).get("id") : null;
        check("an uncommitted 503 is retried to a successful reconciliation",
                "ok".equals(retryResult.get("status"))
                        && retryPools.size() == 1
                        && retryId != null
                        && retryId.equals(retryResult.get("firstId")),
                "result was " + MiniJson.render(retryResult) + " and state was "
                        + MiniJson.render(retryState));
        verifyRetryableCreateOrdering(retryLog);
        check("the retryable scenario made only the two bounded create attempts it needed",
                Long.valueOf(2L).equals(retryState.get("createNetworkPoolAttempts")),
                "createNetworkPoolAttempts was "
                        + MiniJson.render(retryState.get("createNetworkPoolAttempts")));

        Map<String, Object> exhaustedResult = readObject("out/exhausted/result.json");
        Map<String, Object> exhaustedState = readObject("out/exhausted/state.json");
        List<Object> exhaustedLog = readArray("out/exhausted/requests.json");

        verifyOnlyContractOperations(exhaustedLog);
        verifyTokenRequests(exhaustedLog);
        verifyHeaders(exhaustedLog);
        verifyCreateBodiesBetween("exhausted-retry scenario", exhaustedLog, 2, 5);

        List<Object> exhaustedPools = (List<Object>) exhaustedState.get("networkPools");
        Object exhaustedAttempts = exhaustedState.get("createNetworkPoolAttempts");
        long exhaustedCount = exhaustedAttempts instanceof Number
                ? ((Number) exhaustedAttempts).longValue() : -1L;
        check("persistent retryable failures are surfaced after a small bounded attempt count",
                "error".equals(exhaustedResult.get("status"))
                        && exhaustedResult.get("errorClass") != null
                        && exhaustedPools.isEmpty()
                        && exhaustedCount >= 2 && exhaustedCount <= 5,
                "result was " + MiniJson.render(exhaustedResult) + " and state was "
                        + MiniJson.render(exhaustedState));
        verifyExhaustedRetryOrdering(exhaustedLog);

        Map<String, Object> permanentResult = readObject("out/permanent/result.json");
        Map<String, Object> permanentState = readObject("out/permanent/state.json");
        List<Object> permanentLog = readArray("out/permanent/requests.json");

        verifyOnlyContractOperations(permanentLog);
        verifyTokenRequests(permanentLog);
        verifyHeaders(permanentLog);
        verifyExpectedCreateBodies("permanent-failure scenario", permanentLog, 1);

        List<Object> permanentPools = (List<Object>) permanentState.get("networkPools");
        check("a permanent 400 is surfaced as an exception rather than a wrong id",
                "error".equals(permanentResult.get("status"))
                        && permanentResult.get("errorClass") != null
                        && permanentPools.isEmpty(),
                "result was " + MiniJson.render(permanentResult) + " and state was "
                        + MiniJson.render(permanentState));
        verifyPermanentCreateOrdering(permanentLog);
        check("the permanent failure was not retried",
                Long.valueOf(1L).equals(permanentState.get("createNetworkPoolAttempts")),
                "createNetworkPoolAttempts was "
                        + MiniJson.render(permanentState.get("createNetworkPoolAttempts")));
    }

    private static void verifyExpectedCreateBodies(String scenario, List<Object> log,
                                                    int expectedCount) {
        verifyCreateBodiesBetween(scenario, log, expectedCount, expectedCount);
    }

    private static void verifyCreateBodiesBetween(String scenario, List<Object> log,
                                                  int minimumCount, int maximumCount) {
        List<Map<String, Object>> creates = byOperation(log, "createNetworkPool");
        List<String> differences = new ArrayList<>();
        for (Map<String, Object> entry : creates) {
            String diff = MiniJson.firstDifference(
                    Fixture.expectedCreateNetworkPoolBody(), parsedBody(entry), "$");
            if (diff != null) {
                differences.add("request #" + entry.get("seq") + ": " + diff);
            }
        }
        check(scenario + " sent the expected number of exact create request bodies",
                creates.size() >= minimumCount && creates.size() <= maximumCount
                        && differences.isEmpty(),
                "expected between " + minimumCount + " and " + maximumCount
                        + " create request(s), found " + creates.size()
                        + "; body differences: " + differences);
    }

    private static void verifyRetryableCreateOrdering(List<Object> log) {
        List<Map<String, Object>> operations = networkOperations(log);
        List<Integer> creates = new ArrayList<>();
        for (int i = 0; i < operations.size(); i++) {
            if ("createNetworkPool".equals(operations.get(i).get("matchedOperationId"))) {
                creates.add(i);
            }
        }
        boolean ordered = creates.size() == 2;
        if (ordered) {
            Map<String, Object> first = operations.get(creates.get(0));
            Map<String, Object> second = operations.get(creates.get(1));
            ordered = creates.get(0) > 0 && creates.get(1) > creates.get(0) + 1
                    && "getNetworkPool".equals(
                            operations.get(creates.get(0) - 1).get("matchedOperationId"))
                    && "getNetworkPool".equals(
                            operations.get(creates.get(1) - 1).get("matchedOperationId"))
                    && Long.valueOf(503L).equals(first.get("responseStatus"))
                    && Long.valueOf(201L).equals(second.get("responseStatus"));
        }
        long gets = operations.stream()
                .filter(e -> "getNetworkPool".equals(e.get("matchedOperationId"))).count();
        check("the retryable create was preceded by a fresh read on both attempts",
                ordered && gets >= 2 && gets <= 8,
                "network-pool operations were " + describeOperations(operations)
                        + "; expected two creates (503 then 201), each immediately preceded by a "
                        + "read, with a small bounded number of reads");
    }

    private static void verifyPermanentCreateOrdering(List<Object> log) {
        List<Map<String, Object>> operations = networkOperations(log);
        int createIndex = -1;
        int creates = 0;
        for (int i = 0; i < operations.size(); i++) {
            if ("createNetworkPool".equals(operations.get(i).get("matchedOperationId"))) {
                creates++;
                createIndex = i;
            }
        }
        boolean ordered = creates == 1 && createIndex > 0 && createIndex == operations.size() - 1
                && "getNetworkPool".equals(
                        operations.get(createIndex - 1).get("matchedOperationId"))
                && Long.valueOf(400L).equals(
                        operations.get(createIndex).get("responseStatus"));
        check("the permanent failure stopped immediately after the first create rejection",
                ordered,
                "network-pool operations were " + describeOperations(operations)
                        + "; expected one read-preceded create ending in 400 and no later operation");
    }

    private static void verifyExhaustedRetryOrdering(List<Object> log) {
        List<Map<String, Object>> operations = networkOperations(log);
        int creates = 0;
        boolean ordered = true;
        for (int i = 0; i < operations.size(); i++) {
            Map<String, Object> entry = operations.get(i);
            String operation = String.valueOf(entry.get("matchedOperationId"));
            long status = ((Number) entry.get("responseStatus")).longValue();
            if (operation.equals("createNetworkPool")) {
                creates++;
                if (i == 0
                        || !"getNetworkPool".equals(
                                operations.get(i - 1).get("matchedOperationId"))
                        || status != 503L) {
                    ordered = false;
                }
            } else if (status != 200L) {
                ordered = false;
            }
        }
        long gets = operations.stream()
                .filter(e -> "getNetworkPool".equals(e.get("matchedOperationId"))).count();
        check("every exhausted create retry was preceded by a fresh state read",
                ordered && creates >= 2 && creates <= 5 && gets >= creates && gets <= 8,
                "network-pool operations were " + describeOperations(operations)
                        + "; expected 2 to 5 create attempts returning 503, each immediately "
                        + "preceded by a successful read");
    }

    private static List<Map<String, Object>> networkOperations(List<Object> log) {
        List<Map<String, Object>> operations = new ArrayList<>();
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            String operation = String.valueOf(entry.get("matchedOperationId"));
            if (operation.equals("getNetworkPool") || operation.equals("createNetworkPool")) {
                operations.add(entry);
            }
        }
        return operations;
    }

    private static List<String> describeOperations(List<Map<String, Object>> operations) {
        List<String> description = new ArrayList<>();
        for (Map<String, Object> entry : operations) {
            description.add(entry.get("matchedOperationId") + "->" + entry.get("responseStatus"));
        }
        return description;
    }

    private static void verifyHeaders(List<Object> log) {
        List<String> problems = new ArrayList<>();
        String currentToken = null;
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            String op = String.valueOf(entry.get("matchedOperationId"));
            Map<String, Object> headers = asMap(entry.get("headers"));
            String where = "#" + entry.get("seq") + " " + op;
            String accept = str(headers.get("accept"));
            if (accept == null || !accept.contains("application/json")) {
                problems.add(where + ": Accept header is " + MiniJson.render(accept)
                        + ", expected application/json");
            }
            if (entry.get("body") != null) {
                String contentType = str(headers.get("content-type"));
                if (contentType == null || !contentType.startsWith("application/json")) {
                    problems.add(where + ": Content-Type header is "
                            + MiniJson.render(contentType) + ", expected application/json");
                }
            }
            if (op.equals("createToken")) {
                if (entry.get("issuedAccessToken") != null) {
                    currentToken = String.valueOf(entry.get("issuedAccessToken"));
                }
                continue;
            }
            String authorization = str(headers.get("authorization"));
            String expected = currentToken == null ? null : "Bearer " + currentToken;
            if (expected == null) {
                problems.add(where + ": sent before any access token was obtained");
            } else if (!expected.equals(authorization)) {
                problems.add(where + ": Authorization header is "
                        + MiniJson.render(authorization) + ", expected "
                        + MiniJson.render(expected));
            }
        }
        check("every request carried the headers the contract requires", problems.isEmpty(),
                String.join("; ", problems));
    }

    // --------------------------------------------------------------- helpers

    @SuppressWarnings("unchecked")
    private static void scan(Object node, String path, List<String> nulls, List<String> foreign,
                             List<String> readOnly, List<String> emptyContainers) {
        if (node instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> e : ((Map<String, Object>) map).entrySet()) {
                String key = String.valueOf(e.getKey());
                String child = path + "." + key;
                Object value = e.getValue();
                if (value == null) {
                    nulls.add(child);
                    continue;
                }
                if (Fixture.FOREIGN_REVISION_PROPERTIES.contains(key)) {
                    foreign.add(child);
                }
                if (List.of("id", "hostsCount", "freeIps", "usedIps").contains(key)) {
                    readOnly.add(child);
                }
                if (value instanceof Map<?, ?> m && m.isEmpty()) {
                    emptyContainers.add(child + " = {}");
                    continue;
                }
                if (value instanceof List<?> l && l.isEmpty()) {
                    emptyContainers.add(child + " = []");
                    continue;
                }
                if (value instanceof String s && s.isEmpty()) {
                    emptyContainers.add(child + " = \"\"");
                    continue;
                }
                scan(value, child, nulls, foreign, readOnly, emptyContainers);
            }
        } else if (node instanceof List<?> list) {
            for (int i = 0; i < list.size(); i++) {
                scan(list.get(i), path + "[" + i + "]", nulls, foreign, readOnly, emptyContainers);
            }
        }
    }

    private static List<Map<String, Object>> byOperation(List<Object> log, String operationId) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object o : log) {
            Map<String, Object> entry = asMap(o);
            if (operationId.equals(entry.get("matchedOperationId"))) {
                out.add(entry);
            }
        }
        return out;
    }

    private static Object parsedBody(Map<String, Object> entry) {
        Object body = entry.get("body");
        if (!(body instanceof String)) {
            return null;
        }
        try {
            return MiniJson.parse((String) body);
        } catch (RuntimeException e) {
            return "<unparseable: " + e.getMessage() + ">";
        }
    }

    private static String describeError(Map<String, Object> entry) {
        Object body = entry.get("body");
        return body == null ? "" : "(request body was " + MiniJson.render(body) + ")";
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asMap(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : new LinkedHashMap<>();
    }

    private static String str(Object o) {
        return o == null ? null : String.valueOf(o);
    }

    private static Map<String, Object> readObject(String path) throws IOException {
        return MiniJson.parseObject(Files.readString(Paths.get(path), StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    private static List<Object> readArray(String path) throws IOException {
        return (List<Object>) MiniJson.parse(
                Files.readString(Paths.get(path), StandardCharsets.UTF_8));
    }

    private static String sha256(Path path) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        byte[] hash = digest.digest(Files.readAllBytes(path));
        StringBuilder sb = new StringBuilder();
        for (byte b : hash) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static void check(String name, boolean ok, String detail) {
        if (ok) {
            checks.add("[ok]   " + name);
        } else {
            failures++;
            checks.add("[FAIL] " + name + "\n         " + detail);
        }
    }

    private static void fail(String name, String detail) {
        check(name, false, detail);
    }

    private VerifyWireShape() {
    }
}
