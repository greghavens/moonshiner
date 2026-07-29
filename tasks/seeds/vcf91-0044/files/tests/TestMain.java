import java.net.URI;
import java.time.Duration;

public final class TestMain {
    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 6) {
            throw new IllegalArgumentException("expected six harness arguments");
        }

        VcfFailureDiagnosticsClient client = new VcfFailureDiagnosticsClient(
                URI.create(args[0]),
                args[1],
                3,
                Duration.ofSeconds(4));

        VcfFailureDiagnosticsClient.Diagnosis diagnosis =
                client.diagnoseTaskFailure(args[2]);

        check(diagnosis.taskId().equals(args[2]), "task id was not preserved");
        check(diagnosis.eventId().equals(args[3]), "wrong correlated event");
        check(diagnosis.cause().equals(args[4]), "wrong correlated log cause");
        check(diagnosis.bundleId().equals(args[5]), "wrong support bundle");
        check(
                diagnosis.evidencePath().equals("logs/api/vcf-api.log"),
                "wrong evidence path");
        check(diagnosis.relevantEvents().size() == 1, "events were not resource-filtered");
        check(
                diagnosis.relevantEvents().get(0).id().equals(args[3]),
                "relevant event order changed");

        try {
            diagnosis.relevantEvents().clear();
            throw new AssertionError("diagnosis exposed a mutable event list");
        } catch (UnsupportedOperationException expected) {
            // required defensive view
        }
        try {
            diagnosis.relevantEvents().get(0).resourceIds().clear();
            throw new AssertionError("event exposed a mutable resource list");
        } catch (UnsupportedOperationException expected) {
            // required defensive view
        }

        System.out.println("SUCCESSFUL");
    }
}
