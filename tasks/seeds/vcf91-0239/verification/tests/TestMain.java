import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Acceptance verifier for the single-file SDDC LCM client.
 *
 * Drives SddcLcmClient against MockSddcLcm -- a loopback server pinned to
 * docs/contract.json -- and asserts the exact wire shape of every request the
 * client emitted, plus the idempotency behaviour the contract requires.
 *
 * No live VMware endpoint is contacted. Credentials here are dummies.
 *
 * Run: java TestMain.java
 *
 * PROTECTED FIXTURE -- do not modify this file or anything under docs/.
 */
public final class TestMain {

    static final String COMP_VCFA = "6b1d4b3e-9f24-4a7c-8a11-2f0e5c7d3a90";
    static final String FQDN_VCFA = "vcfa-fleet-01.lab.internal";
    static final String COMP_OPS = "a3c9e5f1-77b2-4d0a-9e63-118c4a2f6b58";
    static final String FQDN_OPS = "ops-fleet-01.lab.internal";

    static final String AUTH = "Bearer " + MockSddcLcm.BEARER_TOKEN;

    static int checks = 0;
    static final List<MockSddcLcm.Entry> allEntries = new ArrayList<>();

    // ------------------------------------------------------------------ asserts

    static void check(boolean cond, String label) {
        checks++;
        if (!cond) {
            throw new AssertionError("FAILED: " + label);
        }
    }

