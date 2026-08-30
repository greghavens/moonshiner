import com.example.vcfa.Json;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Deterministic assertions over the mock's request log.
 *
 * <p>PROTECTED FILE - the harness owns this. Do not edit it.
 *
 * <p>Everything here is judged from what actually went over the loopback socket, never from the
 * client's own report of what it did. No live VMware endpoint is contacted.
 */
public final class Verifier {

    private final List<String> failures = new ArrayList<>();
    private int checks;
    private String scenario = "(none)";

    public void scenario(String name) {
        this.scenario = name;
        System.out.println();
        System.out.println("== " + name);
    }

    // --------------------------------------------------------- primitives

    public void check(String name, boolean condition, String detail) {
        checks++;
        if (condition) {
            System.out.println("  PASS  " + name);
        } else {
            System.out.println("  FAIL  " + name + " -- " + detail);
            failures.add(scenario + " / " + name + ": " + detail);
        }
    }

    public void equal(String name, Object expected, Object actual) {
        check(name, deepEquals(expected, actual), "expected <" + expected + "> but got <" + actual + ">");
    }

    public boolean report() {
        System.out.println();
        System.out.println("--------------------------------------------------");
        if (failures.isEmpty()) {
            System.out.println("ALL CHECKS PASSED (" + checks + " checks)");
            return true;
        }
        System.out.println(failures.size() + " of " + checks + " checks FAILED:");
        for (String f : failures) {
            System.out.println("  - " + f);
        }
        return false;
    }

    // ------------------------------------------------------- log selectors

