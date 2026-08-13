import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.List;
import java.util.Objects;

/**
 * Contract verifier for VcfAutomationVirtualCenterClient. Every assertion runs against the
 * loopback fixture in MockVcfAutomation and its request log. No live VMware endpoint is used.
 */
public final class TestMain {
    private static final String TOKEN = MockVcfAutomation.DEFAULT_TOKEN;
    private static final String PASSWORD = "fixture-secret-never-real";
    private static final String USERNAME = "svc-vcfa@vsphere.local";
    private static final String ORG = "urn:vcloud:org:9d2c1b40-0f43-4b6a-9a0e-6a4f5d3c2b17";
    private static final String PATH = MockVcfAutomation.VIRTUAL_CENTERS_PATH;
    private static final String ACCEPT = MockVcfAutomation.ACCEPT;

    private static final String ALPHA_URL = "https://vc-a.lab.example.com";
    private static final String ALPHA_QUERY =
            "filter=url%3D%3Dhttps%3A%2F%2Fvc-a.lab.example.com&page=1&pageSize=128";
    private static final String ALPHA_BODY = "{\"name\":\"vc-alpha\",\"username\":\"" + USERNAME
            + "\",\"password\":\"" + PASSWORD + "\",\"url\":\"" + ALPHA_URL + "\"}";

    private static final String BETA_URL = "https://vc-b.lab.example.com:8443";
    private static final String BETA_QUERY =
            "filter=url%3D%3Dhttps%3A%2F%2Fvc-b.lab.example.com%3A8443&page=1&pageSize=128";
    private static final String BETA_DESCRIPTION = "Bordeaux caf\u00e9 / rack \\ B";
    private static final String BETA_BODY = "{\"name\":\"vc \\\"beta\\\"\",\"description\":\""
            + "Bordeaux caf\u00e9 / rack \\\\ B\",\"username\":\"" + USERNAME + "\",\"password\":\""
            + PASSWORD + "\",\"url\":\"" + BETA_URL + "\",\"isEnabled\":true}";

    public static void main(String[] args) throws Exception {
        testAttachThenRetryIsSafe();
        testOptionalFieldsTenantContextAndEscaping();
        testAlreadyAttachedIsDetectedByUrl();
        testAmbiguousInventoryIsRejected();
        testErrorsAndValidation();
        testFixtureRejectsABlindRepeatAttach();
        testFixtureServesOnlyTheContractedOperations();
        System.out.println("PASS: VCF Automation Attach Virtual Center contract");
    }

