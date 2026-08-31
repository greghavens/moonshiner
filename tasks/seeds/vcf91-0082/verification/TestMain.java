import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

public class TestMain {
    private static int checks;

    public static void main(String[] args) throws Exception {
        Path contract = Path.of("docs", "contract.json");
        NsxPolicyClient.SegmentSpec desired = new NsxPolicyClient.SegmentSpec(
                "Payments \"blue\"\nreconcile",
                null,
                null,
                null,
                List.of(new NsxPolicyClient.SegmentSubnet("10.42.0.1/24", null)),
                null,
                null);
        String expectedBody =
                "{\"display_name\":\"Payments \\\"blue\\\"\\nreconcile\","
                        + "\"subnets\":[{\"gateway_address\":\"10.42.0.1/24\"}]}";
        String authorization = "Basic " + Base64.getEncoder().encodeToString(
                "automation:retry-secret".getBytes(StandardCharsets.UTF_8));

        verifyCommittedOutcome(contract, desired, expectedBody, authorization);
        verifyAbsentOutcomeRetries(contract, desired, expectedBody, authorization);
        verifyMismatchedOutcomeIsAmbiguous(contract, desired);
        verifyEncodedSlashIsLiveFailure(contract, desired);
        verifyTerminalFailureDoesNotReconcile(contract, desired);

        System.out.println("ALL NSX POLICY CONTRACT CHECKS PASSED (" + checks + " checks)");
    }

    private static NsxPolicyClient client(
            ContractMock mock, List<Integer> pacing, String password) {
        return new NsxPolicyClient(
                mock.baseUrl() + "/",
                "automation",
                password,
                HttpClient.newBuilder().build(),
                pacing::add);
    }

    private static void verifyCommittedOutcome(
            Path contract,
            NsxPolicyClient.SegmentSpec desired,
            String expectedBody,
            String authorization) throws Exception {
        List<Integer> pacing = new ArrayList<>();
        try (ContractMock mock = new ContractMock(
                contract, ContractMock.Behavior.COMMIT_THEN_TRANSIENT)) {
            String response = client(mock, pacing, "retry-secret")
                    .createOrReplaceInfraSegment("payments-blue-primary", desired);
            contains(response, "\"display_name\":\"Payments \\\"blue\\\"\\nreconcile\"",
                    "reconciled response retains requested display name");
            equal(List.of(), pacing, "confirmed commit must not pace a retry");
            equal(1, mock.creationEffects("payments-blue-primary"), "one creation effect");
            equal(1, mock.attempts("payments-blue-primary"), "one PUT attempt");

            List<ContractMock.LoggedRequest> requests = mock.requests();
            equal(2, requests.size(), "commit reconciliation request count");
            assertPut(requests.get(0), "/policy/api/v1/infra/segments/payments-blue-primary",
                    expectedBody, authorization, "initial PUT");
            assertGet(requests.get(1), "/policy/api/v1/infra/segments/payments-blue-primary",
                    authorization, "reconciliation GET");
        }
    }

    private static void verifyAbsentOutcomeRetries(
            Path contract,
            NsxPolicyClient.SegmentSpec desired,
            String expectedBody,
            String authorization) throws Exception {
        List<Integer> pacing = new ArrayList<>();
        try (ContractMock mock = new ContractMock(
                contract, ContractMock.Behavior.TRANSIENT_THEN_NOT_FOUND)) {
            String response = client(mock, pacing, "retry-secret")
                    .createOrReplaceInfraSegment("payments-blue-retry", desired);
            equal(expectedBody, response, "successful retried response body");
            equal(List.of(1), pacing, "one paced retry only after 404");
            equal(1, mock.creationEffects("payments-blue-retry"), "one creation effect");
            equal(2, mock.attempts("payments-blue-retry"), "two PUT attempts");

            List<ContractMock.LoggedRequest> requests = mock.requests();
            equal(3, requests.size(), "absent reconciliation request count");
            assertPut(requests.get(0), "/policy/api/v1/infra/segments/payments-blue-retry",
                    expectedBody, authorization, "initial PUT");
            assertGet(requests.get(1), "/policy/api/v1/infra/segments/payments-blue-retry",
                    authorization, "404 reconciliation GET");
            assertPut(requests.get(2), "/policy/api/v1/infra/segments/payments-blue-retry",
                    expectedBody, authorization, "identical retry PUT");
            equal(requests.get(0).bodyUtf8(), requests.get(2).bodyUtf8(),
                    "retry body must be byte-identical");
        }
    }

