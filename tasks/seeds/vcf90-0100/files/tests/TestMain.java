import java.net.URI;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class TestMain {
    private static final String FIRST_ID = "11111111-1111-4111-8111-111111111111";
    private static final String SECOND_ID = "22222222-2222-4222-8222-222222222222";
    private static final String THIRD_ID = "33333333-3333-4333-8333-333333333333";

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new AssertionError("expected appliance base URI argument");
        }

        VcfLogsClient client = new VcfLogsClient(
                URI.create(args[0]),
                "automation@example.com",
                "s3cr\"et\\line\nnext",
                "Local");

        VcfLogsClient.ForwarderPatch firstPatch = new VcfLogsClient.ForwarderPatch(
                "checkout \"primary\"\nwest",
                null,
                null,
                null,
                null,
                6,
                0,
                Map.of(),
                "app=checkout",
                null,
                Boolean.FALSE,
                null);

        Map<String, String> tags = new LinkedHashMap<>();
        tags.put("site\"code", "dr\\east\nline");
        VcfLogsClient.ForwarderPatch secondPatch = new VcfLogsClient.ForwarderPatch(
                null,
                "logs-dr.example.com",
                1514,
                "SYSLOG",
                Boolean.TRUE,
                null,
                null,
                tags,
                null,
                "TCP",
                null,
                Boolean.FALSE);

        VcfLogsClient.ForwarderPatch emptyPatch = new VcfLogsClient.ForwarderPatch(
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null);

        VcfLogsClient.ChangeReport report = client.applyForwarderChanges(List.of(
                new VcfLogsClient.ForwarderChange(FIRST_ID, firstPatch),
                new VcfLogsClient.ForwarderChange(SECOND_ID, secondPatch),
                new VcfLogsClient.ForwarderChange(THIRD_ID, emptyPatch)));

        require(report != null, "report must not be null");
        require(report.results() != null, "report results must not be null");
        require(report.results().size() == 3, "report must retain every attempted step");

        VcfLogsClient.ChangeResult first = report.results().get(0);
        require(FIRST_ID.equals(first.id()), "first result id/order changed");
        require(first.success(), "first change must remain successful");
        require(first.httpStatus() == 204, "first status must retain the non-200 2xx response");
        require(first.errorCode() == null, "successful result must not invent an error code");
        require(first.message() == null, "successful result must not invent an error message");

        VcfLogsClient.ChangeResult second = report.results().get(1);
        require(SECOND_ID.equals(second.id()), "second result id/order changed");
        require(!second.success(), "second change must be reported as failed");
        require(second.httpStatus() == 400, "second status must be 400");
        require("FIELD_ERROR".equals(second.errorCode()), "second errorCode was not parsed");
        require(
                "Forwarder \"dr\" is unreachable.".equals(second.message()),
                "second errorMessage was not parsed or unescaped");

        VcfLogsClient.ChangeResult third = report.results().get(2);
        require(THIRD_ID.equals(third.id()), "third result id/order changed");
        require(third.success(), "processing must continue after a per-change failure");
        require(third.httpStatus() == 200, "third status must be 200");
        require(third.errorCode() == null, "successful result must not invent an error code");
        require(third.message() == null, "successful result must not invent an error message");

        System.out.println("TEST_MAIN_OK");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
