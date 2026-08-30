import java.net.URI;
import java.time.Duration;

/**
 * Fixed integration harness. The Python verifier supplies a loopback URL and
 * runtime-generated fixture values.
 */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 5) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <access-token>"
                            + " <blocked-name> <allowed-name> <provider-type>");
        }

        VcfIdentityProviderClient client = new VcfIdentityProviderClient(
                URI.create(args[0]),
                args[1],
                Duration.ofSeconds(3));

        expectIllegalArgument(
                () -> client.addExternalIdentityProviderIfSafe(
                        new VcfIdentityProviderClient.IdentityProviderSpec(
                                null, args[4], null),
                        null),
                "invalid provider must be rejected before traffic");

        boolean blocked = client.addExternalIdentityProviderIfSafe(
                new VcfIdentityProviderClient.IdentityProviderSpec(
                        args[2], args[4], null),
                null);
        require(!blocked, "FAILURE precheck must gate enrollment");

        boolean enrolled = client.addExternalIdentityProviderIfSafe(
                new VcfIdentityProviderClient.IdentityProviderSpec(
                        args[3], args[4], null),
                null);
        require(enrolled, "SUCCESS precheck must permit enrollment");

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