    private static void verifyMismatchedOutcomeIsAmbiguous(
            Path contract, NsxPolicyClient.SegmentSpec desired) throws Exception {
        List<Integer> pacing = new ArrayList<>();
        try (ContractMock mock = new ContractMock(
                contract, ContractMock.Behavior.MISMATCH_AFTER_TRANSIENT)) {
            try {
                client(mock, pacing, "retry-secret")
                        .createOrReplaceInfraSegment("payments-blue-conflict", desired);
                throw new AssertionError("mismatching reconciliation must be ambiguous");
            } catch (NsxPolicyClient.AmbiguousMutationException expected) {
                equal(503, expected.putStatus(), "ambiguous PUT status");
                equal(200, expected.readStatus(), "ambiguous read status");
            }
            equal(List.of(), pacing, "ambiguous outcome must not retry");
            equal(2, mock.requests().size(), "ambiguous outcome request count");
            equal("GET", mock.requests().get(1).method(), "ambiguous outcome uses GET");
        }
    }

    private static void verifyEncodedSlashIsLiveFailure(
            Path contract, NsxPolicyClient.SegmentSpec desired) throws Exception {
        List<Integer> pacing = new ArrayList<>();
        try (ContractMock mock = new ContractMock(contract)) {
            try {
                client(mock, pacing, "retry-secret")
                        .createOrReplaceInfraSegment("payments/blue", desired);
                throw new AssertionError("encoded slash must fail");
            } catch (NsxPolicyClient.NsxPolicyException expected) {
                equal(400, expected.statusCode(), "encoded slash HTTP status");
                contains(expected.responseBody(), "\"error_code\":512",
                        "encoded slash live error code");
            }
            equal(List.of(), pacing, "encoded slash must not retry");
            equal(1, mock.requests().size(), "encoded slash request count");
            equal("/policy/api/v1/infra/segments/payments%2Fblue",
                    mock.requests().get(0).rawPath(), "encoded slash raw path");
        }
    }

    private static void verifyTerminalFailureDoesNotReconcile(
            Path contract, NsxPolicyClient.SegmentSpec desired) throws Exception {
        List<Integer> pacing = new ArrayList<>();
        try (ContractMock mock = new ContractMock(contract, ContractMock.Behavior.FORBIDDEN)) {
            try {
                client(mock, pacing, "do-not-leak-this-password")
                        .createOrReplaceInfraSegment("denied", desired);
                throw new AssertionError("403 must throw NsxPolicyException");
            } catch (NsxPolicyClient.NsxPolicyException expected) {
                equal(403, expected.statusCode(), "terminal status code");
                absent(expected.getMessage(), "do-not-leak-this-password",
                        "password in exception message");
                absent(expected.getMessage(), "Basic ",
                        "authorization in exception message");
            }
            equal(List.of(), pacing, "403 must not invoke retry pacer");
            equal(0, mock.resourceCount(), "403 must not mutate state");
            equal(1, mock.requests().size(), "403 must not be reconciled or retried");
        }
    }

    private static void assertPut(
            ContractMock.LoggedRequest request,
            String path,
            String body,
            String authorization,
            String label) {
        equal("PUT", request.method(), label + " method");
        equal(path, request.rawPath(), label + " raw path");
        equal(null, request.rawQuery(), label + " no query");
        equal("application/json", mediaType(request.firstHeader("Accept")),
                label + " Accept");
        equal("application/json", mediaType(request.firstHeader("Content-Type")),
                label + " Content-Type");
        equal(authorization, request.firstHeader("Authorization"), label + " Basic auth");
        equal(body, request.bodyUtf8(), label + " body");
        absent(request.bodyUtf8(), ":null", label + " JSON null");
    }

    private static void assertGet(
            ContractMock.LoggedRequest request,
            String path,
            String authorization,
            String label) {
        equal("GET", request.method(), label + " method");
        equal(path, request.rawPath(), label + " raw path");
        equal(null, request.rawQuery(), label + " no query");
        equal("application/json", mediaType(request.firstHeader("Accept")),
                label + " Accept");
        equal(null, request.firstHeader("Content-Type"), label + " no Content-Type");
        equal(authorization, request.firstHeader("Authorization"), label + " Basic auth");
        equal("", request.bodyUtf8(), label + " empty body");
    }

    private static String mediaType(String value) {
        if (value == null) {
            return null;
        }
        int separator = value.indexOf(';');
        return (separator < 0 ? value : value.substring(0, separator)).trim().toLowerCase();
    }

    private static void contains(String text, String needle, String label) {
        checks++;
        if (!text.contains(needle)) {
            throw new AssertionError(label + ": expected <" + needle + "> in <" + text + ">");
        }
    }

    private static void absent(String text, String needle, String label) {
        checks++;
        if (text.contains(needle)) {
            throw new AssertionError(label + " must be omitted, text was " + text);
        }
    }

    private static void equal(Object expected, Object actual, String label) {
        checks++;
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }
}
