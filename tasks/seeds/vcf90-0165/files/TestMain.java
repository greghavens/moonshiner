import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Protected, dependency-free acceptance harness. */
public final class TestMain {
    private static final String REFRESH_TOKEN = "refresh-token-vcf90";

    public static void main(String[] args) throws Exception {
        testRefreshAndOmittedDescriptions();
        testDescriptionsAndJsonEscaping();
        testFailureHandling();
        System.out.println("PASS: VCF Automation wire contract and token refresh behavior");
    }

    private static void testRefreshAndOmittedDescriptions() throws Exception {
        try (ContractMockServer mock = ContractMockServer.start(Path.of("docs/contract.json"))) {
            AutomationClient client = new AutomationClient(mock.baseUri(), REFRESH_TOKEN);
            AutomationClient.ProvisioningResult result = client.provision(
                    "seed-project", "", "seed-deployment", null);

            equal(mock.projectId(), result.projectId(), "project ID must come from the response");
            equal(mock.deploymentId(), result.deploymentId(),
                    "deployment ID must come from the response");
            equal(2, mock.loginCalls(), "initial authentication plus refresh");
            equal(1, mock.projectCreates(), "successful project work must not be repeated");
            equal(1, mock.deploymentCreates(), "deployment must be created once");
            equal(0, mock.unexpectedRequests(), "mock must receive only contract operations");

            List<ContractMockServer.LoggedRequest> requests = mock.requestLog();
            equal(5, requests.size(), "exact request count");

            assertWire(requests.get(0), "/iaas/api/login",
                    Map.of("refreshToken", REFRESH_TOKEN), null);
            assertWire(requests.get(1), "/iaas/api/projects",
                    Map.of("name", "seed-project"), "Bearer " + mock.initialToken());
            Map<String, String> deploymentBody = Map.of(
                    "name", "seed-deployment", "projectId", mock.projectId());
            assertWire(requests.get(2), "/iaas/api/deployments",
                    deploymentBody, "Bearer " + mock.initialToken());
            assertWire(requests.get(3), "/iaas/api/login",
                    Map.of("refreshToken", REFRESH_TOKEN), null);
            assertWire(requests.get(4), "/iaas/api/deployments",
                    deploymentBody, "Bearer " + mock.refreshedToken());

            equal(requests.get(2).body(), requests.get(4).body(),
                    "401 retry must preserve the exact deployment body");
        }
    }

    private static void testDescriptionsAndJsonEscaping() throws Exception {
        String projectName = "project \"quoted\" \\ path\nline ☺";
        String projectDescription = "project description\twith controls";
        String deploymentName = "deployment λ";
        String deploymentDescription = "deployment \"description\" \\ value";

        try (ContractMockServer mock = ContractMockServer.start(Path.of("docs/contract.json"))) {
            AutomationClient client = new AutomationClient(mock.baseUri(), REFRESH_TOKEN);
            AutomationClient.ProvisioningResult result = client.provision(
                    projectName, projectDescription, deploymentName, deploymentDescription);

            equal(mock.projectId(), result.projectId(), "escaped-input run project ID");
            equal(mock.deploymentId(), result.deploymentId(), "escaped-input run deployment ID");
            List<ContractMockServer.LoggedRequest> requests = mock.requestLog();
            equal(5, requests.size(), "escaped-input request count");
            assertWire(requests.get(1), "/iaas/api/projects",
                    Map.of("name", projectName, "description", projectDescription),
                    "Bearer " + mock.initialToken());
            Map<String, String> deploymentBody = Map.of(
                    "name", deploymentName,
                    "description", deploymentDescription,
                    "projectId", mock.projectId());
            assertWire(requests.get(2), "/iaas/api/deployments",
                    deploymentBody, "Bearer " + mock.initialToken());
            assertWire(requests.get(4), "/iaas/api/deployments",
                    deploymentBody, "Bearer " + mock.refreshedToken());
            equal(requests.get(2).body(), requests.get(4).body(),
                    "escaped 401 retry must preserve the exact deployment body");
        }
    }

    private static void testFailureHandling() throws Exception {
        assertProvisionFails(ContractMockServer.Scenario.INITIAL_LOGIN_FAILURE,
                1, 1, 0, "initial authentication failure");
        assertProvisionFails(ContractMockServer.Scenario.PROJECT_FAILURE,
                2, 1, 0, "project creation failure");
        assertProvisionFails(ContractMockServer.Scenario.REFRESH_FAILURE,
                4, 2, 1, "token refresh failure");
        assertProvisionFails(ContractMockServer.Scenario.RETRY_FAILURE,
                5, 2, 1, "refreshed deployment retry failure");
    }

