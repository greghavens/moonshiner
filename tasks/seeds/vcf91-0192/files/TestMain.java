import java.net.URI;

/** Fixed harness invoked by the protected verifier. */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain BASE_URI");
        }

        VcfLogForwarderClient client = new VcfLogForwarderClient(
                URI.create(args[0]), "test-jwt-token");

        VcfLogForwarderClient.LogForwarder update =
                new VcfLogForwarderClient.LogForwarder(
                        null,
                        null,
                        null,
                        true,
                        false,
                        "logs.example.test",
                        "Primary \"audit\" relay",
                        6514,
                        "SYSLOG",
                        true,
                        null,
                        "TCP",
                        null);

        VcfLogForwarderClient.UpdateResult result =
                client.updateLogForwarder("forwarder 01/blue", update);

        require(result.statusCode() == 200, "expected final HTTP 200");
        require(result.body().contains("\"id\":\"forwarder 01/blue\""),
                "response must contain the addressed resource id");
        require(result.body().contains("\"forwardComplementaryFields\":false"),
                "response must preserve explicitly false fields");
        System.out.println("TEST_MAIN_OK");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
