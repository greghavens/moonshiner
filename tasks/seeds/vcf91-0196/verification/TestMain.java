import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.Objects;

public final class TestMain {
    private static final String TOKEN = "fixture-jwt-never-real";

    private static final String EVENT_BODY_1 = "{\"indices\":[\"vcf-events\"],\"query\":{\"bool\":{\"filter\":[{\"match_phrase\":{\"workload_domain_id\":\"wd-edge\"}},{\"match_phrase\":{\"correlation_id\":\"corr-9f\"}},{\"match_phrase\":{\"event_type\":\"LCM_UPGRADE_FAILED\"}},{\"range\":{\"@timestamp\":{\"gte\":\"2026-06-01T10:00:00Z\",\"lte\":\"2026-06-01T10:15:00Z\"}}}]}}},\"size\":100,\"sort\":[{\"@timestamp\":{\"order\":\"asc\"}}],\"trackTotalHits\":true}";
    private static final String LOG_BODY_1 = "{\"indices\":[\"vcf-logs\"],\"query\":{\"bool\":{\"filter\":[{\"match_phrase\":{\"correlation_id\":\"corr-9f\"}},{\"match_phrase\":{\"workflow_id\":\"wf-194\"}},{\"match_phrase\":{\"component\":\"NSX_MANAGER\"}},{\"range\":{\"@timestamp\":{\"gte\":\"2026-06-01T10:00:00Z\",\"lte\":\"2026-06-01T10:15:00Z\"}}}]}}},\"size\":200,\"sort\":[{\"@timestamp\":{\"order\":\"asc\"}}],\"trackTotalHits\":true}";

    private static final String EVENT_RESPONSE_1 = """
            {"events":{"total":1,"hits":[{"msgContent":{"logTimestamp":1780308420000,"originalText":"Upgrade workflow wf-194 failed for NSX_MANAGER","fields":[{"internalName":"event_type","value":"LCM_UPGRADE_FAILED","valueType":"EVENT_TYPE"},{"internalName":"workload_domain_id","value":"wd-edge","valueType":"STRING"},{"internalName":"correlation_id","value":"corr-9f","valueType":"STRING"},{"internalName":"workflow_id","value":"wf-194","valueType":"STRING"},{"internalName":"component","value":"NSX_MANAGER","valueType":"STRING"}]}}]},"timeTakenMillis":4,"timedOut":false}
            """.strip();
    private static final String LOG_RESPONSE_1 = """
            {"events":{"total":2,"hits":[{"msgContent":{"logTimestamp":1780308421000,"originalText":"Upgrade coordinator observed a downstream failure","fields":[{"internalName":"workflow_id","value":"wf-194","valueType":"STRING"},{"internalName":"component","value":"NSX_MANAGER","valueType":"STRING"},{"internalName":"root_cause","value":false,"valueType":"BOOLEAN"},{"internalName":"error_code","value":"DOWNSTREAM_ABORT","valueType":"STRING"}]}},{"msgContent":{"logTimestamp":1780308422000,"originalText":"TLS handshake rejected: manager certificate expired","fields":[{"internalName":"workflow_id","value":"wf-194","valueType":"STRING"},{"internalName":"component","value":"NSX_MANAGER","valueType":"STRING"},{"internalName":"root_cause","value":true,"valueType":"BOOLEAN"},{"internalName":"error_code","value":"CERTIFICATE_EXPIRED","valueType":"STRING"}]}}]},"timeTakenMillis":6,"timedOut":false}
            """.strip();

    private static final String EVENT_BODY_2 = "{\"indices\":[\"vcf-events\"],\"query\":{\"bool\":{\"filter\":[{\"match_phrase\":{\"workload_domain_id\":\"wd-\\\"blue\"}},{\"match_phrase\":{\"correlation_id\":\"corr\\\\88\"}},{\"match_phrase\":{\"event_type\":\"LCM_UPGRADE_FAILED\"}},{\"range\":{\"@timestamp\":{\"gte\":\"2026-06-02T01:00:00Z\",\"lte\":\"2026-06-02T01:30:00Z\"}}}]}}},\"size\":100,\"sort\":[{\"@timestamp\":{\"order\":\"asc\"}}],\"trackTotalHits\":true}";
    private static final String LOG_BODY_2 = "{\"indices\":[\"vcf-logs\"],\"query\":{\"bool\":{\"filter\":[{\"match_phrase\":{\"correlation_id\":\"corr\\\\88\"}},{\"match_phrase\":{\"workflow_id\":\"wf-88\"}},{\"match_phrase\":{\"component\":\"VCENTER\"}},{\"range\":{\"@timestamp\":{\"gte\":\"2026-06-02T01:00:00Z\",\"lte\":\"2026-06-02T01:30:00Z\"}}}]}}},\"size\":200,\"sort\":[{\"@timestamp\":{\"order\":\"asc\"}}],\"trackTotalHits\":true}";
    private static final String EVENT_RESPONSE_2 = """
            {"events":{"total":1,"hits":[{"msgContent":{"logTimestamp":1780362600000,"originalText":"Upgrade workflow wf-88 failed for VCENTER","fields":[{"internalName":"event_type","value":"LCM_UPGRADE_FAILED","valueType":"EVENT_TYPE"},{"internalName":"workload_domain_id","value":"wd-\\\"blue","valueType":"STRING"},{"internalName":"correlation_id","value":"corr\\\\88","valueType":"STRING"},{"internalName":"workflow_id","value":"wf-88","valueType":"STRING"},{"internalName":"component","value":"VCENTER","valueType":"STRING"}]}}]},"timeTakenMillis":2,"timedOut":false}
            """.strip();
    private static final String LOG_RESPONSE_2 = """
            {"events":{"total":1,"hits":[{"msgContent":{"logTimestamp":1780362601000,"originalText":"Datastore café is below the upgrade free-space threshold","fields":[{"internalName":"workflow_id","value":"wf-88","valueType":"STRING"},{"internalName":"component","value":"VCENTER","valueType":"STRING"},{"internalName":"root_cause","value":true,"valueType":"BOOLEAN"},{"internalName":"error_code","value":"DATASTORE_CAPACITY_EXHAUSTED","valueType":"STRING"}]}}]},"timeTakenMillis":3,"timedOut":false}
            """.strip();