    private static void assertProvisionFails(
            ContractMockServer.Scenario scenario,
            int expectedRequests,
            int expectedLoginCalls,
            int expectedProjectCreates,
            String label) throws Exception {
        try (ContractMockServer mock = ContractMockServer.start(
                Path.of("docs/contract.json"), scenario)) {
            AutomationClient client = new AutomationClient(mock.baseUri(), REFRESH_TOKEN);
            boolean failed = false;
            try {
                client.provision("failure-project", null, "failure-deployment", null);
            } catch (Exception expected) {
                failed = true;
            }
            check(failed, label + " must be surfaced");
            equal(expectedRequests, mock.requestLog().size(), label + " request count");
            equal(expectedLoginCalls, mock.loginCalls(), label + " login count");
            equal(expectedProjectCreates, mock.projectCreates(), label + " project count");
            equal(0, mock.deploymentCreates(), label + " must not create a deployment");
            equal(0, mock.unexpectedRequests(), label + " must not issue unrelated operations");
        }
    }

    private static void assertWire(
            ContractMockServer.LoggedRequest request,
            String expectedPath,
            Map<String, String> expectedBody,
            String expectedAuthorization) {
        equal("POST", request.method(), "HTTP method for " + expectedPath);
        equal(expectedPath, request.rawPath(), "raw path");
        equal(null, request.rawQuery(), "unset optional query parameters must be omitted");
        equal(List.of("application/json"), headerValues(request, "Accept"), "Accept header");
        equal(List.of("application/json"), headerValues(request, "Content-Type"),
                "Content-Type header");
        equal(expectedAuthorization == null ? List.of() : List.of(expectedAuthorization),
                headerValues(request, "Authorization"), "Authorization header");
        equal(expectedBody, parseStringObject(request.body()),
                "JSON request body for " + expectedPath);
    }

    private static List<String> headerValues(
            ContractMockServer.LoggedRequest request, String name) {
        for (var entry : request.headers().entrySet()) {
            if (entry.getKey().equalsIgnoreCase(name)) {
                return entry.getValue();
            }
        }
        return List.of();
    }

    /** Parses the flat string-valued request objects without imposing JSON member order. */
    private static Map<String, String> parseStringObject(String json) {
        int[] cursor = {0};
        skipWhitespace(json, cursor);
        expect(json, cursor, '{');
        Map<String, String> values = new LinkedHashMap<>();
        skipWhitespace(json, cursor);
        if (take(json, cursor, '}')) {
            requireEnd(json, cursor);
            return values;
        }
        while (true) {
            String name = readString(json, cursor);
            skipWhitespace(json, cursor);
            expect(json, cursor, ':');
            skipWhitespace(json, cursor);
            String value = readString(json, cursor);
            if (values.put(name, value) != null) {
                throw new AssertionError("duplicate JSON member: " + name);
            }
            skipWhitespace(json, cursor);
            if (take(json, cursor, '}')) {
                requireEnd(json, cursor);
                return values;
            }
            expect(json, cursor, ',');
            skipWhitespace(json, cursor);
        }
    }

    private static String readString(String json, int[] cursor) {
        expect(json, cursor, '"');
        StringBuilder value = new StringBuilder();
        while (cursor[0] < json.length()) {
            char current = json.charAt(cursor[0]++);
            if (current == '"') {
                return value.toString();
            }
            if (current == '\\') {
                if (cursor[0] >= json.length()) {
                    break;
                }
                char escaped = json.charAt(cursor[0]++);
                switch (escaped) {
                    case '"', '\\', '/' -> value.append(escaped);
                    case 'b' -> value.append('\b');
                    case 'f' -> value.append('\f');
                    case 'n' -> value.append('\n');
                    case 'r' -> value.append('\r');
                    case 't' -> value.append('\t');
                    case 'u' -> {
                        if (cursor[0] + 4 > json.length()) {
                            throw new AssertionError("short JSON unicode escape: " + json);
                        }
                        try {
                            value.append((char) Integer.parseInt(
                                    json.substring(cursor[0], cursor[0] + 4), 16));
                        } catch (NumberFormatException error) {
                            throw new AssertionError("invalid JSON unicode escape: " + json, error);
                        }
                        cursor[0] += 4;
                    }
                    default -> throw new AssertionError("invalid JSON escape: \\" + escaped);
                }
            } else {
                if (current < 0x20) {
                    throw new AssertionError("unescaped control character in JSON string");
                }
                value.append(current);
            }
        }
        throw new AssertionError("unterminated JSON string: " + json);
    }

    private static void skipWhitespace(String json, int[] cursor) {
        while (cursor[0] < json.length() && Character.isWhitespace(json.charAt(cursor[0]))) {
            cursor[0]++;
        }
    }

    private static void expect(String json, int[] cursor, char expected) {
        if (!take(json, cursor, expected)) {
            throw new AssertionError("expected '" + expected + "' in JSON: " + json);
        }
    }

    private static boolean take(String json, int[] cursor, char expected) {
        if (cursor[0] < json.length() && json.charAt(cursor[0]) == expected) {
            cursor[0]++;
            return true;
        }
        return false;
    }

    private static void requireEnd(String json, int[] cursor) {
        skipWhitespace(json, cursor);
        if (cursor[0] != json.length()) {
            throw new AssertionError("trailing content after JSON object: " + json);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }
}
