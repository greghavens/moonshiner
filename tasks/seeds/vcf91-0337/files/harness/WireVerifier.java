import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;


/**
 * Asserts the exact wire shape of the traffic the client produced.
 *
 * The verifier reads the mock's request log and docs/contract.json. It never opens a socket and it
 * never contacts a VMware endpoint; a run is a pure function of the recorded requests.
 */
public final class WireVerifier {

    /** Everything the run is expected to look like. */
    public static final class Expectation {
        public String bearerToken;
        public String apiVersion;
        public String cloudAccountId;
        public String newPassword;
        public String oldPassword;
        public String accountName;
        public String accountHostName;
        public String accountUsername;
        public String accountDcid;
        public List<Map<String, Object>> regions;
        public String rotationRequestId;
        public int minimumDrainPolls;
        public int expectedRotationPolls;
    }

    private final List<MockAutomationServer.Recorded> log;
    private final Expectation expected;
    private final List<String> failures = new ArrayList<>();
    private final List<String> checks = new ArrayList<>();
    private final List<Operation> operations = new ArrayList<>();

    private static final class Operation {
        final String id;
        final String method;
        final Pattern pattern;

        Operation(String id, String method, Pattern pattern) {
            this.id = id;
            this.method = method;
            this.pattern = pattern;
        }
    }

    public WireVerifier(List<MockAutomationServer.Recorded> log, Expectation expected, Path contractFile) throws Exception {
        this.log = log;
        this.expected = expected;
        Map<String, Object> contract = Json.object(Json.parse(Files.readString(contractFile)));
        for (Object entry : Json.arr(contract, "operations")) {
            Map<String, Object> operation = Json.object(entry);
            operations.add(new Operation(
                    Json.str(operation, "operationId"),
                    Json.str(operation, "method"),
                    Pattern.compile("^" + Json.str(operation, "path").replaceAll("\\{[^}]+\\}", "([^/]+)") + "$")));
        }
    }

    public List<String> failures() {
        return failures;
    }

    public List<String> checks() {
        return checks;
    }

    public void verify() {
        checkOnlyContractOperations();
        checkNoRejectedRequests();
        checkVersionDiscovery();
        checkCommonHeaders();
        checkApiVersionPinning();
        checkDrainBeforeRotation();
        checkCloudAccountRead();
        checkUpdateRequest();
        checkRotationPolling();
        checkSecretHandling();
    }

    /* ---------------------------------------------------------------- checks */

    private void checkOnlyContractOperations() {
        Set<String> unknown = new LinkedHashSet<>();
        for (MockAutomationServer.Recorded r : log) {
            if (operationOf(r) == null) {
                unknown.add(r.method + " " + r.path);
            }
        }
        record("only operations named by docs/contract.json are called",
                unknown.isEmpty(), "calls outside the contract: " + unknown);
    }

