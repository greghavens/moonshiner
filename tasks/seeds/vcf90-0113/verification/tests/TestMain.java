import java.io.IOException;
import java.net.URI;

/** Black-box harness for the focused VCF Installer client. */
public final class TestMain {
    private static final String BUNDLE_ID = "bundle alpha/9.0?#%";
    private static final String TASK_ID = "task alpha/9.0";
    private static final String ALTERNATE_TASK_ID = "alternate task/accepted?yes#100%";

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new AssertionError("expected base URI and scenario");
        }

        VcfInstallerClient client = new VcfInstallerClient(URI.create(args[0]));
        String scenario = args[1];
        if (expectsIOException(scenario)) {
            expectIOException(client, scenario);
        } else {
            verifyTerminalTask(client, scenario);
        }
        System.out.println("TEST_MAIN_OK");
    }

    private static void expectIOException(VcfInstallerClient client, String scenario)
            throws Exception {
        try {
            client.downloadBundleNowAndWait(BUNDLE_ID);
            throw new AssertionError(scenario + " should have failed with IOException");
        } catch (IOException expected) {
            // Required failure behavior.
        }
    }

    private static boolean expectsIOException(String scenario) {
        return switch (scenario) {
            case "unknown-status",
                    "accepted-unknown-status",
                    "start-http-error",
                    "start-malformed-json",
                    "start-non-object",
                    "start-missing-field",
                    "start-wrong-type",
                    "poll-http-error",
                    "poll-malformed-json",
                    "poll-non-object",
                    "poll-missing-field",
                    "poll-wrong-type" -> true;
            default -> false;
        };
    }

    private static void verifyTerminalTask(VcfInstallerClient client, String scenario)
            throws Exception {
        VcfInstallerClient.Task task = client.downloadBundleNowAndWait(BUNDLE_ID);
        String expectedTaskId = scenario.equals("accepted-terminal") ? ALTERNATE_TASK_ID : TASK_ID;
        String expectedStatus = switch (scenario) {
            case "success", "accepted-terminal" -> "SUCCESSFUL";
            case "mixed-case" -> "Successful";
            case "failed-upper" -> "FAILED";
            case "failed" -> "Failed";
            case "cancelled-upper" -> "CANCELLED";
            case "cancelled" -> "Cancelled";
            case "warning" -> "COMPLETED_WITH_WARNING";
            case "skipped" -> "SKIPPED";
            default -> throw new AssertionError("unknown scenario: " + scenario);
        };
        if (!expectedTaskId.equals(task.id())) {
            throw new AssertionError("wrong task id: " + task.id());
        }
        if (!"Download bundle".equals(task.name())) {
            throw new AssertionError("wrong task name: " + task.name());
        }
        if (!expectedStatus.equals(task.status())) {
            throw new AssertionError("wrong terminal status: " + task.status());
        }
        if (!"2026-08-13T12:00:00Z".equals(task.creationTimestamp())) {
            throw new AssertionError("wrong creation timestamp");
        }
    }
}
