import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

public class TestMain {
    private static int checks;
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path SOURCES = Path.of("docs", "official_sources.json");

    public static void main(String[] args) throws Exception {
        checkProtectedProvenance();
        checkEvidenceBasedDiagnosisAndWireShape();
        checkNonFailedTaskStopsWorkflow();
        checkTerminalApiFailureStopsWorkflow();
        System.out.println("ALL VCENTER ATTESTATION CONTRACT CHECKS PASSED (" + checks + ")");
    }

    private static void checkProtectedProvenance() throws Exception {
        String contract = Files.readString(CONTRACT, StandardCharsets.UTF_8);
        String sources = Files.readString(SOURCES, StandardCharsets.UTF_8);
        String commit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";
        String blob = "8028b0824c4ff3503d05f44814f967938a795c40";
        String spec = "specifications/vsphere/openapi/automation/vcenter.yaml";
        List<String> operationIds = List.of(
                "Cis.Tasks_get",
                "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get",
                "Appliance.SupportBundle_create$Task");

        contains(contract, "\"commitSha\": \"" + commit + "\"", "contract commit");
        contains(contract, "\"specBlobSha\": \"" + blob + "\"", "contract blob");
        contains(contract, "\"specPath\": \"" + spec + "\"", "contract spec path");
        contains(contract, "\"openapi\": \"3.0.3\"", "OpenAPI version");
        contains(contract, "\"apiVersion\": \"9.1.0.0\"", "API version");
        contains(contract, "\"name\": \"vmware-api-session-id\"", "session header");
        contains(sources, "\"repositoryCommitSha\": \"" + commit + "\"",
                "source commit");
        contains(sources, "\"specBlobSha\": \"" + blob + "\"", "source blob");
        contains(sources, "\"specPath\": \"" + spec + "\"", "source spec path");
        for (String operationId : operationIds) {
            equal(1, occurrences(sources, "\"operationId\": \"" + operationId + "\""),
                    "source operation record " + operationId);
            equal(1, occurrences(contract, "\"operationId\": \"" + operationId + "\""),
                    "contract operation record " + operationId);
        }
        equal(4, occurrences(sources, "\"repositoryCommitSha\": \"" + commit + "\""),
                "top-level and per-operation source commits");
        equal(4, occurrences(sources, "\"specPath\": \"" + spec + "\""),
                "top-level and per-operation source paths");
    }

    private static void checkEvidenceBasedDiagnosisAndWireShape() throws Exception {
        String session = "session-secret-91";
        String task = "task attest/42";
        String host = "host 17/edge";
        String tpm = "tpm#0";
        String description = "TPM \"trust\"\nlogs for host 17/edge";

        try (ContractMock mock = new ContractMock(
                CONTRACT, ContractMock.Scenario.FAILED_ATTESTATION)) {
            VcenterAttestationDiagnosticsClient client =
                    new VcenterAttestationDiagnosticsClient(
                            mock.baseUrl() + "/",
                            session,
                            HttpClient.newHttpClient());

            VcenterAttestationDiagnosticsClient.Diagnosis diagnosis =
                    client.diagnoseFailedAttestation(task, host, tpm, description);

            equal("FAILED", diagnosis.taskStatus(), "failed task status");
            equal("FAILED_ATTESTATION", diagnosis.taskErrorType(), "task error type");
            equal("Host trust check failed; inspect TPM events",
                    diagnosis.taskMessage(), "task message");
            equal("EFI_TCG2_EVENT_LOG_FORMAT_TCG_2",
                    diagnosis.eventLogType(), "event log type");
            equal(ContractMock.EVENT_EVIDENCE,
                    diagnosis.eventEvidence(), "decoded event evidence");
            equal(false, diagnosis.eventLogTruncated(), "event truncation flag");
            equal("SECURE_BOOT_DISABLED", diagnosis.rootCause(),
                    "evidence-based root cause");
            equal(ContractMock.SUPPORT_TASK_ID,
                    diagnosis.supportBundleTaskId(), "support task id");

            List<ContractMock.LoggedRequest> requests = mock.requests();
            equal(3, requests.size(), "workflow request count");
            checkCommonRequest(requests.get(0), "Cis.Tasks_get", "GET", session);
            equal("/api/cis/tasks/task%20attest%2F42",
                    requests.get(0).rawPath(), "encoded task path");
            equal(null, requests.get(0).rawQuery(), "task query omitted");
            equal(0, requests.get(0).body().length, "task GET body");
            equal(null, requests.get(0).firstHeader("Content-Type"),
                    "task content type omitted");

            checkCommonRequest(
                    requests.get(1),
                    "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get",
                    "GET",
                    session);
            equal("/api/vcenter/trusted-infrastructure/hosts/"
                            + "host%2017%2Fedge/hardware/tpm/tpm%230/event-log",
                    requests.get(1).rawPath(), "encoded event path");
            equal(null, requests.get(1).rawQuery(), "event query omitted");
            equal(0, requests.get(1).body().length, "event GET body");
            equal(null, requests.get(1).firstHeader("Content-Type"),
                    "event content type omitted");

            checkCommonRequest(
                    requests.get(2),
                    "Appliance.SupportBundle_create$Task",
                    "POST",
                    session);
            equal("/api/appliance/support-bundle",
                    requests.get(2).rawPath(), "support path");
            equal("vmw-task=true", requests.get(2).rawQuery(), "support fixed query");
            equal("application/json",
                    mediaType(requests.get(2).firstHeader("Content-Type")),
                    "support content type");
            String expectedBody =
                    "{\"description\":\"TPM \\\"trust\\\"\\nlogs for host 17/edge\","
                            + "\"content_type\":\"LOGS\"}";
            equal(expectedBody, requests.get(2).bodyUtf8(), "exact support body");
            absent(requests.get(2).bodyUtf8(), "\"components\"",
                    "unset support components");
            absent(requests.get(2).bodyUtf8(), "\"partition\"",
                    "unset support partition");
            absent(requests.get(2).bodyUtf8(), ":null", "JSON null substitute");
            absent(requests.get(2).bodyUtf8(), ":\"\"", "empty string substitute");
            absent(requests.get(2).bodyUtf8(), ":[]", "empty array substitute");
            absent(requests.get(2).bodyUtf8(), ":{}", "empty object substitute");
        }
    }

