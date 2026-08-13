import java.nio.file.Path;
import java.time.Duration;
import java.util.List;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        try (MockVcfOperationsServer mock = new MockVcfOperationsServer(Path.of("docs/contract.json"))) {
            VcfOperationsClient client = new VcfOperationsClient(
                    mock.baseUri(), MockVcfOperationsServer.AUTHORIZATION, Duration.ZERO);

            VcfOperationsClient.ActionStatus terminal = client.performActionAndWait(
                    MockVcfOperationsServer.ACTION_ID, MockVcfOperationsServer.RESOURCE_ID);

            equal(MockVcfOperationsServer.TASK_ID, terminal.taskId(), "returned task id");
            equal("Completed", terminal.state(), "returned terminal state");

            List<MockVcfOperationsServer.LoggedRequest> log = mock.requestLog();
            equal(4, log.size(), "one POST followed by all three status polls");

            MockVcfOperationsServer.LoggedRequest post = log.get(0);
            equal("POST", post.method(), "performAction method");
            equal("/suite-api/api/actions/VMWARE-Power%20Off%20VM", post.rawPath(),
                    "performAction encoded path");
            equal(null, post.rawQuery(), "performAction query");
            equal("{\"parameterGroup\":[{\"resourceId\":\"7e780215-da07-4da1-9167-cd6892dcfdd8\"}]}",
                    post.body(), "performAction exact JSON body");
            onlyHeader(post, "Authorization", "OpsToken fixture-token");
            onlyHeader(post, "Accept", "application/json");
            onlyHeader(post, "Content-Type", "application/json");
            absentFromBody(post.body(), "contextId");
            absentFromBody(post.body(), "contextResourceId");
            absentFromBody(post.body(), "parameterValue");

            String expectedStatusPath = "/suite-api/api/actions/"
                    + MockVcfOperationsServer.TASK_ID + "/status";
            for (int index = 1; index < log.size(); index++) {
                MockVcfOperationsServer.LoggedRequest poll = log.get(index);
                equal("GET", poll.method(), "getActionStatus method at request " + index);
                equal(expectedStatusPath, poll.rawPath(), "getActionStatus path at request " + index);
                equal(null, poll.rawQuery(), "unset detail query must be omitted at request " + index);
                equal("", poll.body(), "getActionStatus body at request " + index);
                onlyHeader(poll, "Authorization", "OpsToken fixture-token");
                onlyHeader(poll, "Accept", "application/json");
                equal(List.of(), poll.header("Content-Type"), "GET Content-Type must be absent");
            }
        }

        System.out.println("PASS: VCF Operations action client matches the pinned wire contract");
    }

    private static void onlyHeader(
            MockVcfOperationsServer.LoggedRequest request, String name, String expected) {
        equal(List.of(expected), request.header(name), name + " header");
    }

    private static void absentFromBody(String body, String field) {
        check(!body.contains("\"" + field + "\""), "optional field must be omitted: " + field);
    }

    private static void equal(Object expected, Object actual, String label) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
