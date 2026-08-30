import java.net.URI;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

public final class TestMain {
    private static final Path CONTRACT = Path.of("docs/contract.json");

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    public static void main(String[] args) throws Exception {
        testLateFailurePreservesCommittedStepsAndExactWire();
        testEarlyFailureStopsImmediately();
        testMiddleFailurePreservesNamespaceAndStops();
        testSuccessAndImmutableLedger();
        testRedirectIsNotFollowed();
        testValidationPerformsNoTraffic();
        testTransportFailureCarriesUnknownLedger();
        System.out.println(
                "PASS: contract-pinned partial VCF/VKS change is reported");
    }

    private static void testLateFailurePreservesCommittedStepsAndExactWire()
            throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.VERSION);
        try (ContractMockServer mock =
                     new ContractMockServer(CONTRACT, fixture)) {
            VcfVksChangeClient client = client(mock, fixture);
            assertEquals(0, mock.requests().size(),
                    "construction must perform no traffic");

            VcfVksChangeClient.ChangeReport report =
                    client.apply(change(fixture));

            assertReport(
                    report,
                    VcfVksChangeClient.OverallStatus.FAILED,
                    new VcfVksChangeClient.StepStatus[] {
                        VcfVksChangeClient.StepStatus.SUCCEEDED,
                        VcfVksChangeClient.StepStatus.SUCCEEDED,
                        VcfVksChangeClient.StepStatus.FAILED
                    },
                    new Integer[] {204, 200, 422},
                    new boolean[] {true, true, false});
            assertTrue(mock.namespaceCommitted(),
                    "namespace change must remain committed");
            assertTrue(mock.labelCommitted(),
                    "label change must remain committed");
            assertFalse(mock.versionCommitted(),
                    "rejected version must not be reported committed");

            List<ContractMockServer.RequestLog> requests = mock.requests();
            assertEquals(3, requests.size(), "request count");
            assertWire(
                    requests.get(0),
                    ContractMockServer.NAMESPACE_OPERATION,
                    mock.vcenterTarget(),
                    "vmware-api-session-id",
                    fixture.vcenterSession(),
                    "application/json",
                    mock.expectedNamespaceBody(),
                    "Authorization");
            assertWire(
                    requests.get(1),
                    ContractMockServer.CLUSTER_OPERATION,
                    mock.kubernetesTarget(),
                    "Authorization",
                    "Bearer " + fixture.kubernetesToken(),
                    "application/merge-patch+json",
                    mock.expectedLabelBody(),
                    "vmware-api-session-id");
            assertWire(
                    requests.get(2),
                    ContractMockServer.CLUSTER_OPERATION,
                    mock.kubernetesTarget(),
                    "Authorization",
                    "Bearer " + fixture.kubernetesToken(),
                    "application/merge-patch+json",
                    mock.expectedVersionBody(),
                    "vmware-api-session-id");

            String namespaceJson = utf8(requests.get(0).body());
            for (String unset : List.of(
                    "resource_spec",
                    "access_list",
                    "storage_specs",
                    "vm_service_spec",
                    "content_libraries",
                    "network_spec",
                    "zones",
                    "edges",
                    "infrastructure_policies")) {
                assertFalse(namespaceJson.contains(unset),
                        "unset namespace field leaked: " + unset);
            }
            for (ContractMockServer.RequestLog request : requests) {
                for (String unset : List.of(
                        "dryRun",
                        "fieldManager",
                        "fieldValidation",
                        "force",
                        "pretty")) {
                    assertFalse(request.rawTarget().contains(unset),
                            "unset Kubernetes query field leaked: " + unset);
                }
                assertFalse(request.rawTarget().contains("?"),
                        "raw target must not have a query delimiter");
            }

            String rendered = report.toString();
            assertFalse(rendered.contains(fixture.vcenterSession()),
                    "report leaked vCenter credential");
            assertFalse(rendered.contains(fixture.kubernetesToken()),
                    "report leaked Kubernetes credential");
            assertFalse(rendered.contains(fixture.sensitiveMarker()),
                    "report leaked protected response detail");
        }
    }

    private static void testEarlyFailureStopsImmediately() throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.NAMESPACE);
        try (ContractMockServer mock =
                     new ContractMockServer(CONTRACT, fixture)) {
            VcfVksChangeClient.ChangeReport report =
                    client(mock, fixture).apply(change(fixture));
            assertReport(
                    report,
                    VcfVksChangeClient.OverallStatus.FAILED,
                    new VcfVksChangeClient.StepStatus[] {
                        VcfVksChangeClient.StepStatus.FAILED,
                        VcfVksChangeClient.StepStatus.SKIPPED,
                        VcfVksChangeClient.StepStatus.SKIPPED
                    },
                    new Integer[] {409, null, null},
                    new boolean[] {false, false, false});
            assertEquals(1, mock.requests().size(),
                    "later operations must be skipped");
            assertFalse(mock.namespaceCommitted(),
                    "failed namespace change cannot be committed");
            assertFalse(mock.labelCommitted(),
                    "label must not be attempted");
        }
    }

    private static void testSuccessAndImmutableLedger() throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.NONE);
        try (ContractMockServer mock =
                     new ContractMockServer(CONTRACT, fixture)) {
            VcfVksChangeClient.ChangeReport report =
                    client(mock, fixture).apply(change(fixture));
            assertReport(
                    report,
                    VcfVksChangeClient.OverallStatus.SUCCEEDED,
                    new VcfVksChangeClient.StepStatus[] {
                        VcfVksChangeClient.StepStatus.SUCCEEDED,
                        VcfVksChangeClient.StepStatus.SUCCEEDED,
                        VcfVksChangeClient.StepStatus.SUCCEEDED
                    },
                    new Integer[] {204, 200, 200},
                    new boolean[] {true, true, true});
            assertTrue(mock.namespaceCommitted(), "namespace commit");
            assertTrue(mock.labelCommitted(), "label commit");
            assertTrue(mock.versionCommitted(), "version commit");
            expectThrows(
                    UnsupportedOperationException.class,
                    () -> report.steps().add(report.steps().get(0)),
                    "step list must be unmodifiable");
        }
    }

    private static void testMiddleFailurePreservesNamespaceAndStops()
            throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.LABEL);
        try (ContractMockServer mock =
                     new ContractMockServer(CONTRACT, fixture)) {
            VcfVksChangeClient.ChangeReport report =
                    client(mock, fixture).apply(change(fixture));
            assertReport(
                    report,
                    VcfVksChangeClient.OverallStatus.FAILED,
                    new VcfVksChangeClient.StepStatus[] {
                        VcfVksChangeClient.StepStatus.SUCCEEDED,
                        VcfVksChangeClient.StepStatus.FAILED,
                        VcfVksChangeClient.StepStatus.SKIPPED
                    },
                    new Integer[] {204, 409, null},
                    new boolean[] {true, false, false});
            assertEquals(2, mock.requests().size(),
                    "version patch must be skipped");
            assertTrue(mock.namespaceCommitted(),
                    "completed namespace change must be preserved");
            assertFalse(mock.labelCommitted(),
                    "failed label patch cannot be committed");
            assertFalse(mock.versionCommitted(),
                    "version patch must not be attempted");
        }
    }

    private static void testRedirectIsNotFollowed() throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.REDIRECT);
        try (ContractMockServer mock =
                     new ContractMockServer(CONTRACT, fixture)) {
            VcfVksChangeClient.ChangeReport report =
                    client(mock, fixture).apply(change(fixture));
            assertReport(
                    report,
                    VcfVksChangeClient.OverallStatus.FAILED,
                    new VcfVksChangeClient.StepStatus[] {
                        VcfVksChangeClient.StepStatus.FAILED,
                        VcfVksChangeClient.StepStatus.SKIPPED,
                        VcfVksChangeClient.StepStatus.SKIPPED
                    },
                    new Integer[] {307, null, null},
                    new boolean[] {false, false, false});
            assertEquals(1, mock.requests().size(),
                    "redirect target must not be contacted");
        }
    }

    private static void testValidationPerformsNoTraffic() throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.NONE);
        try (ContractMockServer mock =
                     new ContractMockServer(CONTRACT, fixture)) {
            VcfVksChangeClient client = client(mock, fixture);
            expectThrows(
                    IllegalArgumentException.class,
                    () -> client.apply(new VcfVksChangeClient.Change(
                            " ", fixture.clusterName(),
                            fixture.namespaceDescription(),
                            fixture.labelKey(), fixture.labelValue(),
                            fixture.targetVersion())),
                    "blank namespace");
            assertEquals(0, mock.requests().size(),
                    "invalid input must not perform traffic");

            expectThrows(
                    IllegalArgumentException.class,
                    () -> new VcfVksChangeClient(
                            HttpClient.newBuilder()
                                    .followRedirects(
                                            HttpClient.Redirect.ALWAYS)
                                    .build(),
                            mock.vcenterApiBase(),
                            mock.kubernetesOrigin(),
                            fixture.vcenterSession(),
                            fixture.kubernetesToken(),
                            Duration.ofSeconds(2)),
                    "redirecting client must be rejected");
            expectThrows(
                    IllegalArgumentException.class,
                    () -> new VcfVksChangeClient(
                            mock.client(),
                            URI.create("http://user@127.0.0.1/api"),
                            mock.kubernetesOrigin(),
                            fixture.vcenterSession(),
                            fixture.kubernetesToken(),
                            Duration.ofSeconds(2)),
                    "URI credentials must be rejected");
            expectThrows(
                    IllegalArgumentException.class,
                    () -> new VcfVksChangeClient(
                            mock.client(),
                            mock.vcenterApiBase(),
                            mock.kubernetesOrigin(),
                            "bad\rcredential",
                            fixture.kubernetesToken(),
                            Duration.ofSeconds(2)),
                    "header-unsafe credential must be rejected");
            assertEquals(0, mock.requests().size(),
                    "constructor validation must be traffic-free");
        }
    }

    private static void testTransportFailureCarriesUnknownLedger()
            throws Exception {
        ContractMockServer.Fixture fixture = fixture(
                ContractMockServer.FailurePoint.NONE);
        ContractMockServer mock = new ContractMockServer(CONTRACT, fixture);
        VcfVksChangeClient client = client(mock, fixture);
        mock.close();
        VcfVksChangeClient.ChangeTransportException error = expectThrows(
                VcfVksChangeClient.ChangeTransportException.class,
                () -> client.apply(change(fixture)),
                "closed listener must produce transport report");
        assertReport(
                error.report(),
                VcfVksChangeClient.OverallStatus.UNKNOWN,
                new VcfVksChangeClient.StepStatus[] {
                    VcfVksChangeClient.StepStatus.UNKNOWN,
                    VcfVksChangeClient.StepStatus.SKIPPED,
                    VcfVksChangeClient.StepStatus.SKIPPED
                },
                new Integer[] {null, null, null},
                new boolean[] {false, false, false});
        assertFalse(error.toString().contains(fixture.vcenterSession()),
                "transport exception leaked vCenter credential");
        assertFalse(error.toString().contains(fixture.kubernetesToken()),
                "transport exception leaked Kubernetes credential");
    }

    private static VcfVksChangeClient client(
            ContractMockServer mock,
            ContractMockServer.Fixture fixture) {
        return new VcfVksChangeClient(
                mock.client(),
                mock.vcenterApiBase(),
                mock.kubernetesOrigin(),
                fixture.vcenterSession(),
                fixture.kubernetesToken(),
                Duration.ofSeconds(3));
    }

    private static VcfVksChangeClient.Change change(
            ContractMockServer.Fixture fixture) {
        return new VcfVksChangeClient.Change(
                fixture.namespace(),
                fixture.clusterName(),
                fixture.namespaceDescription(),
                fixture.labelKey(),
                fixture.labelValue(),
                fixture.targetVersion());
    }

    private static ContractMockServer.Fixture fixture(
            ContractMockServer.FailurePoint failurePoint) {
        String suffix = UUID.randomUUID()
                .toString().replace("-", "").substring(0, 12);
        return new ContractMockServer.Fixture(
                "team \u03a9/" + suffix,
                "payments?blue#" + suffix,
                "owned by \"platform\" \u2603\n" + suffix,
                "operations.example.com/window",
                "wave-\u03b2-" + suffix,
                "v1.33.1+vmware." + suffix,
                "vcs-" + suffix,
                "k8s-" + suffix,
                "do-not-leak-" + suffix,
                failurePoint);
    }

    private static void assertReport(
            VcfVksChangeClient.ChangeReport report,
            VcfVksChangeClient.OverallStatus overall,
            VcfVksChangeClient.StepStatus[] statuses,
            Integer[] httpStatuses,
            boolean[] changed) {
        assertEquals(overall, report.overallStatus(), "overall status");
        assertEquals(3, report.steps().size(), "step count");
        String[] names = {
            "namespace-description",
            "cluster-maintenance-label",
            "cluster-version"
        };
        String[] operations = {
            VcfVksChangeClient.NAMESPACE_UPDATE_OPERATION,
            VcfVksChangeClient.CLUSTER_PATCH_OPERATION,
            VcfVksChangeClient.CLUSTER_PATCH_OPERATION
        };
        for (int index = 0; index < 3; index++) {
            VcfVksChangeClient.StepResult step =
                    report.steps().get(index);
            assertEquals(names[index], step.name(),
                    "step name " + index);
            assertEquals(operations[index], step.operation(),
                    "step operation " + index);
            assertEquals(statuses[index], step.status(),
                    "step status " + index);
            assertEquals(httpStatuses[index], step.httpStatus(),
                    "step HTTP status " + index);
            assertEquals(changed[index], step.changed(),
                    "step changed flag " + index);
        }
    }

    private static void assertWire(
            ContractMockServer.RequestLog request,
            String operation,
            String rawTarget,
            String authenticationHeader,
            String authenticationValue,
            String contentType,
            byte[] body,
            String forbiddenAuthenticationHeader) {
        assertEquals(operation, request.operation(), "operation");
        assertEquals("PATCH", request.method(), "method");
        assertEquals(rawTarget, request.rawTarget(), "raw target");
        assertEquals(List.of("application/json"),
                request.headerValues("Accept"), "Accept header");
        assertEquals(List.of(authenticationValue),
                request.headerValues(authenticationHeader),
                authenticationHeader + " header");
        assertEquals(List.of(), request.headerValues(
                forbiddenAuthenticationHeader),
                "cross-boundary credential header");
        assertEquals(List.of(contentType),
                request.headerValues("Content-Type"),
                "Content-Type header");
        assertEquals(List.of(Integer.toString(body.length)),
                request.headerValues("Content-Length"),
                "Content-Length header");
        assertEquals(List.of(),
                request.headerValues("Transfer-Encoding"),
                "Transfer-Encoding header");
        assertEquals(List.of(),
                request.headerValues("Content-Encoding"),
                "Content-Encoding header");
        assertTrue(Arrays.equals(body, request.body()),
                "request body bytes");
    }

    private static String utf8(byte[] value) {
        return new String(value, StandardCharsets.UTF_8);
    }

    private static <T extends Throwable> T expectThrows(
            Class<T> type,
            ThrowingRunnable runnable,
            String message) throws Exception {
        try {
            runnable.run();
        } catch (Throwable error) {
            if (type.isInstance(error)) {
                return type.cast(error);
            }
            throw new AssertionError(
                    message + ": wrong exception " + error, error);
        }
        throw new AssertionError(message + ": no exception");
    }

    private static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean condition, String message) {
        assertTrue(!condition, message);
    }

    private static void assertEquals(
            Object expected, Object actual, String message) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(
                    message + ": expected " + expected
                            + ", got " + actual);
        }
    }
}
