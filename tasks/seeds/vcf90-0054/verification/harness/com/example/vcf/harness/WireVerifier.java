package com.example.vcf.harness;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Asserts the exact wire shape of the requests recorded by {@link MockVcenter} against
 * {@code docs/contract.json}.
 *
 * <p>The allowed property names are read out of the contract rather than repeated here, so the
 * verifier and the mock stay pinned to the same document.
 *
 * <p>Failures accumulate instead of throwing, so one run reports everything that is wrong.
 */
public final class WireVerifier {

    private final String basePath;
    private final Set<String> applySpecProperties;
    private final Set<String> getSpecProperties;
    private final Map<String, Integer> successStatus = new LinkedHashMap<>();
    private final List<String> failures = new ArrayList<>();

    private String scenario = "";

    public WireVerifier(Path contractFile) throws IOException {
        Map<String, Object> contract = Json.parseObject(Files.readString(contractFile));
        this.basePath = String.valueOf(contract.get("basePath"));

        Set<String> apply = new LinkedHashSet<>();
        Set<String> getSpec = new LinkedHashSet<>();
        for (Object entry : Json.asArray(contract.get("operations"))) {
            Map<String, Object> op = Json.asObject(entry);
            String operationId = String.valueOf(op.get("operationId"));
            Number status = (Number) op.get("successStatus");
            successStatus.put(operationId, status == null ? 200 : status.intValue());

            Map<String, Object> body = Json.asObject(op.get("requestBody"));
            if (body != null) {
                Map<String, Object> props = Json.asObject(body.get("properties"));
                if (props != null) {
                    apply.addAll(props.keySet());
                }
            }
            List<Object> queryParams = Json.asArray(op.get("queryParameters"));
            if (queryParams != null) {
                for (Object q : queryParams) {
                    Map<String, Object> props = Json.asObject(Json.asObject(q).get("properties"));
                    if (props != null) {
                        getSpec.addAll(props.keySet());
                    }
                }
            }
        }
        this.applySpecProperties = apply;
        this.getSpecProperties = getSpec;
    }

    public void scenario(String name) {
        this.scenario = name;
    }

    public List<String> failures() {
        return List.copyOf(failures);
    }

    public boolean passed() {
        return failures.isEmpty();
    }

    private void fail(String message) {
        failures.add("[" + scenario + "] " + message);
    }

    private void check(boolean condition, String message) {
        if (!condition) {
            fail(message);
        }
    }

    private void checkEquals(Object expected, Object actual, String what) {
        if (!Objects.equals(expected, actual)) {
            fail(what + ": expected " + render(expected) + " but the request carried " + render(actual));
        }
    }

    private static String render(Object value) {
        if (value == null) {
            return "<absent>";
        }
        if (value instanceof String s) {
            return "\"" + s + "\"";
        }
        return String.valueOf(value);
    }

    /** Records a plain expected/actual comparison, used for the values the client returns. */
    public void scenarioCheck(Object expected, Object actual, String what) {
        checkEquals(expected, actual, what);
    }

    // ------------------------------------------------------------ operations

    /** POST {basePath}/session, HTTP Basic, no session header, no body, 201. */
    public void verifyLogin(MockVcenter.Recorded rec, String expectedAuthorization) {
        String where = "Cis.Session_create request " + rec.describe();
        checkEquals("Cis.Session_create", rec.operationId, where + " — matched contract operation");
        checkEquals("POST", rec.method.toUpperCase(Locale.ROOT), where + " — HTTP method");
        checkEquals(basePath + "/session", rec.decodedPath, where + " — request path");
        check(rec.rawQuery == null || rec.rawQuery.isEmpty(),
                where + " — the operation declares no query parameters, but the request carried '?"
                        + rec.rawQuery + "'");
        checkEquals(expectedAuthorization, rec.header("authorization"),
                where + " — Authorization header (basic_auth security scheme)");
        check(!rec.hasHeader("vmware-api-session-id"),
                where + " — login must not send vmware-api-session-id; there is no session yet");
        check(rec.body.isEmpty(),
                where + " — the operation declares no request body, but " + rec.body.length()
                        + " bytes were sent: " + rec.body);
        checkEquals(successStatus.get("Cis.Session_create"), rec.responseStatus,
                where + " — response status the appliance replied with");
        check(rec.rejection == null, where + " — server rejected the request: " + rec.rejection);
    }