    public static List<Map<String, Object>> withOperation(List<Map<String, Object>> log, String operationId) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> e : log) {
            if (operationId.equals(e.get("operationId"))) {
                out.add(e);
            }
        }
        return out;
    }

    private static String header(Map<String, Object> entry, String name) {
        @SuppressWarnings("unchecked")
        Map<String, Object> headers = (Map<String, Object>) entry.get("headers");
        Object v = headers.get(name.toLowerCase(java.util.Locale.ROOT));
        return v == null ? null : String.valueOf(v);
    }

    // ------------------------------------------------------ wire assertions

    /**
     * Asserts the precheck request is exactly what the contract's getDeploymentActions operation
     * describes.
     */
    public void assertPrecheckWireShape(Map<String, Object> entry, String deploymentId, String token) {
        equal("precheck matched the contract operation", "getDeploymentActions", entry.get("operationId"));
        equal("precheck method is GET", "GET", entry.get("method"));
        equal(
                "precheck path is /deployment/api/deployments/" + deploymentId + "/actions",
                "/deployment/api/deployments/" + deploymentId + "/actions",
                entry.get("path"));
        check("precheck sends no query string", entry.get("query") == null, "query was " + entry.get("query"));
        equal("precheck sends Authorization: Bearer <token>", "Bearer " + token, header(entry, "Authorization"));
        check(
                "precheck sends Accept: application/json",
                header(entry, "Accept") != null && header(entry, "Accept").contains("application/json"),
                "Accept was " + header(entry, "Accept"));
        equal("precheck sends no body", "", entry.get("body"));
    }

    /**
     * Asserts the mutating request is exactly what the contract's submitDeploymentActionRequest
     * operation describes, and that its JSON body carries exactly {@code expectedBody} - no extra
     * keys, and no key present that {@code expectedBody} does not name.
     */
    public void assertMutatingWireShape(
            Map<String, Object> entry,
            String deploymentId,
            String token,
            Map<String, Object> expectedBody) {
        equal(
                "submit matched the contract operation",
                "submitDeploymentActionRequest",
                entry.get("operationId"));
        equal("submit method is POST", "POST", entry.get("method"));
        equal(
                "submit path is /deployment/api/deployments/" + deploymentId + "/requests",
                "/deployment/api/deployments/" + deploymentId + "/requests",
                entry.get("path"));
        check("submit sends no query string", entry.get("query") == null, "query was " + entry.get("query"));
        equal("submit sends Authorization: Bearer <token>", "Bearer " + token, header(entry, "Authorization"));
        check(
                "submit sends Accept: application/json",
                header(entry, "Accept") != null && header(entry, "Accept").contains("application/json"),
                "Accept was " + header(entry, "Accept"));
        String contentType = header(entry, "Content-Type");
        check(
                "submit sends Content-Type: application/json",
                contentType != null && contentType.toLowerCase(java.util.Locale.ROOT).startsWith("application/json"),
                "Content-Type was " + contentType);

        String raw = String.valueOf(entry.get("body"));
        Object parsed;
        try {
            parsed = Json.parse(raw);
        } catch (RuntimeException e) {
            check("submit body is valid JSON", false, "body was <" + raw + ">: " + e.getMessage());
            return;
        }
        if (!(parsed instanceof Map)) {
            check("submit body is a JSON object", false, "body was <" + raw + ">");
            return;
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> body = (Map<String, Object>) parsed;

        Set<String> expectedKeys = new TreeSet<>(expectedBody.keySet());
        Set<String> actualKeys = new TreeSet<>(body.keySet());
        check(
                "submit body carries exactly the keys " + expectedKeys,
                expectedKeys.equals(actualKeys),
                "body keys were " + actualKeys + " in <" + raw + ">");

        for (Map.Entry<String, Object> e : expectedBody.entrySet()) {
            equal("submit body." + e.getKey() + " value", e.getValue(), body.get(e.getKey()));
        }

        for (String key : new LinkedHashSet<>(List.of("actionId", "inputs", "reason"))) {
            if (!expectedBody.containsKey(key)) {
                assertFieldOmitted(entry, key);
            }
        }
    }

    /**
     * Asserts an unset optional field was omitted from the object outright rather than sent as an
     * empty string, an empty object, an empty array or a null.
     */
    public void assertFieldOmitted(Map<String, Object> entry, String field) {
        String raw = String.valueOf(entry.get("body"));
        Object parsed;
        try {
            parsed = Json.parse(raw);
        } catch (RuntimeException e) {
            check("unset '" + field + "' is omitted", false, "body is not valid JSON: <" + raw + ">");
            return;
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> body = (Map<String, Object>) parsed;
        boolean present = body.containsKey(field);
        String how = present ? describeEmpty(body.get(field)) : "";
        check(
                "unset '" + field + "' is omitted from the body, not sent " + emptyFormOf(field),
                !present,
                "body contained \"" + field + "\"" + how + " -- <" + raw + ">");
    }

    private static String emptyFormOf(String field) {
        return switch (field) {
            case "reason", "actionId" -> "as \"\" or null";
            case "inputs" -> "as {} or null";
            default -> "empty";
        };
    }

    private static String describeEmpty(Object v) {
        if (v == null) {
            return " set to null";
        }
        if (v instanceof String s) {
            return s.isEmpty() ? " set to the empty string" : " set to \"" + s + "\"";
        }
        if (v instanceof Map<?, ?> m) {
            return m.isEmpty() ? " set to an empty object" : " set to " + m;
        }
        if (v instanceof List<?> l) {
            return l.isEmpty() ? " set to an empty array" : " set to " + l;
        }
        return " set to " + v;
    }

    // ----------------------------------------------------- gating assertions

    /** Asserts the precheck refused the action and nothing mutating ever left the client. */
    public void assertNothingMutated(List<Map<String, Object>> log) {
        List<Map<String, Object>> submits = withOperation(log, "submitDeploymentActionRequest");
        check(
                "no submitDeploymentActionRequest was sent",
                submits.isEmpty(),
                "found " + submits.size() + " submit request(s): " + submits);
        List<String> posts = new ArrayList<>();
        for (Map<String, Object> e : log) {
            if ("POST".equals(e.get("method"))) {
                posts.add(String.valueOf(e.get("method")) + " " + e.get("path"));
            }
        }
        check(
                "no POST of any kind reached the appliance",
                posts.isEmpty(),
                "found " + posts);
    }

    /** Asserts the precheck ran before the mutating call, in that order. */
    public void assertPrecheckPrecededSubmit(List<Map<String, Object>> log) {
        int firstSubmit = -1;
        int firstPrecheck = -1;
        for (int i = 0; i < log.size(); i++) {
            String op = String.valueOf(log.get(i).get("operationId"));
            if (firstPrecheck < 0 && op.equals("getDeploymentActions")) {
                firstPrecheck = i;
            }
            if (firstSubmit < 0 && op.equals("submitDeploymentActionRequest")) {
                firstSubmit = i;
            }
        }
        check(
                "the precheck was sent before the mutating call",
                firstPrecheck >= 0 && firstSubmit > firstPrecheck,
                "precheck index " + firstPrecheck + ", submit index " + firstSubmit);
    }

    /** Asserts the client spoke only operations the contract names. */
    public void assertOnlyContractOperations(List<Map<String, Object>> log) {
        List<String> offContract = new ArrayList<>();
        for (Map<String, Object> e : log) {
            if (e.get("operationId") == null) {
                offContract.add(e.get("method") + " " + e.get("path") + " -> " + e.get("responseStatus"));
            }
        }
        check(
                "every request hit an operation the contract names",
                offContract.isEmpty(),
                "off-contract calls: " + offContract);
    }

    public void assertRequestCount(List<Map<String, Object>> log, int expected) {
        List<String> summary = new ArrayList<>();
        for (Map<String, Object> e : log) {
            summary.add(e.get("method") + " " + e.get("path"));
        }
        equal("the appliance saw exactly " + expected + " request(s) " + summary, expected, log.size());
    }

    // ------------------------------------------------------------- equality

    private static boolean deepEquals(Object a, Object b) {
        if (a instanceof Number na && b instanceof Number nb) {
            return na.doubleValue() == nb.doubleValue();
        }
        if (a instanceof Map<?, ?> ma && b instanceof Map<?, ?> mb) {
            if (ma.size() != mb.size()) {
                return false;
            }
            for (Map.Entry<?, ?> e : ma.entrySet()) {
                if (!mb.containsKey(e.getKey()) || !deepEquals(e.getValue(), mb.get(e.getKey()))) {
                    return false;
                }
            }
            return true;
        }
        if (a instanceof List<?> la && b instanceof List<?> lb) {
            if (la.size() != lb.size()) {
                return false;
            }
            for (int i = 0; i < la.size(); i++) {
                if (!deepEquals(la.get(i), lb.get(i))) {
                    return false;
                }
            }
            return true;
        }
        return a == null ? b == null : a.equals(b);
    }
}