    private static void testAttachThenRetryIsSafe() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);

            VcfAutomationVirtualCenterClient.AttachOutcome first =
                    client.ensureVirtualCenterAttached(
                            "vc-alpha", ALPHA_URL, USERNAME, PASSWORD, null, null);

            eq(false, first.alreadyAttached(), "first attach must not report an existing vCenter");
            eq(null, first.vcId(), "the accepted attach is asynchronous, so no vcId is known yet");
            eq(mock.taskLocation(1), first.taskLocation(), "task URI from the 202 Location header");

            List<MockVcfAutomation.RecordedRequest> log = mock.requestLog();
            eq(2, log.size(), "one query then one attach");
            assertQueryWire(log.get(0), ALPHA_QUERY, null);
            assertAttachWire(log.get(1), ALPHA_BODY, null);
            check(!log.get(1).body().contains("\"description\""), "unset description must be omitted");
            check(!log.get(1).body().contains("\"isEnabled\""), "unset isEnabled must be omitted");

            // The runbook is re-run after a transient failure. The vCenter is already attached.
            VcfAutomationVirtualCenterClient.AttachOutcome retry =
                    client.ensureVirtualCenterAttached(
                            "vc-alpha", ALPHA_URL, USERNAME, PASSWORD, null, null);

            eq(true, retry.alreadyAttached(), "the retry must detect the existing attachment");
            eq("urn:vcloud:vimserver:0f1e0000-0000-4000-8000-000000000001", retry.vcId(),
                    "the retry must report the vcId the fixture assigned");
            eq(null, retry.taskLocation(), "a retry that attaches nothing has no task");

            log = mock.requestLog();
            eq(3, log.size(), "the retry issues exactly one more request");
            assertQueryWire(log.get(2), ALPHA_QUERY, null);
            eq(1L, mock.countRequests("POST"), "a retry must not repeat the mutating call");
            eq(1, mock.inventory().size(), "the vCenter must be attached exactly once");
        }
    }

    private static void testOptionalFieldsTenantContextAndEscaping() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, ORG);

            VcfAutomationVirtualCenterClient.AttachOutcome outcome =
                    client.ensureVirtualCenterAttached("vc \"beta\"", BETA_URL, USERNAME, PASSWORD,
                            BETA_DESCRIPTION, Boolean.TRUE);

            eq(false, outcome.alreadyAttached(), "a new vCenter must be attached");
            eq(mock.taskLocation(1), outcome.taskLocation(), "task URI");

            List<MockVcfAutomation.RecordedRequest> log = mock.requestLog();
            eq(2, log.size(), "one query then one attach");
            assertQueryWire(log.get(0), BETA_QUERY, ORG);
            assertAttachWire(log.get(1), BETA_BODY, ORG);

            MockVcfAutomation.VCenter stored = mock.inventory().get(0);
            eq("vc \"beta\"", stored.name(), "the fixture decoded the escaped name");
            eq(BETA_DESCRIPTION, stored.description(), "the fixture decoded the UTF-8 description");
            eq(Boolean.TRUE, stored.isEnabled(), "isEnabled must reach the API as a JSON boolean");
        }

        // A blank description and a null isEnabled are unset, not empty values on the wire.
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, "   ");
            client.ensureVirtualCenterAttached("vc-alpha", ALPHA_URL, USERNAME, PASSWORD, "  ", null);
            List<MockVcfAutomation.RecordedRequest> log = mock.requestLog();
            eq(2, log.size(), "one query then one attach");
            assertAttachWire(log.get(1), ALPHA_BODY, null);
            eq(null, log.get(0).header("X-VMWARE-VCLOUD-TENANT-CONTEXT"),
                    "a blank organization id is no organization context");
        }
    }

    private static void testAlreadyAttachedIsDetectedByUrl() throws Exception {
        MockVcfAutomation.VCenter existing = new MockVcfAutomation.VCenter(
                "urn:vcloud:vimserver:3b6a11c4-5e77-4a0d-9d1f-2c88f0a91b53",
                "vc-gamma-from-day-one", "https://vc-c.lab.example.com", "other@vsphere.local",
                "attached last quarter", Boolean.TRUE);
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of(existing))) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);

            VcfAutomationVirtualCenterClient.AttachOutcome outcome =
                    client.ensureVirtualCenterAttached("vc-gamma", "https://vc-c.lab.example.com",
                            USERNAME, PASSWORD, "new runbook description", Boolean.FALSE);

            eq(true, outcome.alreadyAttached(), "the vCenter url is already attached");
            eq(existing.vcId(), outcome.vcId(), "report the vcId the API returned");
            eq(null, outcome.taskLocation(), "nothing was submitted");
            eq(1, mock.requestLog().size(), "one query is enough");
            eq(0L, mock.countRequests("POST"), "an attached vCenter must never be attached again");
            eq(1, mock.inventory().size(), "inventory is untouched");
        }
    }

    private static void testAmbiguousInventoryIsRejected() throws Exception {
        MockVcfAutomation.VCenter one = new MockVcfAutomation.VCenter(
                "urn:vcloud:vimserver:1111aaaa-0000-4000-8000-000000000001", "vc-dup-a",
                "https://vc-d.lab.example.com", USERNAME, null, Boolean.TRUE);
        MockVcfAutomation.VCenter two = new MockVcfAutomation.VCenter(
                "urn:vcloud:vimserver:1111aaaa-0000-4000-8000-000000000002", "vc-dup-b",
                "https://vc-d.lab.example.com", USERNAME, null, Boolean.TRUE);
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of(one, two))) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);
            expect(IllegalStateException.class,
                    () -> client.ensureVirtualCenterAttached("vc-delta",
                            "https://vc-d.lab.example.com", USERNAME, PASSWORD, null, null),
                    "an ambiguous filtered page");
            eq(1, mock.requestLog().size(), "ambiguity is decided from the query alone");
            eq(0L, mock.countRequests("POST"), "ambiguity must never lead to a mutating call");
        }
    }

    private static void testErrorsAndValidation() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            mock.failNextAttach(400, "INVALID_CONFIGURATION",
                    "The vCenter password " + PASSWORD + " was rejected for session " + TOKEN + ".");
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);
            try {
                client.ensureVirtualCenterAttached("vc-alpha", ALPHA_URL, USERNAME, PASSWORD, null, null);
                fail("expected VcfAutomationApiException for a rejected attach");
            } catch (VcfAutomationVirtualCenterClient.VcfAutomationApiException ex) {
                eq(400, ex.statusCode(), "status from the API");
                eq("INVALID_CONFIGURATION", ex.minorErrorCode(), "minorErrorCode from the Error body");
                check(ex.getMessage().contains("The vCenter password"),
                        "the non-sensitive part of the API message must be reported");
                check(ex.getMessage().contains("[REDACTED]"),
                        "sensitive values in an API message must be redacted");
                check(!ex.getMessage().contains(PASSWORD), "the password must never leak");
                check(!ex.getMessage().contains(TOKEN), "the token must never leak");
            }
            eq(2, mock.requestLog().size(), "a rejected attach is not retried");
            eq(0, mock.inventory().size(), "nothing was attached");
        }

        try (MockVcfAutomation mock = new MockVcfAutomation("a-different-token", List.of())) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);
            try {
                client.ensureVirtualCenterAttached("vc-alpha", ALPHA_URL, USERNAME, PASSWORD, null, null);
                fail("expected VcfAutomationApiException for an unauthorized query");
            } catch (VcfAutomationVirtualCenterClient.VcfAutomationApiException ex) {
                eq(401, ex.statusCode(), "status from the API");
                eq(null, ex.minorErrorCode(), "a 401 carries no Error data structure");
            }
            eq(1, mock.requestLog().size(), "an unauthorized query stops the workflow");
            eq(0L, mock.countRequests("POST"), "no mutating call after an unauthorized query");
        }

        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            mock.respondToNextQueryWith(200, "{\"resultTotal\":0,\"page\":1,\"pageSize\":128}");
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);
            expect(IllegalStateException.class,
                    () -> client.ensureVirtualCenterAttached(
                            "vc-alpha", ALPHA_URL, USERNAME, PASSWORD, null, null),
                    "a page envelope without values");
            eq(1, mock.requestLog().size(), "a malformed page stops the workflow");
            eq(0L, mock.countRequests("POST"), "a malformed page must not lead to an attach");
        }

        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            mock.omitLocationFromNextAcceptedAttach();
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);
            expect(IllegalStateException.class,
                    () -> client.ensureVirtualCenterAttached(
                            "vc-alpha", ALPHA_URL, USERNAME, PASSWORD, null, null),
                    "an accepted attach without a Location header");
            eq(2, mock.requestLog().size(), "a missing task location is detected after one attach");
            eq(1L, mock.countRequests("POST"), "an accepted attach is not repeated");
        }

        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            VcfAutomationVirtualCenterClient client =
                    new VcfAutomationVirtualCenterClient(mock.baseUrl(), TOKEN, null);
            expect(IllegalArgumentException.class,
                    () -> client.ensureVirtualCenterAttached(
                            " ", ALPHA_URL, USERNAME, PASSWORD, null, null),
                    "a blank name");
            expect(IllegalArgumentException.class,
                    () -> client.ensureVirtualCenterAttached(
                            "vc-alpha", "", USERNAME, PASSWORD, null, null),
                    "a blank url");
            expect(IllegalArgumentException.class,
                    () -> client.ensureVirtualCenterAttached(
                            "vc-alpha", ALPHA_URL, null, PASSWORD, null, null),
                    "a null username");
            expect(IllegalArgumentException.class,
                    () -> client.ensureVirtualCenterAttached(
                            "vc-alpha", ALPHA_URL, USERNAME, " ", null, null),
                    "a blank password");
            eq(0, mock.requestLog().size(), "validation happens before the wire");
        }

        expect(IllegalArgumentException.class,
                () -> new VcfAutomationVirtualCenterClient(" ", TOKEN, null), "a blank base url");
        expect(IllegalArgumentException.class,
                () -> new VcfAutomationVirtualCenterClient("http://127.0.0.1:1", " ", null),
                "a blank token");
    }

    private static void testFixtureRejectsABlindRepeatAttach() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            eq(202, rawAttach(mock, ALPHA_BODY).statusCode(), "first attach is accepted");
            HttpResponse<String> repeat = rawAttach(mock, ALPHA_BODY);
            eq(400, repeat.statusCode(), "the API rejects a second attach of the same url");
            check(repeat.body().contains("DUPLICATE_VIM_SERVER_URL"),
                    "the duplicate rejection carries an Error body");
            eq(1, mock.inventory().size(), "the duplicate was not attached");
        }
    }

    private static void testFixtureServesOnlyTheContractedOperations() throws Exception {
        try (MockVcfAutomation mock = new MockVcfAutomation(List.of())) {
            eq(404, raw(mock, "GET", "/cloudapi/1.0.0/sessions/current", null).statusCode(),
                    "no route outside the contract");
            eq(404, raw(mock, "GET", PATH + "/urn:vcloud:vimserver:0000", null).statusCode(),
                    "Get Virtual Center is not in the contract");
            eq(405, raw(mock, "PUT", PATH, "{}").statusCode(),
                    "only GET and POST are served on the contracted path");
        }
    }

    private static HttpResponse<String> rawAttach(MockVcfAutomation mock, String body)
            throws Exception {
        return raw(mock, "POST", PATH, body);
    }

    private static HttpResponse<String> raw(MockVcfAutomation mock, String method, String path,
            String body) throws Exception {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(mock.baseUrl() + path))
                .header("Authorization", "Bearer " + TOKEN)
                .header("Accept", ACCEPT)
                .method(method, body == null
                        ? HttpRequest.BodyPublishers.noBody()
                        : HttpRequest.BodyPublishers.ofString(body));
        if (body != null) {
            builder.header("Content-Type", "application/json");
        }
        return HttpClient.newHttpClient().send(builder.build(), HttpResponse.BodyHandlers.ofString());
    }

    private static void assertQueryWire(MockVcfAutomation.RecordedRequest request,
            String expectedRawQuery, String expectedOrg) {
        eq("GET", request.method(), "Query Virtual Centers method");
        eq(PATH, request.path(), "Query Virtual Centers path");
        eq(expectedRawQuery, request.rawQuery(), "exact raw query string");
        eq("", request.body(), "a query carries no body");
        assertCommonHeaders(request, expectedOrg);
        eq(null, request.header("Content-Type"), "a query declares no content type");
    }

    private static void assertAttachWire(MockVcfAutomation.RecordedRequest request,
            String expectedBody, String expectedOrg) {
        eq("POST", request.method(), "Attach Virtual Center method");
        eq(PATH, request.path(), "Attach Virtual Center path");
        eq(null, request.rawQuery(), "the attach takes no query parameter");
        assertCommonHeaders(request, expectedOrg);
        eq("application/json", request.header("Content-Type"), "Content-Type header");
        eq(expectedBody, request.body(), "exact UTF-8 JSON wire body");

        for (String omitted : List.of("vcId", "vsphereWebClientServerUrl", "hasProxy", "rootFolder",
                "vcNoneNetwork", "tenantVisibleName", "isConnected", "mode", "listenerState",
                "clusterHealthStatus", "vcVersion", "buildNumber", "uuid", "nsxVManager",
                "proxyConfigurationUrn", "isDedicatedForClassicTenants", "licenseStatus",
                "sddcManager")) {
            check(!request.body().contains("\"" + omitted + "\""),
                    "unset optional property must be omitted: " + omitted);
        }
        check(!request.body().contains("null"), "an unset property is omitted, never sent as null");
        check(!request.body().contains(":\"\""), "an unset property is never sent as an empty string");
        check(!request.body().contains("{}"), "an unset property is never sent as an empty object");
    }

    private static void assertCommonHeaders(MockVcfAutomation.RecordedRequest request,
            String expectedOrg) {
        eq(ACCEPT, request.header("Accept"), "versioned Accept header");
        eq("Bearer " + TOKEN, request.header("Authorization"), "JWT Authorization header");
        eq(expectedOrg, request.header("X-VMWARE-VCLOUD-TENANT-CONTEXT"), "tenant context header");
        eq(null, request.header("x-vcloud-authorization"), "deprecated session header must be absent");
        eq(null, request.header("X-VMWARE-VCLOUD-AUTH-CONTEXT"), "auth context header must be absent");
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
