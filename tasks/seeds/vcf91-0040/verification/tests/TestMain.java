import java.net.URI;
import java.time.Duration;

/**
 * Fixed integration harness. The Python verifier supplies runtime-generated
 * values and a loopback URL.
 */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <access-token> <username> <password>");
        }

        VcfDepotClient client = new VcfDepotClient(
                URI.create(args[0]),
                args[1],
                Duration.ofSeconds(3));

        VcfDepotClient.DepotAccount account = new VcfDepotClient.DepotAccount(
                args[2],
                args[3],
                null,
                null,
                null,
                null);
        client.updateDepotSettings(account);

        expectIllegalArgument(
                () -> client.updateDepotSettings(null),
                "a null account must be rejected before traffic");
        expectIllegalArgument(
                () -> client.updateDepotSettings(new VcfDepotClient.DepotAccount(
                        null, args[3], null, null, null, null)),
                "a missing username must be rejected before traffic");
        expectIllegalArgument(
                () -> client.updateDepotSettings(new VcfDepotClient.DepotAccount(
                        args[2], null, null, null, null, null)),
                "a missing password must be rejected before traffic");

        System.out.println("UPDATED");
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
