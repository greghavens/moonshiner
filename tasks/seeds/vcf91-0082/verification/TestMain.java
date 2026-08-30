import java.net.http.HttpClient;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;

public class TestMain {
    private static int checks;

    public static void main(String[] args) throws Exception {
        Path contract = Path.of("docs", "contract.json");
        List<Integer> pacedRetries = new ArrayList<>();

        try (ContractMock mock = new ContractMock(contract)) {
            NsxPolicyClient client = new NsxPolicyClient(
                    mock.baseUrl() + "/",
                    "automation",
                    "retry-secret",
                    HttpClient.newBuilder().build(),
                    pacedRetries::add);

            NsxPolicyClient.SegmentSpec desired = new NsxPolicyClient.SegmentSpec(
                    "Payments \"blue\"\nretry",
                    null,
                    null,
                    null,
                    List.of(new NsxPolicyClient.SegmentSubnet("10.42.0.1/24", null)),
                    null);

            String response = client.createOrReplaceInfraSegment(
                    "payments blue/primary",
                    desired);

            String expectedBody =
                    "{\"display_name\":\"Payments \\\"blue\\\"\\nretry\","
                            + "\"subnets\":[{\"gateway_address\":\"10.42.0.1/24\"}]}";

            equal(expectedBody, response, "successful response body");
            equal(List.of(1), pacedRetries, "one paced retry");
            equal(1, mock.resourceCount(), "one resource after retry");
            equal(1, mock.creationEffects("payments blue/primary"),
                    "retry must not duplicate the create effect");
            equal(2, mock.attempts("payments blue/primary"), "two PUT attempts");
            equal(expectedBody, mock.storedBody("payments blue/primary"),
                    "stored replacement body");

            List<ContractMock.LoggedRequest> requests = mock.requests();
            equal(2, requests.size(), "request count");
            for (int index = 0; index < requests.size(); index++) {
                ContractMock.LoggedRequest request = requests.get(index);
                equal("PUT", request.method(), "method attempt " + (index + 1));
                equal("/policy/api/v1/infra/segments/payments%20blue%2Fprimary",
                        request.rawPath(), "raw path attempt " + (index + 1));
                equal(null, request.rawQuery(), "no query attempt " + (index + 1));
                equal("application/json", mediaType(request.firstHeader("Accept")),
                        "accept attempt " + (index + 1));
                equal("application/json", mediaType(request.firstHeader("Content-Type")),
                        "content type attempt " + (index + 1));
                equal("Basic " + Base64.getEncoder().encodeToString(
                                "automation:retry-secret".getBytes(java.nio.charset.StandardCharsets.UTF_8)),
                        request.firstHeader("Authorization"),
                        "basic authorization attempt " + (index + 1));
                equal(expectedBody, request.bodyUtf8(),
                        "byte-exact body attempt " + (index + 1));
                absent(request.bodyUtf8(), "\"description\"", "unset description");
                absent(request.bodyUtf8(), "\"connectivity_path\"", "unset connectivity path");
                absent(request.bodyUtf8(), "\"transport_zone_path\"", "unset transport zone");
                absent(request.bodyUtf8(), "\"tags\"", "unset tags");
                absent(request.bodyUtf8(), "\"dhcp_ranges\"", "unset nested DHCP ranges");
                absent(request.bodyUtf8(), ":null", "JSON null");
                absent(request.bodyUtf8(), ":\"\"", "empty string substitute");
                absent(request.bodyUtf8(), ":[]", "empty array substitute");
            }

            equal(requests.get(0).rawPath(), requests.get(1).rawPath(),
                    "retry URI must be identical");
            equal(requests.get(0).bodyUtf8(), requests.get(1).bodyUtf8(),
                    "retry body must be identical");
        }

        List<Integer> terminalPacing = new ArrayList<>();
        try (ContractMock mock = new ContractMock(contract, ContractMock.Behavior.FORBIDDEN)) {
            NsxPolicyClient client = new NsxPolicyClient(
                    mock.baseUrl(),
                    "automation",
                    "do-not-leak-this-password",
                    HttpClient.newHttpClient(),
                    terminalPacing::add);
            NsxPolicyClient.SegmentSpec desired = new NsxPolicyClient.SegmentSpec(
                    "Denied segment",
                    "must not retry",
                    "/infra/tier-1s/app-tier",
                    "/infra/sites/default/enforcement-points/default/transport-zones/overlay",
                    List.of(new NsxPolicyClient.SegmentSubnet(
                            "10.50.0.1/24",
                            List.of("10.50.0.10-10.50.0.20"))),
                    List.of(
                            new NsxPolicyClient.Tag("environment", "test"),
                            new NsxPolicyClient.Tag(null, "payments")));

            String expectedBody =
                    "{\"display_name\":\"Denied segment\","
                            + "\"description\":\"must not retry\","
                            + "\"connectivity_path\":\"/infra/tier-1s/app-tier\","
                            + "\"transport_zone_path\":"
                            + "\"/infra/sites/default/enforcement-points/default/transport-zones/overlay\","
                            + "\"subnets\":[{\"gateway_address\":\"10.50.0.1/24\","
                            + "\"dhcp_ranges\":[\"10.50.0.10-10.50.0.20\"]}],"
                            + "\"tags\":[{\"scope\":\"environment\",\"tag\":\"test\"},{\"tag\":\"payments\"}]}";

            try {
                client.createOrReplaceInfraSegment("denied", desired);
                throw new AssertionError("403 must throw NsxPolicyException");
            } catch (NsxPolicyClient.NsxPolicyException expected) {
                equal(403, expected.statusCode(), "terminal status code");
                equal("{\"error_code\":403001,\"error_message\":\"forbidden by policy\"}",
                        expected.responseBody(), "terminal response body");
                absent(expected.getMessage(), "do-not-leak-this-password",
                        "password in exception message");
                absent(expected.getMessage(), "Basic ", "authorization in exception message");
            }
            equal(List.of(), terminalPacing, "403 must not invoke retry pacer");
            equal(0, mock.resourceCount(), "403 must not mutate state");
            equal(1, mock.requests().size(), "403 must not be retried");
            equal(expectedBody, mock.requests().get(0).bodyUtf8(),
                    "all supported optional fields");
        }

        System.out.println("ALL NSX POLICY CONTRACT CHECKS PASSED (" + checks + " checks)");
    }

    private static String mediaType(String value) {
        if (value == null) {
            return null;
        }
        int separator = value.indexOf(';');
        return (separator < 0 ? value : value.substring(0, separator)).trim().toLowerCase();
    }

    private static void absent(String text, String needle, String label) {
        checks++;
        if (text.contains(needle)) {
            throw new AssertionError(label + " must be omitted, body was " + text);
        }
    }

    private static void equal(Object expected, Object actual, String label) {
        checks++;
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }
}
