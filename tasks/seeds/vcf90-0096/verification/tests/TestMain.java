import java.io.IOException;
import java.net.URI;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain SCENARIO BASE_URI MASTER_FQDN");
        }

        VcfOperationsForLogsClient client =
                new VcfOperationsForLogsClient(URI.create(args[1]));

        switch (args[0]) {
            case "success-retries" -> requireResult(
                    client.joinAndWait(args[2]),
                    "192.0.2.10", "192.0.2.11", 16520,
                    "worker-token-9-0", 443);
            case "success-escaped" -> requireResult(
                    client.joinAndWait(args[2]),
                    "primary \"alpha\" \\ path \u2603", "worker-\u03b2", 16521,
                    "token \\ slash \" quote", 8443);
            case "join-error", "wait-error" -> requireIOException(
                    () -> client.joinAndWait(args[2]), args[0]);
            default -> throw new IllegalArgumentException(
                    "unknown scenario: " + args[0]);
        }

        System.out.println("TestMain passed: " + args[0]);
    }

    private static void requireResult(
            VcfOperationsForLogsClient.JoinResult result,
            String masterAddress,
            String workerAddress,
            int workerPort,
            String workerToken,
            int masterUiPort) {
        require(masterAddress.equals(result.masterAddress()), "masterAddress");
        require(workerAddress.equals(result.workerAddress()), "workerAddress");
        require(result.workerPort() == workerPort, "workerPort");
        require(workerToken.equals(result.workerToken()), "workerToken");
        require(result.masterUiPort() == masterUiPort, "masterUiPort");
    }

    private static void requireIOException(IoAction action, String scenario)
            throws Exception {
        try {
            action.run();
        } catch (IOException expected) {
            return;
        }
        throw new AssertionError("expected IOException for " + scenario);
    }

    private static void require(boolean condition, String field) {
        if (!condition) {
            throw new AssertionError("unexpected " + field);
        }
    }

    @FunctionalInterface
    private interface IoAction {
        void run() throws Exception;
    }
}
