import java.io.IOException;
import java.net.URI;
import java.util.List;

public final class TestMain {
    private static final String SUCCESS_VALIDATION_ID = "11111111-1111-4111-8111-111111111111";
    private static final String FAILURE_VALIDATION_ID = "22222222-2222-4222-8222-222222222222";
    private static final String TASK_ID = "33333333-3333-4333-8333-333333333333";

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain BASE_URL SCENARIO");
        }

        boolean optionals = args[1].startsWith("success-optionals");
        VcfInstallerClient.SddcSpec spec = optionals
                ? new VcfInstallerClient.SddcSpec(
                        "edge-02",
                        "vc02.edge.example",
                        "Opt!Pass\n\"\\2026",
                        List.of(
                                new VcfInstallerClient.NetworkSpec("MANAGEMENT", 0),
                                new VcfInstallerClient.NetworkSpec("VMOTION", 4094)),
                        "edge.example",
                        "VCF_COMPLETE",
                        "9.1.0",
                        "medium",
                        "large",
                        List.of("192.0.2.10", "ns\"\\\n.example"))
                : new VcfInstallerClient.SddcSpec(
                        "lab-01",
                        "vc01.lab.example",
                        "VCF!Pass\"\\2026",
                        List.of(new VcfInstallerClient.NetworkSpec("MANAGEMENT", 120)),
                        "lab.example",
                        null,
                        null,
                        null,
                        null,
                        null);

        Boolean skipValidations = null;
        if ("success-optionals-true".equals(args[1])) {
            skipValidations = true;
        } else if ("success-optionals-false".equals(args[1])) {
            skipValidations = false;
        }

        VcfInstallerClient client = new VcfInstallerClient(
                URI.create(args[0]), "verifier-token", 4);
        VcfInstallerClient.DeploymentOutcome outcome;
        try {
            outcome = client.precheckThenDeploy(spec, skipValidations);
        } catch (IOException expected) {
            if ("timeout".equals(args[1])) {
                System.out.println("TIMEOUT");
                return;
            }
            if ("http-error".equals(args[1])) {
                System.out.println("HTTP_ERROR");
                return;
            }
            if ("poll-error".equals(args[1])) {
                System.out.println("POLL_ERROR");
                return;
            }
            throw expected;
        }

        require(
                !"timeout".equals(args[1])
                        && !"http-error".equals(args[1])
                        && !"poll-error".equals(args[1]),
                "failure scenario unexpectedly returned an outcome");

        if ("success".equals(args[1])
                || args[1].startsWith("success-optionals")
                || "immediate-success".equals(args[1])) {
            require(outcome.deployed(), "successful precheck did not deploy");
            require(SUCCESS_VALIDATION_ID.equals(outcome.validationId()), "wrong validation id");
            require("SUCCEEDED".equals(outcome.validationResultStatus()), "wrong validation result");
            require(TASK_ID.equals(outcome.taskId()), "wrong task id");
            System.out.println("SUCCESS " + outcome.validationId() + " " + outcome.taskId());
        } else if ("failure".equals(args[1]) || "immediate-failure".equals(args[1])) {
            require(!outcome.deployed(), "failed precheck deployed");
            require(FAILURE_VALIDATION_ID.equals(outcome.validationId()), "wrong validation id");
            String expectedResult = "immediate-failure".equals(args[1]) ? "UNKNOWN" : "FAILED";
            require(expectedResult.equals(outcome.validationResultStatus()), "wrong validation result");
            require(outcome.taskId() == null, "failed precheck returned a task id");
            System.out.println("BLOCKED " + outcome.validationId());
        } else {
            throw new IllegalArgumentException("unknown scenario: " + args[1]);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
