package com.example.vcf.harness;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Deterministic, offline verification of the SDDC Manager client exercise.
 *
 * <p>PROTECTED HARNESS FILE — do not modify.
 *
 * <p>Reads {@code target/request-log.jsonl} and {@code target/result.json} produced by
 * {@link TestMain}, plus {@code docs/contract.json} and {@code docs/official_sources.json}. It
 * contacts nothing. Exits 0 when every check passes, 1 otherwise, printing every failure.
 */
public final class Verifier {

    private static final String SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json";
    private static final String SPEC_TAG = "9.0.0.0";

    /** SHA-256 of the 40-character commit id that the vcf-api-specs tag 9.0.0.0 points at. */
    private static final String COMMIT_SHA_DIGEST =
            "33081e6a37b93a99c88b486516bee45550749d8c6e99fafce81f0623215b3aa4";

    /** SHA-256 of the commit id behind tag 9.1.0.0 — recorded only so it can be rejected by name. */
    private static final String WRONG_RELEASE_COMMIT_SHA_DIGEST =
            "15c2eff25b44c0b0e05a0d69cd19ea9551ceb6883136f32e48c3ef72a6a80102";

    /**
     * SHA-256 of the SCREAMING_SNAKE_CASE values of {@code Task.status} in the 9.0.0.0 spec,
     * de-duplicated, sorted ascending, joined with a single comma and no spaces.
     */
    private static final String TASK_STATUS_DIGEST =
            "962954259da2e55b3708bca7aed710763145d98b5b2ee7389f29d705b21b338f";

    private static final List<String> CONTRACT_OPERATION_IDS = List.of(
            "createToken", "refreshAccessToken", "getBundles", "startBundleDownloadByID", "getTask");

    private static final String BEARER_1 = "Bearer " + MockSddcManager.FIRST_ACCESS_TOKEN;
    private static final String BEARER_2 = "Bearer " + MockSddcManager.SECOND_ACCESS_TOKEN;

    private final List<String> failures = new ArrayList<>();

    public static void main(String[] args) throws Exception {
        Path outDir = Path.of(args.length > 0 ? args[0] : "target");
        Path docsDir = Path.of(args.length > 1 ? args[1] : "docs");
        Verifier verifier = new Verifier();
        verifier.run(outDir, docsDir);
        if (verifier.failures.isEmpty()) {
            System.out.println("VERIFICATION PASSED — all checks green.");
            return;
        }
        System.out.println("VERIFICATION FAILED — " + verifier.failures.size() + " problem(s):");
        for (int i = 0; i < verifier.failures.size(); i++) {
            System.out.println("  " + (i + 1) + ". " + verifier.failures.get(i));
        }
        System.exit(1);
    }

    private void run(Path outDir, Path docsDir) throws Exception {
        checkExecution(outDir, "SUCCESSFUL");
        for (String terminalStatus : MockSddcManager.TERMINAL_TASK_STATUSES) {
            if (!"SUCCESSFUL".equals(terminalStatus)) {
                checkExecution(outDir.resolve("terminal-" + terminalStatus), terminalStatus);
            }
        }
        Map<String, Object> contract = readJsonObject(docsDir.resolve("contract.json"), "docs/contract.json");
        if (contract != null) {
            checkContract(contract);
        }
        Map<String, Object> sources =
                readJsonObject(docsDir.resolve("official_sources.json"), "docs/official_sources.json");
        if (sources != null) {
            checkOfficialSources(sources);
        }
    }

    private void checkExecution(Path outDir, String terminalStatus) throws Exception {
        List<Map<String, Object>> log = readRequestLog(outDir.resolve("request-log.jsonl"));
        checkResult(outDir.resolve("result.json"), terminalStatus);
        if (log != null) {
            checkOnlyContractOperations(log);
            // Positional wire-shape checks only make sense once the call sequence lines up.
            if (checkCallSequence(log)) {
                checkWireShape(log);
            }
            checkNoWorkLost(log);
        }
    }

    // ---------------------------------------------------------------- result