    private void checkNoRejectedRequests() {
        List<String> rejected = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            if (r.responseStatus >= 400) {
                rejected.add("#" + r.sequence + " " + r.line() + " -> " + r.responseStatus);
            }
        }
        record("the server never had to reject a request", rejected.isEmpty(),
                "rejected requests: " + rejected);
    }

    private void checkVersionDiscovery() {
        List<MockAutomationServer.Recorded> about = matching("getAbout");
        record("GET /iaas/api/about is called to discover the API version", !about.isEmpty(),
                "the about operation was never called");
        if (about.isEmpty()) {
            return;
        }
        record("version discovery is the first request the client makes",
                about.get(0).sequence == 1, "first request was " + log.get(0).line());
        List<String> queried = new ArrayList<>();
        for (MockAutomationServer.Recorded r : about) {
            if (!r.query.isEmpty()) {
                queried.add("#" + r.sequence + " " + r.rawQuery);
            }
        }
        record("GET /iaas/api/about is sent without query parameters",
                queried.isEmpty(), "query parameters were sent on " + queried);
    }

    private void checkCommonHeaders() {
        List<String> badAuth = new ArrayList<>();
        List<String> badAccept = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            if (!("Bearer " + expected.bearerToken).equals(r.header("authorization"))) {
                badAuth.add("#" + r.sequence + " " + r.header("authorization"));
            }
            String accept = r.header("accept");
            if (accept == null || !accept.toLowerCase(Locale.ROOT).contains("application/json")) {
                badAccept.add("#" + r.sequence + " " + accept);
            }
        }
        record("every request carries 'Authorization: Bearer <token>'", badAuth.isEmpty(),
                "offending requests: " + badAuth);
        record("every request asks for application/json", badAccept.isEmpty(),
                "offending requests: " + badAccept);
    }

    private void checkApiVersionPinning() {
        List<String> problems = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            String operation = operationOf(r);
            if (operation == null || "getAbout".equals(operation)) {
                continue;
            }
            if (r.paramCount("apiVersion") != 1) {
                problems.add("#" + r.sequence + " " + r.line() + " carries "
                        + r.paramCount("apiVersion") + " apiVersion parameters");
            } else if (!expected.apiVersion.equals(r.param("apiVersion"))) {
                problems.add("#" + r.sequence + " " + r.line() + " pinned '" + r.param("apiVersion")
                        + "' instead of the version reported by /iaas/api/about ('"
                        + expected.apiVersion + "')");
            }
        }
        record("every versioned call pins the apiVersion discovered from /iaas/api/about",
                problems.isEmpty(), String.join("; ", problems));

        List<String> bodies = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            if ("GET".equals(r.method) && r.body != null && !r.body.isEmpty()) {
                bodies.add("#" + r.sequence + " " + r.line());
            }
        }
        record("GET requests are sent without a body", bodies.isEmpty(), "offending requests: " + bodies);
    }

    private void checkDrainBeforeRotation() {
        MockAutomationServer.Recorded update = singleUpdate();
        List<MockAutomationServer.Recorded> drainPolls = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            if ("getRequestTrackers".equals(operationOf(r))
                    && (update == null || r.sequence < update.sequence)) {
                drainPolls.add(r);
            }
        }
        record("in-flight requests are enumerated with GET /iaas/api/request-tracker before rotating",
                !drainPolls.isEmpty(), "no request tracker listing precedes the update");
        record("the client waits for the in-flight requests to settle before rotating "
                        + "(at least " + expected.minimumDrainPolls + " observations)",
                drainPolls.size() >= expected.minimumDrainPolls,
                "only " + drainPolls.size() + " listing(s) preceded the update");
    }

    private void checkCloudAccountRead() {
        MockAutomationServer.Recorded update = singleUpdate();
        List<MockAutomationServer.Recorded> reads = new ArrayList<>();
        for (MockAutomationServer.Recorded r : matching("getVSphereCloudAccount")) {
            if (r.path.equals("/iaas/api/cloud-accounts-vsphere/" + expected.cloudAccountId)
                    && (update == null || r.sequence < update.sequence)) {
                reads.add(r);
            }
        }
        record("the cloud account is read before its replacement update is built",
                !reads.isEmpty(), "no read of the target cloud account preceded the update");
    }

    private void checkUpdateRequest() {
        List<MockAutomationServer.Recorded> updates = matching("updateVSphereCloudAccountAsync");
        record("the cloud account is updated exactly once", updates.size() == 1,
                "found " + updates.size() + " PATCH requests");
        if (updates.size() != 1) {
            return;
        }
        MockAutomationServer.Recorded r = updates.get(0);
        record("the update targets the cloud account under rotation",
                r.path.equals("/iaas/api/cloud-accounts-vsphere/" + expected.cloudAccountId),
                "targeted " + r.path);
        record("the update was accepted with 202", r.responseStatus == 202,
                "server answered " + r.responseStatus);

        String contentType = r.header("content-type");
        record("the update declares Content-Type: application/json",
                contentType != null && contentType.toLowerCase(Locale.ROOT).startsWith("application/json"),
                "sent '" + contentType + "'");

        Map<String, Object> body;
        try {
            body = Json.object(Json.parse(r.body));
        } catch (RuntimeException malformed) {
            record("the update body is a JSON object", false, malformed.toString());
            return;
        }

        Set<String> sent = new LinkedHashSet<>(body.keySet());
        Set<String> want = new LinkedHashSet<>(List.of(
                "name", "hostName", "username", "dcid", "regions", "password"));
        Set<String> missing = new LinkedHashSet<>(want);
        missing.removeAll(sent);
        Set<String> extra = new LinkedHashSet<>(sent);
        extra.removeAll(want);
        record("the update body carries exactly the fields the read returned plus the new password",
                missing.isEmpty() && extra.isEmpty(),
                "missing " + missing + ", unexpected " + extra);

        List<String> empties = new ArrayList<>();
        collectEmpty(body, "", empties);
        record("optional fields with no value are omitted rather than sent null or empty",
                empties.isEmpty(), "sent empty: " + empties);

        record("name is carried forward unchanged",
                expected.accountName.equals(Json.str(body, "name")), "sent " + Json.str(body, "name"));
        record("hostName is carried forward unchanged",
                expected.accountHostName.equals(Json.str(body, "hostName")),
                "sent " + Json.str(body, "hostName"));
        record("username is carried forward unchanged",
                expected.accountUsername.equals(Json.str(body, "username")),
                "sent " + Json.str(body, "username"));
        record("dcid is carried forward unchanged",
                expected.accountDcid.equals(Json.str(body, "dcid")), "sent " + Json.str(body, "dcid"));
        record("password carries the new secret",
                expected.newPassword.equals(Json.str(body, "password")),
                "the password field did not carry the new secret");

        checkRegions(Json.arr(body, "regions"));
    }

    private void checkRegions(List<Object> sent) {
        List<Map<String, Object>> want = expected.regions;
        if (sent.size() != want.size()) {
            record("regions repeats every enabled region of the account",
                    false, "sent " + sent.size() + " region(s), expected " + want.size());
            return;
        }
        List<String> problems = new ArrayList<>();
        for (int i = 0; i < want.size(); i++) {
            if (!(sent.get(i) instanceof Map)) {
                problems.add("entry " + i + " is not an object");
                continue;
            }
            Map<String, Object> region = Json.object(sent.get(i));
            Set<String> keys = new LinkedHashSet<>(region.keySet());
            if (!keys.equals(Set.of("name", "externalRegionId"))) {
                problems.add("entry " + i + " has fields " + keys
                        + " but RegionSpecification declares only {name, externalRegionId}");
            }
            for (String field : List.of("name", "externalRegionId")) {
                Object value = region.get(field);
                if (!want.get(i).get(field).equals(value)) {
                    problems.add("entry " + i + " " + field + "='" + value + "' but the account reports '"
                            + want.get(i).get(field) + "'");
                }
            }
        }
        record("regions repeats every enabled region as a write-shaped RegionSpecification",
                problems.isEmpty(), String.join("; ", problems));
    }

    private void checkRotationPolling() {
        MockAutomationServer.Recorded update = singleUpdate();
        List<MockAutomationServer.Recorded> polls = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            if ("getRequestTracker".equals(operationOf(r))
                    && r.path.endsWith("/" + expected.rotationRequestId)
                    && (update == null || r.sequence > update.sequence)) {
                polls.add(r);
            }
        }
        record("the request tracker returned by the update is polled by id",
                !polls.isEmpty(), "the tracker was never polled by id");
        record("polling stops as soon as the tracker reaches its terminal state",
                polls.size() == expected.expectedRotationPolls,
                "polled " + polls.size() + " time(s), terminal state was reached on poll "
                        + expected.expectedRotationPolls);
        if (polls.isEmpty()) {
            return;
        }
        MockAutomationServer.Recorded last = polls.get(polls.size() - 1);
        record("polling continues until the tracker leaves INPROGRESS",
                last.sequence == log.get(log.size() - 1).sequence,
                "the run continued past the last tracker poll with " + log.get(log.size() - 1).line());
    }

    private void checkSecretHandling() {
        List<String> leaks = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            String requestMetadata = r.line() + " " + String.join(" ", r.headers.values());
            if (contains(requestMetadata, expected.newPassword)) {
                leaks.add("#" + r.sequence + " puts the new secret outside the update body");
            }
            if (contains(r.body, expected.newPassword)
                    && !"updateVSphereCloudAccountAsync".equals(operationOf(r))) {
                leaks.add("#" + r.sequence + " puts the new secret in a non-update body");
            }
            String all = r.line() + " " + String.join(" ", r.headers.values()) + " "
                    + (r.body == null ? "" : r.body);
            if (contains(all, expected.oldPassword)) {
                leaks.add("#" + r.sequence + " transmits the retired secret");
            }
        }
        record("the secret only ever travels in the update body, and the retired secret is never sent",
                leaks.isEmpty(), String.join("; ", leaks));
    }

    /* ---------------------------------------------------------------- helpers */

    private MockAutomationServer.Recorded singleUpdate() {
        List<MockAutomationServer.Recorded> updates = matching("updateVSphereCloudAccountAsync");
        return updates.size() == 1 ? updates.get(0) : null;
    }

    private List<MockAutomationServer.Recorded> matching(String operationId) {
        List<MockAutomationServer.Recorded> result = new ArrayList<>();
        for (MockAutomationServer.Recorded r : log) {
            if (operationId.equals(operationOf(r))) {
                result.add(r);
            }
        }
        return result;
    }

    private String operationOf(MockAutomationServer.Recorded r) {
        for (Operation operation : operations) {
            Matcher m = operation.pattern.matcher(r.path);
            if (m.matches() && operation.method.equalsIgnoreCase(r.method)) {
                return operation.id;
            }
        }
        return null;
    }

    private static boolean contains(String haystack, String needle) {
        return haystack != null && needle != null && !needle.isEmpty() && haystack.contains(needle);
    }

    private static void collectEmpty(Object value, String path, List<String> out) {
        if (value == null) {
            out.add((path.isEmpty() ? "<root>" : path) + "=null");
        } else if (value instanceof String s) {
            if (s.isEmpty()) {
                out.add(path + "=\"\"");
            }
        } else if (value instanceof Map<?, ?> map) {
            if (map.isEmpty()) {
                out.add(path + "={}");
            }
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                collectEmpty(entry.getValue(), path.isEmpty() ? String.valueOf(entry.getKey())
                        : path + "." + entry.getKey(), out);
            }
        } else if (value instanceof List<?> list) {
            if (list.isEmpty()) {
                out.add(path + "=[]");
            }
            for (int i = 0; i < list.size(); i++) {
                collectEmpty(list.get(i), path + "[" + i + "]", out);
            }
        }
    }

    private void record(String description, boolean passed, String detail) {
        checks.add((passed ? "PASS  " : "FAIL  ") + description
                + (passed || detail == null || detail.isEmpty() ? "" : "\n        " + detail));
        if (!passed) {
            failures.add(description + (detail == null || detail.isEmpty() ? "" : " -- " + detail));
        }
    }
}
