import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

public final class TestMain {
    private static final String TOKEN = "fixture-access-token-never-real";
    private static final String SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";

    public static void main(String[] args) throws Exception {
        testProtectedContractProvenance();
        testLiveShapedPartialFailureAndExactWire();
        testAllStepsAccepted();
        testFailurePositionsAndExactStatuses();
        testAcceptedResponseProtocolChecks();
        testValidationBeforeWire();
        testMockServesOnlyFocusedOperations();
        System.out.println("PASS: live-aligned VCF bootstrap partial-progress contract");
    }

    private static void testProtectedContractProvenance() throws Exception {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        for (String text : List.of(contract, sources)) {
            check(text.contains(SHA), "pinned commit must be recorded");
            check(text.contains("specifications/vcf-installer/vcf-installer-openapi.json"),
                    "Installer source must be recorded");
            check(text.contains("specifications/sddc-manager/sddc-manager-openapi.json"),
                    "SDDC Manager source must be recorded");
            check(text.contains("updateProxyConfiguration"), "proxy operation must be recorded");
            check(text.contains("updateServicesConfig"), "services operation must be recorded");
            check(text.contains("syncDepotMetadata"), "sync operation must be recorded");
        }
    }

    private static void testLiveShapedPartialFailureAndExactWire() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller()) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            VcfInstallerClient.ProxyConfiguration proxy =
                    new VcfInstallerClient.ProxyConfiguration(
                            true, "proxy.example.com", 3128, "HTTPS", null, null, false);
            try {
                client.configureDepotAccess(proxy, services());
                fail("expected sync failure");
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq("syncDepotMetadata", ex.operationId(), "failed operation");
                eq(500, ex.statusCode(), "sync status");
                assertReport(
                        ex.report(),
                        VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                        List.of(
                                VcfInstallerClient.StepStatus.ACCEPTED,
                                VcfInstallerClient.StepStatus.ACCEPTED,
                                VcfInstallerClient.StepStatus.FAILED),
                        List.of(202, 200, 500));
                eq("proxy-task-0219", ex.report().steps().get(0).taskId(), "proxy task ID");
            }

            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            eq(3, requests.size(), "three attempted operations");
            assertJson(
                    requests.get(0),
                    "PATCH",
                    "/v1/system/proxy-configuration",
                    "{\"isEnabled\":true,\"host\":\"proxy.example.com\",\"port\":3128,"
                            + "\"transferProtocol\":\"HTTPS\",\"isAuthenticated\":false}");
            assertJson(requests.get(1), "PUT", "/v1/services-config", servicesBody());
            assertBodyless(requests.get(2), "/v1/system/settings/depot/depot-sync-info");
        }
    }

    private static void testAllStepsAccepted() throws Exception {
        List<MockVcfInstaller.Reply> replies = List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("success-task")),
                new MockVcfInstaller.Reply(200, MockVcfInstaller.services()),
                new MockVcfInstaller.Reply(202, "{\"syncStatus\":\"SYNC_IN_PROGRESS\"}"));
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient.ChangeReport report =
                    new VcfInstallerClient(mock.baseUrl(), TOKEN)
                            .configureDepotAccess(minimalProxy(), services());
            assertReport(
                    report,
                    VcfInstallerClient.Outcome.ACCEPTED,
                    List.of(
                            VcfInstallerClient.StepStatus.ACCEPTED,
                            VcfInstallerClient.StepStatus.ACCEPTED,
                            VcfInstallerClient.StepStatus.ACCEPTED),
                    List.of(202, 200, 202));
            eq(List.of(
                    "updateProxyConfiguration",
                    "updateServicesConfig",
                    "syncDepotMetadata"),
                    report.steps().stream().map(VcfInstallerClient.StepResult::operationId).toList(),
                    "operation order");
        }
    }

    private static void testFailurePositionsAndExactStatuses() throws Exception {
        String error = MockVcfInstaller.apiError(
                "REJECTED", "VALIDATION", "rejected", "correct input", "ref");
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(200, error)))) {
            try {
                new VcfInstallerClient(mock.baseUrl(), TOKEN)
                        .configureDepotAccess(minimalProxy(), services());
                fail("expected wrong proxy status");
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq("updateProxyConfiguration", ex.operationId(), "proxy operation");
                assertReport(ex.report(), VcfInstallerClient.Outcome.FAILED,
                        List.of(
                                VcfInstallerClient.StepStatus.FAILED,
                                VcfInstallerClient.StepStatus.NOT_RUN,
                                VcfInstallerClient.StepStatus.NOT_RUN),
                        List.of(200, 0, 0));
            }
        }

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-services")),
                new MockVcfInstaller.Reply(202, error)))) {
            try {
                new VcfInstallerClient(mock.baseUrl(), TOKEN)
                        .configureDepotAccess(minimalProxy(), services());
                fail("expected wrong services status");
            } catch (VcfInstallerClient.VcfApiException ex) {
                eq("updateServicesConfig", ex.operationId(), "services operation");
                assertReport(ex.report(), VcfInstallerClient.Outcome.PARTIAL_FAILURE,
                        List.of(
                                VcfInstallerClient.StepStatus.ACCEPTED,
                                VcfInstallerClient.StepStatus.FAILED,
                                VcfInstallerClient.StepStatus.NOT_RUN),
                        List.of(202, 202, 0));
            }
        }
    }

    private static void testAcceptedResponseProtocolChecks() throws Exception {
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(
                        202, "text/plain", MockVcfInstaller.task("wrong-media"))),
                "updateProxyConfiguration",
                List.of(202, 0, 0));
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(
                        202, "{\"name\":\"Proxy\",\"status\":\"COMPLETED_WITH_SUCCESS\","
                                + "\"creationTimestamp\":\"now\"}")),
                "updateProxyConfiguration",
                List.of(202, 0, 0));
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-services")),
                        new MockVcfInstaller.Reply(200, "{}")),
                "updateServicesConfig",
                List.of(202, 200, 0));
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(202, MockVcfInstaller.task("before-sync")),
                        new MockVcfInstaller.Reply(200, MockVcfInstaller.services()),
                        new MockVcfInstaller.Reply(202, "{}")),
                "syncDepotMetadata",
                List.of(202, 200, 202));
    }

    private static void assertProtocol(
            List<MockVcfInstaller.Reply> replies,
            String operationId,
            List<Integer> statuses) throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            try {
                new VcfInstallerClient(mock.baseUrl(), TOKEN)
                        .configureDepotAccess(minimalProxy(), services());
                fail("expected ProtocolException");
            } catch (VcfInstallerClient.ProtocolException ex) {
                eq(operationId, ex.operationId(), "protocol operation");
                eq(statuses, ex.report().steps().stream()
                        .map(VcfInstallerClient.StepResult::httpStatus).toList(),
                        "protocol statuses");
            }
        }
    }

    private static void testValidationBeforeWire() throws Exception {
        for (ThrowingRunnable invalid : List.<ThrowingRunnable>of(
                () -> new VcfInstallerClient(" ", TOKEN),
                () -> new VcfInstallerClient("ftp://127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("http://user@127.0.0.1", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1/path", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1?query", TOKEN),
                () -> new VcfInstallerClient("http://127.0.0.1", "bad\nheader"))) {
            expect(IllegalArgumentException.class, invalid, "constructor validation");
        }
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            VcfInstallerClient client = new VcfInstallerClient(mock.baseUrl(), TOKEN);
            expect(NullPointerException.class,
                    () -> client.configureDepotAccess(null, services()), "null proxy");
            expect(NullPointerException.class,
                    () -> client.configureDepotAccess(minimalProxy(), null), "null services");
            eq(0, mock.requests().size(), "validation before wire");
        }
    }

    private static void testMockServesOnlyFocusedOperations() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            java.net.http.HttpResponse<String> response =
                    java.net.http.HttpClient.newHttpClient().send(
                            java.net.http.HttpRequest.newBuilder(
                                    java.net.URI.create(mock.baseUrl() + "/v1/system/settings/depot"))
                                    .PUT(java.net.http.HttpRequest.BodyPublishers.ofString("{}"))
                                    .build(),
                            java.net.http.HttpResponse.BodyHandlers.ofString());
            eq(404, response.statusCode(), "retired depot route must not be served");
        }
    }

    private static VcfInstallerClient.ProxyConfiguration minimalProxy() {
        return new VcfInstallerClient.ProxyConfiguration(
                false, null, null, null, null, null, null);
    }

    private static VcfInstallerClient.ServicesConfig services() {
        return new VcfInstallerClient.ServicesConfig(List.of(
                new VcfInstallerClient.ServiceConfig(
                        "VCF Depot",
                        "VCF_DEPOT",
                        "depot-service-key",
                        List.of(new VcfInstallerClient.ServiceNode(
                                "VCF Depot",
                                List.of(new VcfInstallerClient.ServiceNodeAddress(
                                        "Fqdn", "vcf-flt01.vcf.lab")))))));
    }

    private static String servicesBody() {
        return "{\"services\":[{\"name\":\"VCF Depot\",\"type\":\"VCF_DEPOT\","
                + "\"key\":\"depot-service-key\",\"nodes\":[{\"name\":\"VCF Depot\","
                + "\"addresses\":[{\"type\":\"Fqdn\","
                + "\"value\":\"vcf-flt01.vcf.lab\"}]}]}]}";
    }

    private static void assertJson(
            MockVcfInstaller.RecordedRequest request,
            String method,
            String path,
            String expectedBody) {
        eq(method, request.method(), "method");
        eq(path, request.rawTarget(), "raw target");
        eq(null, request.rawQuery(), "query");
        eq(expectedBody, new String(request.body(), StandardCharsets.UTF_8), "body");
        eq(List.of("Bearer " + TOKEN), request.headerValues("Authorization"), "authorization");
        eq(List.of("application/json"), request.headerValues("Accept"), "accept");
        eq(List.of("application/json"), request.headerValues("Content-Type"), "content type");
    }

    private static void assertBodyless(
            MockVcfInstaller.RecordedRequest request, String path) {
        eq("PATCH", request.method(), "sync method");
        eq(path, request.rawTarget(), "sync target");
        eq(0, request.body().length, "sync body");
        eq(List.of(), request.headerValues("Content-Type"), "sync content type");
        eq(List.of("Bearer " + TOKEN), request.headerValues("Authorization"), "sync auth");
    }

    private static void assertReport(
            VcfInstallerClient.ChangeReport report,
            VcfInstallerClient.Outcome outcome,
            List<VcfInstallerClient.StepStatus> statuses,
            List<Integer> httpStatuses) {
        eq(outcome, report.outcome(), "outcome");
        eq(statuses, report.steps().stream()
                .map(VcfInstallerClient.StepResult::status).toList(), "step statuses");
        eq(httpStatuses, report.steps().stream()
                .map(VcfInstallerClient.StepResult::httpStatus).toList(), "HTTP statuses");
        try {
            report.steps().add(report.steps().get(0));
            fail("report steps must be immutable");
        } catch (UnsupportedOperationException expected) {
            // expected
        }
    }

    private static void expect(
            Class<? extends Throwable> type, ThrowingRunnable runnable, String label)
            throws Exception {
        try {
            runnable.run();
            fail("expected " + type.getSimpleName() + " for " + label);
        } catch (Throwable thrown) {
            if (!type.isInstance(thrown)) {
                throw new AssertionError(label + " threw " + thrown, thrown);
            }
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void eq(Object expected, Object actual, String label) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected=" + expected + " actual=" + actual);
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