    private void checkResult(Path resultFile, String terminalStatus) throws Exception {
        Map<String, Object> outcome = readJsonObject(resultFile, resultFile.toString());
        if (outcome == null) {
            return;
        }
        if (!Boolean.TRUE.equals(outcome.get("ok"))) {
            fail("the client did not complete: " + outcome.get("error"));
            return;
        }
        Object resultValue = outcome.get("result");
        if (!(resultValue instanceof Map)) {
            fail("downloadPendingSddcManagerBundle() must return a Map; it returned "
                    + MiniJson.describe(resultValue));
            return;
        }
        Map<String, Object> result = MiniJson.asObject(resultValue);
        expectEquals(
                "the returned bundleId",
                result.get("bundleId"),
                MockSddcManager.PENDING_BUNDLE_ID);
        expectEquals("the returned taskId", result.get("taskId"), MockSddcManager.TASK_ID);
        expectEquals("the returned taskStatus", result.get("taskStatus"), terminalStatus);
        Object refreshes = result.get("accessTokenRefreshes");
        long refreshCount = refreshes instanceof Number ? ((Number) refreshes).longValue() : -1L;
        if (refreshCount != 1L) {
            fail("the returned accessTokenRefreshes must be 1 (the run expires the token exactly once) "
                    + "but was " + MiniJson.describe(refreshes));
        }
    }

    // ----------------------------------------------------------- request log

    private void checkOnlyContractOperations(List<Map<String, Object>> log) {
        for (Map<String, Object> entry : log) {
            Object operationId = entry.get("operationId");
            if (operationId == null || !CONTRACT_OPERATION_IDS.contains(operationId)) {
                fail("request #" + seq(entry) + " (" + entry.get("method") + " " + entry.get("path")
                        + ") does not match any operation the contract names; the mock answered "
                        + entry.get("status"));
            }
        }
    }

    private boolean checkCallSequence(List<Map<String, Object>> log) {
        List<String> expected = List.of(
                "createToken:201",
                "getBundles:200",
                "startBundleDownloadByID:202",
                "getTask:200",
                "getTask:401",
                "refreshAccessToken:200",
                "getTask:200",
                "getTask:200");
        List<String> actual = new ArrayList<>();
        for (Map<String, Object> entry : log) {
            actual.add(entry.get("operationId") + ":" + asLong(entry.get("status")));
        }
        if (expected.equals(actual)) {
            return true;
        }
        fail("the request sequence must be exactly " + expected + " but was " + actual);
        return false;
    }

