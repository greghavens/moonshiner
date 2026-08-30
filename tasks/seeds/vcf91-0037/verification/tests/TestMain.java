import java.net.URI;
import java.time.Duration;

/**
 * Fixed integration harness. The Python verifier supplies a loopback URL.
 */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <access-token> <task-id>");
        }

        VcfBackupClient client = new VcfBackupClient(
                URI.create(args[0]),
                args[1],
                Duration.ofSeconds(3));

        VcfBackupClient.BackupLocation location =
                new VcfBackupClient.BackupLocation(
                        "backup01.lab.example",
                        22,
                        "SFTP",
                        "svc-vcf-\"backup\"",
                        "/exports/vcf\\nightly",
                        null,
                        null);

        VcfBackupClient.Task result = client.updateBackupConfigurationAndWait(
                location,
                5,
                Duration.ofMillis(10));

        require(args[2].equals(result.id()), "unexpected task id: " + result.id());
        require("SUCCESSFUL".equals(result.status()), "task was not polled to success: " + result.status());
        require(result.completionTimestamp() != null, "terminal response was not returned");

        expectIllegalArgument(
                () -> client.updateBackupConfigurationAndWait(
                        location, 0, Duration.ZERO),
                "maxPolls must be validated before traffic");
        expectIllegalArgument(
                () -> client.updateBackupConfigurationAndWait(
                        location, 1, Duration.ofMillis(-1)),
                "negative pollInterval must be validated before traffic");
        System.out.println("SUCCESSFUL");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void expectIllegalArgument(ThrowingAction action, String message)
            throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