    private static void checkNonFailedTaskStopsWorkflow() throws Exception {
        try (ContractMock mock = new ContractMock(
                CONTRACT, ContractMock.Scenario.RUNNING_TASK)) {
            VcenterAttestationDiagnosticsClient client =
                    new VcenterAttestationDiagnosticsClient(
                            mock.baseUrl(),
                            "running-session",
                            HttpClient.newHttpClient());
            try {
                client.diagnoseFailedAttestation(
                        "task-running", "host-running", "tpm-running", "not collected");
                throw new AssertionError("a RUNNING task must not be diagnosed as failed");
            } catch (IllegalStateException expected) {
                contains(expected.getMessage(), "FAILED",
                        "non-failed task exception explains required state");
            }
            List<ContractMock.LoggedRequest> requests = mock.requests();
            equal(1, requests.size(), "non-failed task stops after one request");
            equal("Cis.Tasks_get", requests.get(0).operationId(),
                    "only task operation for non-failed task");
        }
    }

    private static void checkTerminalApiFailureStopsWorkflow() throws Exception {
        String session = "must-not-leak-session-value";
        try (ContractMock mock = new ContractMock(
                CONTRACT, ContractMock.Scenario.EVENT_SERVICE_UNAVAILABLE)) {
            VcenterAttestationDiagnosticsClient client =
                    new VcenterAttestationDiagnosticsClient(
                            mock.baseUrl(),
                            session,
                            HttpClient.newHttpClient());
            try {
                client.diagnoseFailedAttestation(
                        "task-failed", "host-failed", "tpm-failed", "not collected");
                throw new AssertionError("event HTTP 503 must throw VcenterApiException");
            } catch (VcenterAttestationDiagnosticsClient.VcenterApiException expected) {
                equal(503, expected.statusCode(), "terminal status code");
                contains(expected.responseBody(), "SERVICE_UNAVAILABLE",
                        "terminal response body");
                absent(expected.getMessage(), session, "session in exception message");
            }
            List<ContractMock.LoggedRequest> requests = mock.requests();
            equal(2, requests.size(), "event failure stops before support bundle");
            equal("Cis.Tasks_get", requests.get(0).operationId(),
                    "terminal scenario task operation");
            equal("Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get",
                    requests.get(1).operationId(), "terminal scenario event operation");
        }
    }

    private static void checkCommonRequest(
            ContractMock.LoggedRequest request,
            String operationId,
            String method,
            String session) {
        equal(operationId, request.operationId(), operationId + " route");
        equal(method, request.method(), operationId + " method");
        equal("application/json", mediaType(request.firstHeader("Accept")),
                operationId + " accept");
        equal(session, request.firstHeader("vmware-api-session-id"),
                operationId + " session");
        equal(null, request.firstHeader("Authorization"),
                operationId + " authorization omitted");
    }

    private static String mediaType(String value) {
        if (value == null) {
            return null;
        }
        int semicolon = value.indexOf(';');
        return (semicolon < 0 ? value : value.substring(0, semicolon)).trim();
    }

    private static int occurrences(String text, String value) {
        int count = 0;
        int from = 0;
        while (true) {
            int at = text.indexOf(value, from);
            if (at < 0) {
                return count;
            }
            count++;
            from = at + value.length();
        }
    }

    private static void contains(String text, String value, String label) {
        checks++;
        if (text == null || !text.contains(value)) {
            throw new AssertionError(label + " missing <" + value + "> in <" + text + ">");
        }
    }

    private static void absent(String text, String value, String label) {
        checks++;
        if (text != null && text.contains(value)) {
            throw new AssertionError(label + " unexpectedly contains <" + value + ">");
        }
    }

    private static void equal(Object expected, Object actual, String label) {
        checks++;
        if (!java.util.Objects.deepEquals(expected, actual)) {
            throw new AssertionError(
                    label + " expected <" + expected + "> but got <" + actual + ">");
        }
    }
}