    private void checkWireShape(List<Map<String, Object>> log) {
        for (Map<String, Object> entry : log) {
            checkNoEmptyQueryValues(entry);
            checkNoNullOrEmptyBodyFields(entry);
        }

        Map<String, Object> createToken = at(log, 0);
        if (createToken != null) {
            expectEquals("createToken method", createToken.get("method"), "POST");
            expectEquals("createToken path", createToken.get("path"), "/v1/tokens");
            expectNoQuery(createToken, "createToken");
            expectJsonContentType(createToken, "createToken");
            expectNoAuthorization(
                    createToken,
                    "createToken",
                    "it is the endpoint that mints the token");
            Map<String, Object> spec = bodyObject(createToken, "createToken");
            if (spec != null) {
                expectKeys(
                        "the TokenCreationSpec body of createToken",
                        spec.keySet(),
                        Set.of("username", "password"),
                        "apiKey and idToken are optional and are not being used, so they must be "
                                + "omitted from the JSON body entirely");
                expectEquals("createToken username", spec.get("username"), MockSddcManager.USERNAME);
                expectEquals("createToken password", spec.get("password"), MockSddcManager.PASSWORD);
            }
        }

        Map<String, Object> getBundles = at(log, 1);
        if (getBundles != null) {
            expectEquals("getBundles method", getBundles.get("method"), "GET");
            expectEquals("getBundles path", getBundles.get("path"), "/v1/bundles");
            Object rawQuery = getBundles.get("rawQuery");
            if (!"productType=SDDC_MANAGER".equals(rawQuery)) {
                fail("getBundles must send exactly the query string 'productType=SDDC_MANAGER' — the "
                        + "optional isCompliant and bundleType parameters are unused and must not appear "
                        + "at all, not even with an empty value — but the query string was "
                        + MiniJson.describe(rawQuery));
            }
            expectEquals("getBundles Authorization header", getBundles.get("authorization"), BEARER_1);
            expectEmptyBody(getBundles, "getBundles");
        }

        Map<String, Object> startDownload = at(log, 2);
        if (startDownload != null) {
            expectEquals("startBundleDownloadByID method", startDownload.get("method"), "PATCH");
            expectEquals(
                    "startBundleDownloadByID path",
                    startDownload.get("path"),
                    "/v1/bundles/" + MockSddcManager.PENDING_BUNDLE_ID);
            expectNoQuery(startDownload, "startBundleDownloadByID");
            expectJsonContentType(startDownload, "startBundleDownloadByID");
            expectEquals(
                    "startBundleDownloadByID Authorization header",
                    startDownload.get("authorization"),
                    BEARER_1);
            Map<String, Object> updateSpec = bodyObject(startDownload, "startBundleDownloadByID");
            if (updateSpec != null) {
                expectKeys(
                        "the BundleUpdateSpec body of startBundleDownloadByID",
                        updateSpec.keySet(),
                        Set.of("bundleDownloadSpec"),
                        "BundleUpdateSpec declares exactly one property");
                Object downloadSpec = updateSpec.get("bundleDownloadSpec");
                if (!(downloadSpec instanceof Map)) {
                    fail("bundleDownloadSpec must be a JSON object but was "
                            + MiniJson.describe(downloadSpec));
                } else {
                    Map<String, Object> spec = MiniJson.asObject(downloadSpec);
                    expectKeys(
                            "the BundleDownloadSpec body of startBundleDownloadByID",
                            spec.keySet(),
                            Set.of("downloadNow"),
                            "scheduledTimestamp and cancelNow are optional and unused here, so they must "
                                    + "be omitted rather than sent as null or an empty value");
                    if (!Boolean.TRUE.equals(spec.get("downloadNow"))) {
                        fail("downloadNow must be the JSON boolean true but was "
                                + MiniJson.describe(spec.get("downloadNow")));
                    }
                }
            }
        }

        String taskPath = "/v1/tasks/" + MockSddcManager.TASK_ID;
        for (int index : new int[] {3, 4}) {
            Map<String, Object> poll = at(log, index);
            if (poll == null) {
                continue;
            }
            String label = "the getTask poll at request #" + (index + 1);
            expectEquals(label + " method", poll.get("method"), "GET");
            expectEquals(label + " path", poll.get("path"), taskPath);
            expectNoQuery(poll, label);
            expectEquals(label + " Authorization header", poll.get("authorization"), BEARER_1);
            expectEmptyBody(poll, label);
        }

        Map<String, Object> refresh = at(log, 5);
        if (refresh != null) {
            expectEquals("refreshAccessToken method", refresh.get("method"), "PATCH");
            expectEquals(
                    "refreshAccessToken path", refresh.get("path"), "/v1/tokens/access-token/refresh");
            expectNoQuery(refresh, "refreshAccessToken");
            expectJsonContentType(refresh, "refreshAccessToken");
            expectNoAuthorization(
                    refresh,
                    "refreshAccessToken",
                    "the access token it is replacing has already expired");
            String raw = string(refresh.get("body")).trim();
            String wanted = "\"" + MockSddcManager.REFRESH_TOKEN_ID + "\"";
            if (!wanted.equals(raw)) {
                fail("the refreshAccessToken request body is a bare JSON string — the refresh token id "
                        + "wrapped in double quotes, not an object — so it must be exactly " + wanted
                        + " but it was " + MiniJson.write(raw));
            }
        }

        for (int index : new int[] {6, 7}) {
            Map<String, Object> poll = at(log, index);
            if (poll == null) {
                continue;
            }
            String label = "the getTask poll at request #" + (index + 1);
            expectEquals(label + " method", poll.get("method"), "GET");
            expectEquals(label + " path", poll.get("path"), taskPath);
            expectNoQuery(poll, label);
            if (BEARER_1.equals(poll.get("authorization"))) {
                fail(label + " still presents the expired access token; after refreshing, every "
                        + "subsequent call must carry the new one");
            } else {
                expectEquals(label + " Authorization header", poll.get("authorization"), BEARER_2);
            }
            expectEmptyBody(poll, label);
        }
    }