    /**
     * POST {basePath}/esx/settings/clusters/{cluster}/software?action=apply&amp;vmw-task=true.
     *
     * @param expectedBody the complete set of JSON members the body must carry — no more, no less
     */
    public void verifyApply(MockVcenter.Recorded rec, String expectedCluster, String expectedSessionToken,
                            Map<String, Object> expectedBody) {
        String where = "Esx.Settings.Clusters.Software_apply$Task request " + rec.describe();
        checkEquals("Esx.Settings.Clusters.Software_apply$Task", rec.operationId,
                where + " — matched contract operation");
        checkEquals("POST", rec.method.toUpperCase(Locale.ROOT), where + " — HTTP method");
        checkEquals(basePath + "/esx/settings/clusters/" + expectedCluster + "/software", rec.decodedPath,
                where + " — request path");

        verifyQuery(where, rec, Map.of("action", "apply", "vmw-task", "true"),
                List.of("action", "vmw-task"));

        checkEquals(expectedSessionToken, rec.header("vmware-api-session-id"),
                where + " — vmware-api-session-id header (api_key_auth security scheme)");
        check(!rec.hasHeader("authorization"),
                where + " — once a session exists the request authenticates with the session header, "
                        + "not with Basic credentials");

        String contentType = rec.header("content-type");
        check(contentType != null && contentType.toLowerCase(Locale.ROOT).startsWith("application/json"),
                where + " — Content-Type must be application/json, was " + render(contentType));

        verifyApplyBody(where, rec, expectedBody);

        checkEquals(successStatus.get("Esx.Settings.Clusters.Software_apply$Task"), rec.responseStatus,
                where + " — response status the appliance replied with");
        check(rec.rejection == null, where + " — server rejected the request: " + rec.rejection);
    }

    private void verifyApplyBody(String where, MockVcenter.Recorded rec, Map<String, Object> expectedBody) {
        Map<String, Object> body;
        try {
            body = Json.parseObject(rec.body);
        } catch (RuntimeException e) {
            fail(where + " — request body is not a JSON object (" + e.getMessage() + "): " + rec.body);
            return;
        }

        Set<String> actualKeys = new LinkedHashSet<>(body.keySet());
        Set<String> expectedKeys = new LinkedHashSet<>(expectedBody.keySet());

        Set<String> unknown = new LinkedHashSet<>(actualKeys);
        unknown.removeAll(applySpecProperties);
        check(unknown.isEmpty(), where + " — body carries members that are not properties of "
                + "Esx.Settings.Clusters.Software.ApplySpec: " + unknown
                + " (the schema allows only " + applySpecProperties + ")");

        Set<String> shouldBeOmitted = new LinkedHashSet<>(actualKeys);
        shouldBeOmitted.removeAll(expectedKeys);
        shouldBeOmitted.retainAll(applySpecProperties);
        for (String key : shouldBeOmitted) {
            fail(where + " — optional property \"" + key + "\" was not supplied by the caller and must be "
                    + "omitted from the body entirely, but it was sent as "
                    + Json.describe(body.get(key)));
        }

        Set<String> missing = new LinkedHashSet<>(expectedKeys);
        missing.removeAll(actualKeys);
        for (String key : missing) {
            fail(where + " — the caller supplied \"" + key + "\" but the body does not carry it");
        }

        for (Map.Entry<String, Object> expected : expectedBody.entrySet()) {
            if (body.containsKey(expected.getKey())) {
                checkEquals(expected.getValue(), body.get(expected.getKey()),
                        where + " — body member \"" + expected.getKey() + "\"");
            }
        }

        for (Map.Entry<String, Object> member : body.entrySet()) {
            Object value = member.getValue();
            String key = member.getKey();
            if (value == null) {
                fail(where + " — body member \"" + key + "\" is JSON null; an unset optional property "
                        + "is omitted, never sent as null");
            } else if (value instanceof String s && s.isEmpty()) {
                fail(where + " — body member \"" + key + "\" is an empty string; an unset optional property "
                        + "is omitted, never sent empty");
            } else if (value instanceof List<?> list && list.isEmpty()) {
                fail(where + " — body member \"" + key + "\" is an empty array; an unset optional property "
                        + "is omitted, never sent empty");
            }
        }
    }

