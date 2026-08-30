import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

/** Deterministic contract harness. It contacts only a loopback HttpServer. */
public final class TestMain {
    private static final String SUBMIT_PATH = "/deployment/api/deployments/deployment-42/requests";
    private static final String REQUEST_PATH = "/deployment/api/requests/request-7";
    private static final String TOKEN_PATH = "/csp/gateway/am/api/auth/token";

    public static void main(String[] args) throws Exception {
        try (MockVcfAutomationServer mock = new MockVcfAutomationServer()) {
            mock.start();
            AutomationClient client = new AutomationClient(
                    mock.baseUri(),
                    mock.initialAccessToken(),
                    mock.initialRefreshToken(),
                    mock.clientId(),
                    mock.clientSecret(),
                    Duration.ZERO);

            AutomationClient.OperationResult result = client.runDeploymentAction(
                    "deployment-42", "PowerOff", "Quarterly \"maintenance\"\nwindow");

            // This assertion is deliberately independent of the returned status. It makes
            // verification fail if a client reports a terminal state before the operation
            // in the mock has actually reached a terminal state.
            if (isTerminal(result.status()) && !mock.operationReachedTerminal()) {
                throw new AssertionError("client reported terminal state " + result.status()
                        + " before the operation reached a terminal state");
            }
            check(mock.operationReachedTerminal(), "operation never reached a terminal state");
            check("request-7".equals(result.requestId()), "wrong request id: " + result.requestId());
            check("SUCCESSFUL".equals(result.status()), "wrong final status: " + result.status());

            verifyRequestLog(mock.requestLog());

            verifyPollingErrorPropagation(MockVcfAutomationServer.Mode.POLL_HTTP_ERROR, "HTTP 503");
            verifyPollingErrorPropagation(
                    MockVcfAutomationServer.Mode.POLL_MALFORMED,
                    "missing required JSON string field: status");
            System.out.println("PASS: " + result);
        }
    }

    private static void verifyRequestLog(List<MockVcfAutomationServer.LogEntry> log) {
        List<MockVcfAutomationServer.LogEntry> submits = matching(log, "POST", SUBMIT_PATH);
        List<MockVcfAutomationServer.LogEntry> polls = matching(log, "GET", REQUEST_PATH);
        List<MockVcfAutomationServer.LogEntry> refreshes = matching(log, "POST", TOKEN_PATH);

        check(submits.size() == 1, "action must be submitted exactly once, log=" + log);
        check(refreshes.size() == 2,
                "both expired access tokens must be refreshed, log=" + log);
        check(polls.size() == 5,
                "each interrupted poll must be retried against the same request, log=" + log);
        check(log.size() == 8, "mock received an operation outside the pinned scenario, log=" + log);

        check(submits.get(0).body.contains("\"Quarterly \\\"maintenance\\\"\\nwindow\""),
                "deployment action JSON was not escaped correctly: " + submits.get(0).body);
        check("application/x-www-form-urlencoded".equals(refreshes.get(0).contentType),
                "refresh must use form encoding");
        check(refreshes.get(0).body.contains("grant_type=refresh_token"),
                "refresh grant_type is missing");
        check(refreshes.get(1).body.contains(
                        "refresh_token=refresh+after%2Brotation%26step%3D2"),
                "rotated refresh token was not retained and form-encoded for the next exchange");

        List<Integer> pollStatuses = polls.stream().map(entry -> entry.responseStatus)
                .collect(Collectors.toList());
        check(pollStatuses.equals(Arrays.asList(200, 401, 200, 401, 200)),
                "unexpected poll response sequence: " + pollStatuses);
        check(polls.stream().allMatch(entry -> REQUEST_PATH.equals(entry.path)),
                "polling did not preserve the original request id");
        check(!polls.get(2).terminalAfterResponse,
                "COMPLETION observation must occur before the terminal response");
        check(polls.get(4).terminalAfterResponse,
                "last poll did not observe the server's terminal state");
    }

    private static void verifyPollingErrorPropagation(
            MockVcfAutomationServer.Mode mode, String expectedMessage) throws Exception {
        try (MockVcfAutomationServer mock = new MockVcfAutomationServer(mode)) {
            mock.start();
            AutomationClient client = new AutomationClient(
                    mock.baseUri(),
                    mock.initialAccessToken(),
                    mock.initialRefreshToken(),
                    mock.clientId(),
                    mock.clientSecret(),
                    Duration.ZERO);
            try {
                client.runDeploymentAction("deployment-42", "PowerOff", "error contract check");
                throw new AssertionError("client accepted polling response for mode " + mode);
            } catch (IOException expected) {
                check(expected.getMessage() != null
                                && expected.getMessage().contains(expectedMessage),
                        "wrong error for " + mode + ": " + expected);
            }
        }
    }

    private static List<MockVcfAutomationServer.LogEntry> matching(
            List<MockVcfAutomationServer.LogEntry> log, String method, String path) {
        List<MockVcfAutomationServer.LogEntry> matches = new ArrayList<>();
        for (MockVcfAutomationServer.LogEntry entry : log) {
            if (method.equals(entry.method) && path.equals(entry.path)) {
                matches.add(entry);
            }
        }
        return matches;
    }

    private static boolean isTerminal(String status) {
        return "SUCCESSFUL".equals(status)
                || "FAILED".equals(status)
                || "ABORTED".equals(status)
                || "APPROVAL_REJECTED".equals(status);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