    private void checkNoWorkLost(List<Map<String, Object>> log) {
        long logins = count(log, "createToken");
        long downloads = count(log, "startBundleDownloadByID");
        long refreshes = count(log, "refreshAccessToken");
        if (logins != 1) {
            fail("createToken must be called exactly once — recovering from an expired token means "
                    + "refreshing it, not signing in again — but it was called " + logins + " time(s)");
        }
        if (downloads != 1) {
            fail("startBundleDownloadByID must be called exactly once — the download already running "
                    + "when the token expired must not be restarted — but it was called " + downloads
                    + " time(s)");
        }
        if (refreshes != 1) {
            fail("refreshAccessToken must be called exactly once but was called " + refreshes + " time(s)");
        }
        Set<String> polledTaskIds = new LinkedHashSet<>();
        for (Map<String, Object> entry : log) {
            if ("getTask".equals(entry.get("operationId"))) {
                String path = string(entry.get("path"));
                polledTaskIds.add(path.substring(path.lastIndexOf('/') + 1));
            }
        }
        if (!polledTaskIds.equals(Set.of(MockSddcManager.TASK_ID))) {
            fail("every getTask call must poll the one task id returned by startBundleDownloadByID ("
                    + MockSddcManager.TASK_ID + ") but the run polled " + polledTaskIds);
        }
    }

    // ------------------------------------------------------------- contract

    private void checkContract(Map<String, Object> contract) throws Exception {
        expectEquals("contract.json specVersion", contract.get("specVersion"), SPEC_TAG);

        Object statusValues = contract.get("taskStatusValues");
        if (!(statusValues instanceof List)) {
            fail("contract.json must carry a taskStatusValues array holding the Task.status values "
                    + "the 9.0.0.0 spec enumerates, but it holds " + MiniJson.describe(statusValues));
        } else {
            List<Object> statusList = MiniJson.asArray(statusValues);
            Set<String> values = new TreeSet<>();
            for (Object value : statusList) {
                values.add(string(value));
            }
            if (values.size() != statusList.size()) {
                fail("contract.json taskStatusValues must list each status exactly once");
            }
            if (values.contains("QUEUED") || values.contains("TIMED_OUT")) {
                fail("contract.json taskStatusValues contains QUEUED and/or TIMED_OUT, which the 9.1.0.0 "
                        + "revision of sddc-manager-openapi.json added; the contract must come from the "
                        + "9.0.0.0 revision");
            } else if (!sha256(String.join(",", values)).equals(TASK_STATUS_DIGEST)) {
                fail("contract.json taskStatusValues does not match Task.status in the 9.0.0.0 spec "
                        + "(list the SCREAMING_SNAKE_CASE values only, once each); got " + values);
            }
        }

        Object operationsValue = contract.get("operations");
        if (!(operationsValue instanceof List)) {
            fail("contract.json must carry an operations array but it holds "
                    + MiniJson.describe(operationsValue));
            return;
        }
        List<Object> operations = MiniJson.asArray(operationsValue);
        if (operations.size() != CONTRACT_OPERATION_IDS.size()) {
            fail("contract.json operations must contain exactly " + CONTRACT_OPERATION_IDS.size()
                    + " entries but contained " + operations.size());
        }
        Set<String> ids = new LinkedHashSet<>();
        for (Object item : operations) {
            if (!(item instanceof Map)) {
                fail("every entry of contract.json operations must be an object but one is "
                        + MiniJson.describe(item));
                continue;
            }
            Map<String, Object> operation = MiniJson.asObject(item);
            ids.add(String.valueOf(operation.get("operationId")));
        }
        expectKeys(
                "the operationIds in contract.json",
                ids,
                new LinkedHashSet<>(CONTRACT_OPERATION_IDS),
                "the contract must name exactly the operations this client uses");

        checkOperation(operations, "createToken", "POST", "/v1/tokens", 201,
                Set.of(), "TokenCreationSpec", Set.of("username", "password", "apiKey", "idToken"));
        checkOperation(operations, "refreshAccessToken", "PATCH", "/v1/tokens/access-token/refresh", 200,
                Set.of(), "string", Set.of());
        checkOperation(operations, "getBundles", "GET", "/v1/bundles", 200,
                Set.of("productType", "isCompliant", "bundleType"), null, Set.of());
        checkOperation(operations, "startBundleDownloadByID", "PATCH", "/v1/bundles/{id}", 202,
                Set.of(), "BundleUpdateSpec", Set.of("bundleDownloadSpec"));
        checkOperation(operations, "getTask", "GET", "/v1/tasks/{id}", 200,
                Set.of(), null, Set.of());
    }

