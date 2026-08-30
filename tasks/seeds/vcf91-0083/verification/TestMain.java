import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

public final class TestMain {
    private static final String EXPECTED_REPORT =
            "{\"status\":\"partial_failure\",\"succeeded\":1,\"failed\":1,"
            + "\"steps\":[{\"name\":\"source-group\","
            + "\"operationId\":\"PatchGroupForDomain\",\"status\":\"succeeded\"},"
            + "{\"name\":\"security-policy\","
            + "\"operationId\":\"PatchSecurityPolicyForDomain\","
            + "\"status\":\"failed\",\"http_status\":503,\"error_code\":73001}]}\n";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new AssertionError("usage: TestMain BASE_URL REPORT_PATH");
        }

        Path reportPath = Path.of(args[1]);
        Path invalidReport = reportPath.resolveSibling("invalid-report.json");
        NsxPolicyClient client = new NsxPolicyClient(
                args[0], "contract-user", "s3cret-value");

        NsxPolicyClient.ChangePlan invalid = new NsxPolicyClient.ChangePlan(
                new NsxPolicyClient.GroupSpec(
                        "Source group", List.of("10.20.0.0/24"), null),
                new NsxPolicyClient.PolicySpec(
                        "Application policy",
                        "Allow app traffic",
                        "/infra/domains/default/groups/destination",
                        1_000_000,
                        10,
                        null,
                        null));
        boolean rejected = false;
        try {
            client.applyChange(
                    "prod east", "source+blue", "allow/edge", invalid, invalidReport);
        } catch (IllegalArgumentException expected) {
            rejected = true;
        }
        check(rejected, "the complete invalid plan was not rejected locally");
        check(!Files.exists(invalidReport), "validation created a report");

        NsxPolicyClient.ChangePlan plan = new NsxPolicyClient.ChangePlan(
                new NsxPolicyClient.GroupSpec(
                        "Source \"blue\"",
                        List.of("10.20.0.0/24", "2001:db8::/64"),
                        null),
                new NsxPolicyClient.PolicySpec(
                        "Application policy",
                        "Allow app\ntraffic",
                        "/infra/domains/default/groups/destination",
                        120,
                        10,
                        null,
                        null));

        NsxPolicyClient.ChangeReport report = client.applyChange(
                "prod east", "source+blue", "allow/edge", plan, reportPath);

        check("partial_failure".equals(report.status()), "wrong report status");
        check(report.succeeded() == 1, "wrong succeeded count");
        check(report.failed() == 1, "wrong failed count");
        check(report.steps().size() == 2, "wrong step count");
        checkStep(
                report.steps().get(0),
                "source-group",
                "PatchGroupForDomain",
                "succeeded",
                null,
                null);
        checkStep(
                report.steps().get(1),
                "security-policy",
                "PatchSecurityPolicyForDomain",
                "failed",
                503,
                73001L);

        boolean immutable = false;
        try {
            report.steps().add(report.steps().get(0));
        } catch (UnsupportedOperationException expected) {
            immutable = true;
        }
        check(immutable, "returned steps are mutable");

        byte[] actual = Files.readAllBytes(reportPath);
        check(
                java.util.Arrays.equals(
                        EXPECTED_REPORT.getBytes(StandardCharsets.UTF_8), actual),
                "report bytes differ from the deterministic contract");
        String serialized = new String(actual, StandardCharsets.UTF_8);
        check(!serialized.contains("contract-user"), "report leaked username");
        check(!serialized.contains("s3cret-value"), "report leaked password");

        System.out.println("ALL NSX POLICY CONTRACT CHECKS PASSED");
    }

    private static void checkStep(
            NsxPolicyClient.StepResult step,
            String name,
            String operationId,
            String status,
            Integer httpStatus,
            Long errorCode) {
        check(name.equals(step.name()), "wrong step name");
        check(operationId.equals(step.operationId()), "wrong operationId");
        check(status.equals(step.status()), "wrong step status");
        check(java.util.Objects.equals(httpStatus, step.httpStatus()),
                "wrong step HTTP status");
        check(java.util.Objects.equals(errorCode, step.errorCode()),
                "wrong step error code");
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