    static void eq(Object actual, Object expected, String label) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError("FAILED: " + label
                    + "\n  expected: " + expected + "\n  actual:   " + actual);
        }
    }

    /** Assertions every request in the run must satisfy. */
    static void assertCommon(MockSddcLcm.Entry e, String label) {
        eq(e.header("authorization"), AUTH, label + ": Authorization header");
        eq(e.header("accept"), "application/json", label + ": Accept header");
        check(!e.path.contains("//"), label + ": path must not contain an empty segment");
        check(e.status < 400, label + ": mock accepted the request (got " + e.status
                + (e.status >= 400 ? ", body rejected by the contract-pinned mock" : "") + ")");
    }

    static void assertNoBody(MockSddcLcm.Entry e, String label) {
        check(!e.hasHeader("content-type"),
                label + ": a request with no body must not send Content-Type");
        eq(e.body, "", label + ": body must be empty");
    }

    // ------------------------------------------------------------------ fixtures

    static MockSddcLcm newMock() throws Exception {
        MockSddcLcm mock = new MockSddcLcm();
        mock.addFleetComponent(COMP_VCFA, FQDN_VCFA, "VCF_AUTOMATION");
        mock.addFleetComponent(COMP_OPS, FQDN_OPS, "VCF_OPERATIONS");
        return mock;
    }

    static List<MockSddcLcm.Entry> since(MockSddcLcm mock, int start) {
        List<MockSddcLcm.Entry> slice = new ArrayList<>(mock.log.subList(start, mock.log.size()));
        allEntries.addAll(slice);
        return slice;
    }

    static String tasksQuery(String componentId) {
        return "resourceId=" + componentId + "&resourceType=COMPONENT";
    }

    // ---------------------------------------------------------------- scenarios

    /** Submit, then resubmit the same correlation key three ways. */
    static void scenarioSubmitAndAdopt() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);
            String key = "corr-alpha-7f31";

            // --- A1: first submission, no look-back window supplied.
            int mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult first =
                    client.requestSupportBundle(FQDN_VCFA, key, null);
            List<MockSddcLcm.Entry> es = since(mock, mark);

            eq(es.size(), 3, "A1: resolve, pre-flight, submit");

            MockSddcLcm.Entry comps = es.get(0);
            assertCommon(comps, "A1 getComponents");
            assertNoBody(comps, "A1 getComponents");
            eq(comps.op, "getComponents", "A1: first call is getComponents");
            eq(comps.method, "GET", "A1 getComponents method");
            eq(comps.path, "/v1/components", "A1 getComponents path");
            eq(comps.rawQuery, "scope=FLEET", "A1 getComponents query is exactly scope=FLEET");

            MockSddcLcm.Entry pre = es.get(1);
            assertCommon(pre, "A1 getTasks");
            assertNoBody(pre, "A1 getTasks");
            eq(pre.op, "getTasks", "A1: second call is getTasks");
            eq(pre.method, "GET", "A1 getTasks method");
            eq(pre.path, "/v1/tasks", "A1 getTasks path");
            eq(pre.rawQuery, tasksQuery(COMP_VCFA),
                    "A1 getTasks query is exactly resourceId then resourceType, no pageNumber");

            MockSddcLcm.Entry post = es.get(2);
            assertCommon(post, "A1 submit");
            eq(post.op, "generateComponentSupportBundle", "A1: third call is the submission");
            eq(post.method, "POST", "A1 submit method");
            eq(post.path, "/v1/components/" + COMP_VCFA + "/support-bundles", "A1 submit path");
            check(post.rawQuery == null || post.rawQuery.isEmpty(),
                    "A1 submit carries no query string");
            eq(post.header("x-correlation-id"), key, "A1 submit sends X-Correlation-Id");
            check(post.header("content-type") != null
                            && post.header("content-type").startsWith("application/json"),
                    "A1 submit sends Content-Type: application/json");

            Map<String, Object> body = MockSddcLcm.Json.object(post.body);
            eq(body.size(), 0,
                    "A1 submit body is an empty ComponentSupportBundleSpec (unset lookBackWindow "
                            + "is omitted, not sent as null or 0); got " + post.body);
            check(!post.body.contains("lookBackWindow"),
                    "A1 submit body must not mention lookBackWindow at all; got " + post.body);

            check(!first.adopted, "A1: a fresh key is submitted, not adopted");
            eq(first.componentId, COMP_VCFA, "A1 resolved component id");
            eq(first.taskStatus, "PENDING", "A1 task status from the 202 Task");
            check(first.taskId != null && !first.taskId.isEmpty(), "A1 task id is populated");
            eq(mock.supportBundlePosts, 1, "A1: exactly one submission reached the service");

            // --- A2: identical resubmission while the task is still PENDING.
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult second =
                    client.requestSupportBundle(FQDN_VCFA, key, null);
            es = since(mock, mark);

            eq(es.size(), 2, "A2: resolve and pre-flight only -- no second submission");
            eq(es.get(0).op, "getComponents", "A2 first call");
            eq(es.get(1).op, "getTasks", "A2 second call");
            eq(es.get(1).rawQuery, tasksQuery(COMP_VCFA), "A2 getTasks query");
            check(second.adopted, "A2: the in-flight task is adopted");
            eq(second.taskId, first.taskId, "A2 adopts the very same task");
            eq(second.taskStatus, "PENDING", "A2 reports the adopted task's status");
            eq(mock.supportBundlePosts, 1, "A2: still exactly one submission -- no duplicate");

            // --- A2b: SCHEDULED is also an effect that already stands.
            mock.setTaskStatus(first.taskId, "SCHEDULED");
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult scheduled =
                    client.requestSupportBundle(FQDN_VCFA, key, null);
            es = since(mock, mark);
            eq(es.size(), 2, "A2b: a SCHEDULED prior task is adopted, not resubmitted");
            eq(scheduled.taskId, first.taskId, "A2b adopts the scheduled task");
            eq(scheduled.taskStatus, "SCHEDULED", "A2b reports SCHEDULED");
            check(scheduled.adopted, "A2b adopted flag");
            eq(mock.supportBundlePosts, 1, "A2b: no duplicate submission");

            // --- A3: the prior run SUCCEEDED; its effect stands, so still no resubmission.
            mock.setTaskStatus(first.taskId, "SUCCEEDED");
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult third =
                    client.requestSupportBundle(FQDN_VCFA, key, null);
            es = since(mock, mark);
            eq(es.size(), 2, "A3: a SUCCEEDED prior task is adopted, not resubmitted");
            eq(third.taskId, first.taskId, "A3 adopts the completed task");
            eq(third.taskStatus, "SUCCEEDED", "A3 reports SUCCEEDED");
            check(third.adopted, "A3 adopted flag");
            eq(mock.supportBundlePosts, 1, "A3: no duplicate submission");

            // --- A4: the prior run FAILED; nothing stands, so this is a genuine retry.
            mock.setTaskStatus(first.taskId, "FAILED");
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult fourth =
                    client.requestSupportBundle(FQDN_VCFA, key, null);
            es = since(mock, mark);
            eq(es.size(), 3, "A4: a FAILED prior task is retried");
            eq(es.get(2).op, "generateComponentSupportBundle", "A4 submits again");
            eq(es.get(2).header("x-correlation-id"), key, "A4 reuses the same correlation key");
            check(!fourth.adopted, "A4 is a fresh submission");
            check(!fourth.taskId.equals(first.taskId), "A4 produced a new task");
            eq(mock.supportBundlePosts, 2, "A4: the retry did reach the service");

            // --- A5: two tasks now carry the key (FAILED, then PENDING). Adopt the live one.
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult fifth =
                    client.requestSupportBundle(FQDN_VCFA, key, null);
            es = since(mock, mark);
            eq(es.size(), 2, "A5: an adoptable match wins over an earlier FAILED match");
            eq(fifth.taskId, fourth.taskId, "A5 adopts the live task, not the failed one");
            check(fifth.adopted, "A5 adopted flag");
            eq(mock.supportBundlePosts, 2, "A5: no duplicate submission");
        } finally {
            mock.stop();
        }
    }

    /** Correlation matching must be exact-field equality, not text search. */
    static void scenarioDecoysAndLookBackWindow() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);
            String key = "corr-beta";

            // A longer key that has the real key as a prefix.
            mock.seedTask(COMP_OPS, "corr-beta-2", "RUNNING", null);
            // A different key whose human-readable description quotes the real key.
            mock.seedTask(COMP_OPS, "corr-gamma", "RUNNING",
                    "Retry requested with correlationId=\"corr-beta\" by operator svc-lcm");
            // A key held by another component entirely.
            mock.seedTask(COMP_VCFA, key, "RUNNING", null);
            // The one true match -- and it was cancelled, so nothing stands.
            String cancelled = mock.seedTask(COMP_OPS, key, "CANCELED", null);

            int mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult r =
                    client.requestSupportBundle(FQDN_OPS, key, 7);
            List<MockSddcLcm.Entry> es = since(mock, mark);

            eq(es.size(), 3, "B: decoys must not be adopted -- the submission goes out");
            check(!r.adopted, "B: a CANCELED match is not adoptable");
            check(!r.taskId.equals(cancelled), "B: did not adopt the cancelled task");
            eq(r.componentId, COMP_OPS, "B resolved component id");
            eq(mock.supportBundlePosts, 1, "B: one submission");

            MockSddcLcm.Entry post = es.get(2);
            assertCommon(post, "B submit");
            eq(post.path, "/v1/components/" + COMP_OPS + "/support-bundles", "B submit path");
            Map<String, Object> body = MockSddcLcm.Json.object(post.body);
            eq(body.size(), 1, "B submit body carries exactly one property; got " + post.body);
            check(body.containsKey("lookBackWindow"), "B submit body has lookBackWindow");
            check(body.get("lookBackWindow") instanceof Double
                            && ((Double) body.get("lookBackWindow")).intValue() == 7,
                    "B submit sends lookBackWindow as the integer 7; got " + post.body);
            check(!post.body.contains("\"7\""),
                    "B: lookBackWindow is an integer, not a string; got " + post.body);

            // Resubmitting now finds the PENDING task the mock just created.
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult again =
                    client.requestSupportBundle(FQDN_OPS, key, 7);
            since(mock, mark);
            check(again.adopted, "B: the resubmission adopts");
            eq(again.taskId, r.taskId, "B: adopts the task just created");
            eq(mock.supportBundlePosts, 1, "B: still one submission -- no duplicate");
        } finally {
            mock.stop();
        }
    }

    /** Paging: zero-based, pageNumber omitted on the first page, early stop on a hit. */
    static void scenarioPaging() throws Exception {
        MockSddcLcm mock = newMock();
        mock.pageSize = 2;
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);

            mock.seedTask(COMP_VCFA, "corr-p0", "RUNNING", null);      // page 0
            mock.seedTask(COMP_VCFA, "corr-p1", "FAILED", null);       // page 0
            String target = mock.seedTask(COMP_VCFA, "corr-target", "RUNNING", null); // page 1
            mock.seedTask(COMP_VCFA, "corr-p3", "RUNNING", null);      // page 1
            mock.seedTask(COMP_VCFA, "corr-p4", "RUNNING", null);      // page 2

            // --- C1: the match sits on page 1; page 2 must never be requested.
            int mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult hit =
                    client.requestSupportBundle(FQDN_VCFA, "corr-target", null);
            List<MockSddcLcm.Entry> es = since(mock, mark);

            eq(es.size(), 3, "C1: getComponents, page 0, page 1 -- then stop");
            eq(es.get(1).rawQuery, tasksQuery(COMP_VCFA), "C1 page 0 omits pageNumber");
            eq(es.get(2).rawQuery, tasksQuery(COMP_VCFA) + "&pageNumber=1",
                    "C1 page 1 appends pageNumber=1 last");
            check(hit.adopted, "C1 adopted the paged match");
            eq(hit.taskId, target, "C1 adopted the right task");
            eq(mock.supportBundlePosts, 0, "C1: nothing submitted");

            // --- C2: no match anywhere; every page is walked, then the submission goes out.
            mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult miss =
                    client.requestSupportBundle(FQDN_VCFA, "corr-absent", null);
            es = since(mock, mark);

            eq(es.size(), 5, "C2: getComponents, pages 0/1/2, submit");
            eq(es.get(1).rawQuery, tasksQuery(COMP_VCFA), "C2 page 0");
            eq(es.get(2).rawQuery, tasksQuery(COMP_VCFA) + "&pageNumber=1", "C2 page 1");
            eq(es.get(3).rawQuery, tasksQuery(COMP_VCFA) + "&pageNumber=2", "C2 page 2");
            eq(es.get(4).op, "generateComponentSupportBundle", "C2 submits after exhausting pages");
            check(!miss.adopted, "C2 is a fresh submission");
            eq(mock.supportBundlePosts, 1, "C2: exactly one submission");
        } finally {
            mock.stop();
        }
    }

    /** A failed pre-flight must abort -- never fall through to the mutation. */
    static void scenarioPreflightFailureIsFatal() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);
            mock.tasksFaults.add(new MockSddcLcm.Fault(503, "LCM_TASK_SERVICE_UNAVAILABLE",
                    "Task service is starting up."));

            int mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA, "corr-preflight", null);
                check(false, "D: a failed duplicate check must not be swallowed");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 503, "D httpStatus");
                eq(e.errorCode, "LCM_TASK_SERVICE_UNAVAILABLE", "D errorCode");
                check(e.referenceId != null && e.referenceId.startsWith("ref-"),
                        "D referenceId decoded from the ErrorResponse envelope");
                check(e.getMessage() != null && e.getMessage().contains("starting up"),
                        "D message comes from ErrorResponse.message.defaultMessage; got "
                                + e.getMessage());
            }
            List<MockSddcLcm.Entry> es = since(mock, mark);
            eq(es.size(), 2, "D: getComponents and the failed getTasks -- nothing more");
            eq(es.get(1).op, "getTasks", "D: the failure was on the pre-flight");
            eq(mock.supportBundlePosts, 0,
                    "D: the mutation must NOT be issued after a failed duplicate check");

            // A nominal 200 that cannot prove the scan is complete is also a failed
            // duplicate check. Treating absent metadata as zero pages would submit blindly.
            mock.omitTaskPageMetadataOnce = true;
            mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA, "corr-preflight-malformed", null);
                check(false, "D2: unusable task paging metadata must not be treated as a miss");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 0, "D2: malformed success payload is a client-side failure");
                eq(e.errorCode, null, "D2: malformed success payload has no error envelope");
                check(e.getMessage() != null && e.getMessage().contains("pageMetadata"),
                        "D2: the failure identifies unusable pageMetadata; got " + e.getMessage());
            }
            es = since(mock, mark);
            eq(es.size(), 2, "D2: getComponents and malformed getTasks -- nothing more");
            eq(es.get(1).status, 200, "D2: getTasks was HTTP-successful but unusable");
            eq(mock.supportBundlePosts, 0,
                    "D2: malformed duplicate-check data must NOT permit the mutation");
        } finally {
            mock.stop();
        }
    }

    /** Component resolution failures, both HTTP and client-side. */
    static void scenarioResolutionFailures() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);

            // --- E1: the FQDN is not a fleet component. Client-side, no HTTP status.
            int mark = mock.log.size();
            try {
                client.requestSupportBundle("ghost.lab.internal", "corr-e1", null);
                check(false, "E1: an unknown FQDN must fail");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 0, "E1: a client-side failure reports httpStatus 0");
                eq(e.errorCode, null, "E1: no ErrorResponse envelope, so no errorCode");
                check(e.getMessage() != null && e.getMessage().contains("ghost.lab.internal"),
                        "E1: the message names the FQDN that did not resolve; got "
                                + e.getMessage());
            }
            List<MockSddcLcm.Entry> es = since(mock, mark);
            eq(es.size(), 1, "E1: stops after getComponents");
            eq(mock.supportBundlePosts, 0, "E1: nothing submitted");

            // --- E1b: matching is byte-for-byte, including ASCII case.
            mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA.toUpperCase(), "corr-e1b", null);
                check(false, "E1b: component FQDN matching must be byte-for-byte");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 0, "E1b: case-mismatched FQDN is a caller error");
                check(e.getMessage() != null && e.getMessage().contains(FQDN_VCFA.toUpperCase()),
                        "E1b: the message names the unmatched FQDN; got " + e.getMessage());
            }
            es = since(mock, mark);
            eq(es.size(), 1, "E1b: exact-match miss stops after getComponents");
            eq(mock.supportBundlePosts, 0, "E1b: nothing submitted");

            // --- E2: getComponents itself fails.
            mock.componentsFaults.add(new MockSddcLcm.Fault(500, "LCM_INTERNAL_ERROR",
                    "Component inventory is unavailable."));
            mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA, "corr-e2", null);
                check(false, "E2: a 500 on getComponents must fail");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 500, "E2 httpStatus");
                eq(e.errorCode, "LCM_INTERNAL_ERROR", "E2 errorCode");
            }
            es = since(mock, mark);
            eq(es.size(), 1, "E2: stops at the failed getComponents");
            eq(mock.supportBundlePosts, 0, "E2: nothing submitted");
        } finally {
            mock.stop();
        }
    }

    /** Submission errors and the 202-only success rule. */
    static void scenarioSubmissionStatuses() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);

            // --- F: a 404 on the submission surfaces the envelope.
            mock.postFaults.add(new MockSddcLcm.Fault(404, "LCM_COMPONENT_NOT_FOUND",
                    "Component was removed while the request was in flight."));
            int mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA, "corr-f", null);
                check(false, "F: a 404 submission must fail");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 404, "F httpStatus");
                eq(e.errorCode, "LCM_COMPONENT_NOT_FOUND", "F errorCode");
                check(e.getMessage() != null && e.getMessage().contains("in flight"),
                        "F message from the envelope; got " + e.getMessage());
            }
            List<MockSddcLcm.Entry> es = since(mock, mark);
            eq(es.size(), 3, "F: resolve, pre-flight, failed submit");
            eq(mock.supportBundlePosts, 0, "F: the faulted submission created nothing");

            // --- G: 202 is the only documented success; 200 is a contract violation.
            mock.postSuccessStatus = 200;
            mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA, "corr-g", null);
                check(false, "G: a 200 on generateComponentSupportBundle must not be accepted");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 200, "G: the unexpected status is surfaced as-is");
                eq(e.errorCode, null, "G: a 200 body is not an ErrorResponse, so no errorCode");
            }
            es = since(mock, mark);
            eq(es.size(), 3, "G: resolve, pre-flight, submit");
            eq(es.get(2).status, 200, "G: the mock did answer 200");
        } finally {
            mock.stop();
        }
    }

    /** A candidate task carrying a status outside the 9.1 enum is a protocol violation. */
    static void scenarioUnrecognizedStatus() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base, MockSddcLcm.BEARER_TOKEN);
            // IN_PROGRESS belongs to the SDDC Manager task enum, not SDDC LCM 9.1.
            mock.seedTask(COMP_VCFA, "corr-h", "IN_PROGRESS", null);

            int mark = mock.log.size();
            try {
                client.requestSupportBundle(FQDN_VCFA, "corr-h", null);
                check(false, "H: an unrecognized TaskStatus must not be guessed at");
            } catch (SddcLcmClient.SddcLcmException e) {
                eq(e.httpStatus, 0, "H: a protocol violation is a client-side failure");
                check(e.getMessage() != null && e.getMessage().contains("IN_PROGRESS"),
                        "H: the message names the unrecognized status; got " + e.getMessage());
            }
            List<MockSddcLcm.Entry> es = since(mock, mark);
            eq(es.size(), 2, "H: stops at the pre-flight");
            eq(mock.supportBundlePosts, 0,
                    "H: an undecidable pre-flight must not fall through to the mutation");
        } finally {
            mock.stop();
        }
    }

    /** A base URL with a trailing slash must not produce an empty path segment. */
    static void scenarioTrailingSlashBaseUrl() throws Exception {
        MockSddcLcm mock = newMock();
        String base = mock.start();
        try {
            SddcLcmClient client = new SddcLcmClient(base + "/", MockSddcLcm.BEARER_TOKEN);
            int mark = mock.log.size();
            SddcLcmClient.SupportBundleRequestResult r =
                    client.requestSupportBundle(FQDN_OPS, "corr-slash", 30);
            List<MockSddcLcm.Entry> es = since(mock, mark);

            eq(es.size(), 3, "I: resolve, pre-flight, submit");
            eq(es.get(0).path, "/v1/components", "I getComponents path");
            eq(es.get(1).path, "/v1/tasks", "I getTasks path");
            eq(es.get(2).path, "/v1/components/" + COMP_OPS + "/support-bundles", "I submit path");
            check(!r.adopted, "I submitted");
            eq(mock.supportBundlePosts, 1, "I: one submission");
        } finally {
            mock.stop();
        }
    }

    // --------------------------------------------------------------------- main

    public static void main(String[] args) throws Exception {
        scenarioSubmitAndAdopt();
        scenarioDecoysAndLookBackWindow();
        scenarioPaging();
        scenarioPreflightFailureIsFatal();
        scenarioResolutionFailures();
        scenarioSubmissionStatuses();
        scenarioUnrecognizedStatus();
        scenarioTrailingSlashBaseUrl();

        // Whole-run sweeps over every request the client made.
        check(!allEntries.isEmpty(), "sweep: requests were recorded");
        for (MockSddcLcm.Entry e : allEntries) {
            String where = e.method + " " + e.target();
            check(!e.op.equals("<not-in-contract>"),
                    "sweep: only contract operations may be called; saw " + where);
            eq(e.header("authorization"), AUTH, "sweep: bearer auth on " + where);
            check(e.rawQuery == null || !e.rawQuery.contains("correlationId"),
                    "sweep: getTasks has no correlationId query parameter in the 9.1 spec; saw "
                            + where);
            check(e.rawQuery == null || !e.rawQuery.contains("pageSize"),
                    "sweep: the contract pins pageSize off the wire; saw " + where);
            check(e.rawQuery == null || !e.rawQuery.contains("includeSystemTasks"),
                    "sweep: includeSystemTasks defaults to false and is not sent; saw " + where);
            check(!e.path.contains("//"), "sweep: no empty path segment; saw " + where);
            if ("GET".equals(e.method)) {
                check(!e.hasHeader("content-type"),
                        "sweep: bodyless GET has no Content-Type; saw " + where);
                eq(e.body, "", "sweep: GET has no request body; saw " + where);
            } else if ("generateComponentSupportBundle".equals(e.op)) {
                check(e.header("content-type") != null
                                && e.header("content-type").startsWith("application/json"),
                        "sweep: submission sends JSON Content-Type; saw " + where);
                check(e.header("x-correlation-id") != null
                                && !e.header("x-correlation-id").isEmpty(),
                        "sweep: submission carries X-Correlation-Id; saw " + where);
                check(e.rawQuery == null || e.rawQuery.isEmpty(),
                        "sweep: submission carries no query string; saw " + where);
                Map<String, Object> body = MockSddcLcm.Json.object(e.body);
                check(body.keySet().stream().allMatch("lookBackWindow"::equals),
                        "sweep: submission body has no extra members; saw " + e.body);
                if (body.containsKey("lookBackWindow")) {
                    check(body.get("lookBackWindow") instanceof Double
                                    && Double.isFinite((Double) body.get("lookBackWindow"))
                                    && (Double) body.get("lookBackWindow")
                                            == Math.rint((Double) body.get("lookBackWindow")),
                            "sweep: lookBackWindow is an integer JSON number; saw " + e.body);
                }
            }
        }

        System.out.println("OK - " + checks + " checks passed across "
                + allEntries.size() + " verified requests");
    }
}