    private void checkOperation(
            List<Object> operations,
            String operationId,
            String method,
            String path,
            long successStatus,
            Set<String> queryParameters,
            String requestBodySchema,
            Set<String> requestBodyFields) {
        Map<String, Object> op = null;
        for (Object item : operations) {
            if (item instanceof Map && operationId.equals(MiniJson.asObject(item).get("operationId"))) {
                op = MiniJson.asObject(item);
                break;
            }
        }
        if (op == null) {
            return; // already reported by the operationId set check
        }
        String label = "contract.json operation " + operationId;
        expectEquals(label + " method", op.get("method"), method);
        expectEquals(label + " path", op.get("path"), path);
        if (asLong(op.get("successStatus")) != successStatus) {
            fail(label + " successStatus must be the 2xx code the 9.0.0.0 spec declares ("
                    + successStatus + ") but was " + MiniJson.describe(op.get("successStatus")));
        }
        expectKeys(
                label + " queryParameters",
                namesOf(op.get("queryParameters"), label + " queryParameters"),
                queryParameters,
                "list every query parameter the spec declares for this operation, each as an object "
                        + "with name and required");
        expectKeys(
                label + " requestBodyFields",
                namesOf(op.get("requestBodyFields"), label + " requestBodyFields"),
                requestBodyFields,
                "list the top-level properties of the request body schema, each as an object with "
                        + "name and required");
        Object schema = op.get("requestBodySchema");
        if (requestBodySchema == null) {
            if (schema != null) {
                fail(label + " has no request body in the 9.0.0.0 spec, so requestBodySchema must be null "
                        + "but it was " + MiniJson.describe(schema));
            }
        } else if (!requestBodySchema.equals(schema)) {
            fail(label + " requestBodySchema must name the schema the spec references ("
                    + requestBodySchema + ") but was " + MiniJson.describe(schema));
        }
        markOptional(op, label, queryParameters, requestBodyFields);
    }

    /** Every parameter and body property involved here is optional in the 9.0.0.0 spec. */
    private void markOptional(
            Map<String, Object> op, String label, Set<String> queryParameters, Set<String> bodyFields) {
        for (String key : List.of("queryParameters", "requestBodyFields")) {
            Object value = op.get(key);
            if (!(value instanceof List)) {
                continue;
            }
            for (Object item : MiniJson.asArray(value)) {
                if (!(item instanceof Map)) {
                    continue;
                }
                Map<String, Object> entry = MiniJson.asObject(item);
                if (!Boolean.FALSE.equals(entry.get("required"))) {
                    fail(label + " " + key + " entry '" + entry.get("name") + "' must record "
                            + "\"required\": false — the 9.0.0.0 spec marks it optional, which is why the "
                            + "client omits it when it has no value");
                }
            }
        }
    }

    // ------------------------------------------------------ official sources