    public static void main(String[] args) throws Exception {
        testCorrelatedDiagnosisAndExactWire();
        testAlternateEvidenceAndJsonEscaping();
        testErrorsTimeoutAndValidation();
        testMockDoesNotServeDeprecatedAlias();
        System.out.println("PASS: VCF Operations log-management contract");
    }

    private static void testCorrelatedDiagnosisAndExactWire() throws Exception {
        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200, EVENT_RESPONSE_1),
                new MockVcfOperations.Reply(200, LOG_RESPONSE_1)))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            VcfOperationsLogClient.Diagnosis diagnosis = client.diagnoseUpgradeFailure(
                    "wd-edge", "corr-9f", "2026-06-01T10:00:00Z", "2026-06-01T10:15:00Z");

            eq("corr-9f", diagnosis.correlationId(), "correlation id");
            eq("wd-edge", diagnosis.workloadDomainId(), "workload domain");
            eq("wf-194", diagnosis.workflowId(), "workflow from event evidence");
            eq("NSX_MANAGER", diagnosis.component(), "component from event evidence");
            eq("CERTIFICATE_EXPIRED", diagnosis.failureCode(), "root cause code from log evidence");
            eq("Upgrade workflow wf-194 failed for NSX_MANAGER", diagnosis.eventText(), "event text");
            eq("TLS handshake rejected: manager certificate expired", diagnosis.rootCauseText(), "root log text");

            List<MockVcfOperations.RecordedRequest> requests = mock.requests();
            eq(2, requests.size(), "event and log request count");
            assertWire(requests.get(0), EVENT_BODY_1);
            assertWire(requests.get(1), LOG_BODY_1);
        }
    }

    private static void testAlternateEvidenceAndJsonEscaping() throws Exception {
        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200, EVENT_RESPONSE_2),
                new MockVcfOperations.Reply(200, LOG_RESPONSE_2)))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl() + "/", TOKEN);
            VcfOperationsLogClient.Diagnosis diagnosis = client.diagnoseUpgradeFailure(
                    "wd-\"blue", "corr\\88", "2026-06-02T01:00:00Z", "2026-06-02T01:30:00Z");

            eq("VCENTER", diagnosis.component(), "alternate component must come from event");
            eq("wf-88", diagnosis.workflowId(), "alternate workflow must come from event");
            eq("DATASTORE_CAPACITY_EXHAUSTED", diagnosis.failureCode(), "alternate root cause must not be guessed");
            eq("Datastore café is below the upgrade free-space threshold", diagnosis.rootCauseText(), "UTF-8 evidence");

            List<MockVcfOperations.RecordedRequest> requests = mock.requests();
            eq(2, requests.size(), "alternate request count");
            assertWire(requests.get(0), EVENT_BODY_2);
            assertWire(requests.get(1), LOG_BODY_2);
        }
    }

    private static void testErrorsTimeoutAndValidation() throws Exception {
        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(403, "{\"errorCode\":\"SECURITY_ERROR\",\"errorMessage\":\"access denied for "
                        + TOKEN + "\",\"errorDetails\":{}}")))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            try {
                client.diagnoseUpgradeFailure("wd-edge", "corr-9f", "start", "end");
                fail("expected VcfApiException");
            } catch (VcfOperationsLogClient.VcfApiException ex) {
                eq(403, ex.statusCode(), "API status");
                eq("SECURITY_ERROR", ex.errorCode(), "API error code");
                check(ex.getMessage().contains("access denied"), "API message detail");
                check(!ex.getMessage().contains(TOKEN), "exception must not leak token");
            }
            eq(1, mock.requests().size(), "HTTP error stops diagnosis");
        }

        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200, "{\"events\":{\"total\":0,\"hits\":[]},\"timedOut\":true,\"failureReason\":\"SYSTEM\",\"failureMessage\":\"deadline\"}")))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            expect(IllegalStateException.class,
                    () -> client.diagnoseUpgradeFailure("wd-edge", "corr-9f", "start", "end"),
                    "timed out search");
            eq(1, mock.requests().size(), "timeout stops diagnosis");
        }

        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200,
                        "{\"events\":{\"total\":0,\"hits\":[]},\"timeTakenMillis\":1,\"timedOut\":false}")))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            expect(IllegalStateException.class,
                    () -> client.diagnoseUpgradeFailure("wd-edge", "corr-9f", "start", "end"),
                    "missing lifecycle event evidence");
            eq(1, mock.requests().size(), "missing event stops diagnosis");
        }

        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200, "{}")))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            expect(IllegalStateException.class,
                    () -> client.diagnoseUpgradeFailure("wd-edge", "corr-9f", "start", "end"),
                    "malformed success envelope");
            eq(1, mock.requests().size(), "malformed response stops diagnosis");
        }

        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200, EVENT_RESPONSE_1),
                new MockVcfOperations.Reply(200, "{}")))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            expect(IllegalStateException.class,
                    () -> client.diagnoseUpgradeFailure(
                            "wd-edge", "corr-9f", "2026-06-01T10:00:00Z", "2026-06-01T10:15:00Z"),
                    "malformed log-search response");
            eq(2, mock.requests().size(), "malformed second response follows event pull");
        }

        String noRootCause = LOG_RESPONSE_1.replace("\"value\":true,\"valueType\":\"BOOLEAN\"",
                "\"value\":false,\"valueType\":\"BOOLEAN\"");
        try (MockVcfOperations mock = new MockVcfOperations(List.of(
                new MockVcfOperations.Reply(200, EVENT_RESPONSE_1),
                new MockVcfOperations.Reply(200, noRootCause)))) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            expect(IllegalStateException.class,
                    () -> client.diagnoseUpgradeFailure(
                            "wd-edge", "corr-9f", "2026-06-01T10:00:00Z", "2026-06-01T10:15:00Z"),
                    "missing root-cause evidence");
            eq(2, mock.requests().size(), "missing evidence still requires both pulls");
        }

        try (MockVcfOperations mock = new MockVcfOperations(List.of())) {
            VcfOperationsLogClient client = new VcfOperationsLogClient(mock.baseUrl(), TOKEN);
            List<ThrowingRunnable> invalidCalls = List.of(
                    () -> client.diagnoseUpgradeFailure(" ", "corr", "start", "end"),
                    () -> client.diagnoseUpgradeFailure("wd", " ", "start", "end"),
                    () -> client.diagnoseUpgradeFailure("wd", "corr", " ", "end"),
                    () -> client.diagnoseUpgradeFailure("wd", "corr", "start", " "));
            for (ThrowingRunnable invalidCall : invalidCalls) {
                expect(IllegalArgumentException.class, invalidCall, "blank input");
            }
            eq(0, mock.requests().size(), "validation happens before the wire");
        }

        expect(IllegalArgumentException.class,
                () -> new VcfOperationsLogClient(" ", TOKEN),
                "blank base URL");
        expect(IllegalArgumentException.class,
                () -> new VcfOperationsLogClient("http://127.0.0.1", " "),
                "blank JWT token");
    }

    private static void testMockDoesNotServeDeprecatedAlias() throws Exception {
        try (MockVcfOperations mock = new MockVcfOperations(List.of())) {
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(URI.create(mock.baseUrl() + "/api/v2/search"))
                            .POST(HttpRequest.BodyPublishers.ofString("{}"))
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            eq(404, response.statusCode(), "deprecated alias must not be served by fixture");
        }
    }

    private static void assertWire(MockVcfOperations.RecordedRequest request, String expectedBody) {
        eq("POST", request.method(), "HTTP method");
        eq("/api/v2/logs/search", request.path(), "operation path");
        eq(null, request.rawQuery(), "query string must be absent");
        eq("application/json", request.header("Accept"), "Accept header");
        eq("application/json", request.header("Content-Type"), "Content-Type header");
        eq(TOKEN, request.header("X-JWT-Token"), "VCF auth header");
        eq(null, request.header("Authorization"), "no alternate auth header");
        eq(expectedBody, request.body(), "exact UTF-8 JSON wire body");

        for (String omitted : List.of("aggregations", "from", "scroll", "scrollSize", "empty")) {
            check(!request.body().contains("\"" + omitted + "\""),
                    "unset optional field must be omitted: " + omitted);
        }
    }

    private static void expect(Class<? extends Throwable> type, ThrowingRunnable action, String label) {
        try {
            action.run();
            fail("expected " + type.getSimpleName() + " for " + label);
        } catch (Throwable thrown) {
            if (!type.isInstance(thrown)) {
                throw new AssertionError(label + " threw " + thrown, thrown);
            }
        }
    }

    private static void eq(Object expected, Object actual, String label) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