    /**
     * GET {basePath}/cis/tasks/{task}.
     *
     * @param expectedQuery the complete set of query parameters the request must carry; pass an
     *                      empty map to require that the request has no query string at all
     */
    public void verifyPoll(MockVcenter.Recorded rec, String expectedTaskId, String expectedSessionToken,
                           Map<String, String> expectedQuery) {
        String where = "Cis.Tasks_get request " + rec.describe();
        checkEquals("Cis.Tasks_get", rec.operationId, where + " — matched contract operation");
        checkEquals("GET", rec.method.toUpperCase(Locale.ROOT), where + " — HTTP method");
        checkEquals(basePath + "/cis/tasks/" + expectedTaskId, rec.decodedPath, where + " — request path");

        if (expectedQuery.isEmpty()) {
            check(rec.rawQuery == null || rec.rawQuery.isEmpty(),
                    where + " — no property of Cis.Tasks.GetSpec was supplied, so the request must carry "
                            + "no query string at all, but it carried '?" + rec.rawQuery + "'");
        }
        verifyQuery(where, rec, expectedQuery, new ArrayList<>(expectedQuery.keySet()));

        for (String name : rec.queryNames()) {
            check(getSpecProperties.contains(name),
                    where + " — query parameter \"" + name + "\" is not a property of Cis.Tasks.GetSpec "
                            + "(the schema allows only " + getSpecProperties + ")");
        }
        for (String[] pair : rec.queryParams) {
            check(!pair[1].isEmpty(),
                    where + " — query parameter \"" + pair[0] + "\" was sent with an empty value; an unset "
                            + "optional property is omitted, never sent empty");
        }

        checkEquals(expectedSessionToken, rec.header("vmware-api-session-id"),
                where + " — vmware-api-session-id header (api_key_auth security scheme)");
        check(!rec.hasHeader("authorization"),
                where + " — polling authenticates with the session header, not with Basic credentials");
        check(rec.body.isEmpty(),
                where + " — a GET for task status carries no request body, but " + rec.body.length()
                        + " bytes were sent: " + rec.body);
        checkEquals(successStatus.get("Cis.Tasks_get"), rec.responseStatus,
                where + " — response status the appliance replied with");
        check(rec.rejection == null, where + " — server rejected the request: " + rec.rejection);
    }

    private void verifyQuery(String where, MockVcenter.Recorded rec,
                             Map<String, String> expected, List<String> expectedNames) {
        Set<String> actualNames = new LinkedHashSet<>(rec.queryNames());
        check(actualNames.size() == rec.queryParams.size(),
                where + " — the query string repeats a parameter: " + rec.queryNames());

        Set<String> expectedSet = new LinkedHashSet<>(expectedNames);
        Set<String> extra = new LinkedHashSet<>(actualNames);
        extra.removeAll(expectedSet);
        for (String name : extra) {
            fail(where + " — unexpected query parameter \"" + name + "="
                    + String.join(",", rec.queryValues(name)) + "\"; this request must carry exactly "
                    + expectedSet);
        }
        Set<String> missing = new LinkedHashSet<>(expectedSet);
        missing.removeAll(actualNames);
        for (String name : missing) {
            fail(where + " — query parameter \"" + name + "\" is missing; this request must carry exactly "
                    + expectedSet);
        }
        for (Map.Entry<String, String> e : expected.entrySet()) {
            if (actualNames.contains(e.getKey())) {
                checkEquals(e.getValue(), rec.queryValues(e.getKey()).get(0),
                        where + " — query parameter \"" + e.getKey() + "\"");
            }
        }
    }

    // ------------------------------------------------------- whole-log rules

    /** Every request the client made must have been one of the operations the contract names. */
    public void verifyStayedInsideContract(MockVcenter mock) {
        checkEquals(0, mock.unmatchedCount(),
                "the client sent requests that no operation in docs/contract.json covers");
        for (MockVcenter.Recorded rec : mock.log()) {
            check(rec.operationId != null,
                    "request outside the contract: " + rec.describe());
            check(rec.responseStatus < 400,
                    "the appliance answered " + rec.responseStatus + " to " + rec.describe()
                            + (rec.rejection == null ? "" : " — " + rec.rejection));
        }
    }

    /** The whole exchange, in order: one login, one apply, then nothing but polls. */
    public void verifyCallSequence(MockVcenter mock, int expectedPolls) {
        List<MockVcenter.Recorded> log = mock.log();
        int expectedTotal = 2 + expectedPolls;
        if (log.size() != expectedTotal) {
            StringBuilder sb = new StringBuilder("expected exactly " + expectedTotal + " requests "
                    + "(1 login, 1 apply, " + expectedPolls + " task polls) but the client made "
                    + log.size() + ":");
            for (MockVcenter.Recorded rec : log) {
                sb.append("\n        ").append(rec.describe());
            }
            fail(sb.toString());
            return;
        }
        checkEquals("Cis.Session_create", log.get(0).operationId, "first request");
        checkEquals("Esx.Settings.Clusters.Software_apply$Task", log.get(1).operationId, "second request");
        for (int i = 2; i < log.size(); i++) {
            checkEquals("Cis.Tasks_get", log.get(i).operationId, "request #" + (i + 1));
        }
    }
}