    private void checkOfficialSources(Map<String, Object> sources) throws Exception {
        expectEquals(
                "official_sources.json repository",
                sources.get("repository"),
                "https://github.com/vmware/vcf-api-specs");
        expectEquals("official_sources.json license", sources.get("license"), "Apache-2.0");
        expectEquals("official_sources.json tag", sources.get("tag"), SPEC_TAG);
        expectEquals("official_sources.json specPath", sources.get("specPath"), SPEC_PATH);
        expectEquals("official_sources.json specVersion", sources.get("specVersion"), SPEC_TAG);

        String commitSha = string(sources.get("commitSha")).trim();
        if (!commitSha.matches("[0-9a-f]{40}")) {
            fail("official_sources.json commitSha must be the full 40-character lowercase commit id that "
                    + "tag " + SPEC_TAG + " points at, but was " + MiniJson.describe(sources.get("commitSha")));
        } else {
            String digest = sha256(commitSha);
            if (WRONG_RELEASE_COMMIT_SHA_DIGEST.equals(digest)) {
                fail("official_sources.json commitSha is the commit behind tag 9.1.0.0; the contract must "
                        + "be derived from the 9.0.0.0 revision of " + SPEC_PATH);
            } else if (!COMMIT_SHA_DIGEST.equals(digest)) {
                fail("official_sources.json commitSha " + commitSha + " is not the commit that tag "
                        + SPEC_TAG + " of vmware/vcf-api-specs points at");
            }
        }

        Object idsValue = sources.get("operationIds");
        if (!(idsValue instanceof List)) {
            fail("official_sources.json must list the operationIds it sourced, but operationIds holds "
                    + MiniJson.describe(idsValue));
            return;
        }
        List<Object> idList = MiniJson.asArray(idsValue);
        Set<String> ids = new LinkedHashSet<>();
        for (Object item : idList) {
            ids.add(string(item));
        }
        expectKeys(
                "official_sources.json operationIds",
                ids,
                new LinkedHashSet<>(CONTRACT_OPERATION_IDS),
                "record every operationId taken from the specification");
    }

    // --------------------------------------------------------------- helpers

    private List<Map<String, Object>> readRequestLog(Path path) throws Exception {
        if (!Files.isRegularFile(path)) {
            fail("no request log at " + path + " — run the harness before verifying");
            return null;
        }
        List<Map<String, Object>> entries = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                entries.add(MiniJson.asObject(MiniJson.parse(line)));
            }
        }
        if (entries.isEmpty()) {
            fail("the request log at " + path + " is empty — the client made no HTTP calls");
            return null;
        }
        return entries;
    }

    private Map<String, Object> readJsonObject(Path path, String label) throws Exception {
        if (!Files.isRegularFile(path)) {
            fail(label + " is missing");
            return null;
        }
        try {
            return MiniJson.asObject(MiniJson.parse(Files.readString(path, StandardCharsets.UTF_8)));
        } catch (RuntimeException e) {
            fail(label + " is not a valid JSON object: " + e.getMessage());
            return null;
        }
    }

    private Map<String, Object> at(List<Map<String, Object>> log, int index) {
        return index < log.size() ? log.get(index) : null;
    }

    private long count(List<Map<String, Object>> log, String operationId) {
        return log.stream().filter(e -> operationId.equals(e.get("operationId"))).count();
    }

    private Set<String> namesOf(Object value, String label) {
        Set<String> names = new LinkedHashSet<>();
        if (value == null) {
            fail(label + " must be an array but was missing");
            return names;
        }
        if (!(value instanceof List)) {
            fail(label + " must be an array but was " + MiniJson.describe(value));
            return names;
        }
        List<Object> entries = MiniJson.asArray(value);
        for (Object item : entries) {
            if (item instanceof Map) {
                Map<String, Object> entry = MiniJson.asObject(item);
                names.add(String.valueOf(entry.get("name")));
            } else {
                fail(label + " entries must be objects with name and required, but one is "
                        + MiniJson.describe(item));
            }
        }
        if (names.size() != entries.size()) {
            fail(label + " must list each name exactly once");
        }
        return names;
    }

    private Map<String, Object> bodyObject(Map<String, Object> entry, String label) {
        String raw = string(entry.get("body"));
        try {
            Object parsed = MiniJson.parse(raw);
            if (!(parsed instanceof Map)) {
                fail("the " + label + " request body must be a JSON object but was "
                        + MiniJson.describe(parsed));
                return null;
            }
            return MiniJson.asObject(parsed);
        } catch (RuntimeException e) {
            fail("the " + label + " request body is not valid JSON: " + MiniJson.write(raw));
            return null;
        }
    }

    private void checkNoEmptyQueryValues(Map<String, Object> entry) {
        Object query = entry.get("query");
        if (!(query instanceof Map)) {
            return;
        }
        for (Map.Entry<String, Object> param : MiniJson.asObject(query).entrySet()) {
            for (Object value : MiniJson.asArray(param.getValue())) {
                if (string(value).isEmpty()) {
                    fail("request #" + seq(entry) + " (" + entry.get("operationId") + ") sends query "
                            + "parameter '" + param.getKey() + "' with an empty value; an optional "
                            + "parameter with no value must be left out of the URL entirely");
                }
            }
        }
    }

    private void checkNoNullOrEmptyBodyFields(Map<String, Object> entry) {
        String raw = string(entry.get("body"));
        if (raw.isBlank()) {
            return;
        }
        Object parsed;
        try {
            parsed = MiniJson.parse(raw);
        } catch (RuntimeException e) {
            return; // reported elsewhere
        }
        scanForBlanks(parsed, "", entry);
    }

    private void scanForBlanks(Object value, String pointer, Map<String, Object> entry) {
        if (value instanceof Map) {
            for (Map.Entry<String, Object> field : MiniJson.asObject(value).entrySet()) {
                String child = pointer + "/" + field.getKey();
                Object childValue = field.getValue();
                if (childValue == null) {
                    fail("request #" + seq(entry) + " (" + entry.get("operationId") + ") sends "
                            + child + " as JSON null; an optional field with no value must be omitted "
                            + "from the body, not sent empty");
                } else if (childValue instanceof String && ((String) childValue).isEmpty()) {
                    fail("request #" + seq(entry) + " (" + entry.get("operationId") + ") sends "
                            + child + " as an empty string; an optional field with no value must be "
                            + "omitted from the body, not sent empty");
                } else {
                    scanForBlanks(childValue, child, entry);
                }
            }
        } else if (value instanceof List) {
            List<Object> items = MiniJson.asArray(value);
            for (int i = 0; i < items.size(); i++) {
                scanForBlanks(items.get(i), pointer + "/" + i, entry);
            }
        }
    }

    private void expectNoQuery(Map<String, Object> entry, String label) {
        Object rawQuery = entry.get("rawQuery");
        if (rawQuery != null && !string(rawQuery).isEmpty()) {
            fail(label + " takes no query parameters in the 9.0.0.0 spec but the request sent '"
                    + rawQuery + "'");
        }
    }

    private void expectJsonContentType(Map<String, Object> entry, String label) {
        String contentType = string(entry.get("contentType")).toLowerCase(Locale.ROOT);
        if (!contentType.startsWith("application/json")) {
            fail(label + " must send Content-Type: application/json but sent "
                    + MiniJson.describe(entry.get("contentType")));
        }
    }

    private void expectNoAuthorization(Map<String, Object> entry, String label, String because) {
        if (entry.get("authorization") != null) {
            fail(label + " must not send an Authorization header — " + because + " — but it sent "
                    + MiniJson.describe(entry.get("authorization")));
        }
    }

    private void expectEmptyBody(Map<String, Object> entry, String label) {
        if (!string(entry.get("body")).isEmpty()) {
            fail(label + " is a GET with no request body but it sent "
                    + MiniJson.write(string(entry.get("body"))));
        }
    }

    private void expectEquals(String label, Object actual, Object expected) {
        if (!expected.equals(actual)) {
            fail(label + " must be " + MiniJson.write(expected) + " but was " + MiniJson.describe(actual));
        }
    }

    private void expectKeys(String label, Collection<String> actual, Set<String> expected, String why) {
        Set<String> got = new TreeSet<>(actual);
        Set<String> want = new TreeSet<>(expected);
        if (!got.equals(want)) {
            fail(label + " must be exactly " + want + " but was " + got + " — " + why);
        }
    }

    private static long asLong(Object value) {
        return value instanceof Number ? ((Number) value).longValue() : Long.MIN_VALUE;
    }

    private static String string(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static String seq(Map<String, Object> entry) {
        return String.valueOf(entry.get("seq"));
    }

    private static String sha256(String text) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        for (byte b : digest) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private void fail(String message) {
        failures.add(message);
    }

    private Verifier() {}
}
