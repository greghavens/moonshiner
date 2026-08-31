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
                    "usage: TestMain <loopback-base-uri> <access-token> <service-key> <service-address>");
        }

        VcfDepotClient client = new VcfDepotClient(
                URI.create(args[0]),
                args[1],
                Duration.ofSeconds(3));

        VcfDepotClient.ServiceConfig service = new VcfDepotClient.ServiceConfig(
                "VCF Depot",
                "VCF_DEPOT",
                args[2],
                "VCF Depot",
                "Fqdn",
                args[3]);
        client.updateServicesConfig(service);

        expectIllegalArgument(
                () -> client.updateServicesConfig(null),
                "a null service must be rejected before traffic");
        expectIllegalArgument(
                () -> client.updateServicesConfig(new VcfDepotClient.ServiceConfig(
                        null, "VCF_DEPOT", args[2], "VCF Depot", "Fqdn", args[3])),
                "a missing service name must be rejected before traffic");
        expectIllegalArgument(
                () -> client.updateServicesConfig(new VcfDepotClient.ServiceConfig(
                        "VCF Depot", "VCF_DEPOT", null, "VCF Depot", "Fqdn", args[3])),
                "a missing service key must be rejected before traffic");
        expectIllegalArgument(
                () -> client.updateServicesConfig(new VcfDepotClient.ServiceConfig(
                        "VCF Depot", "VCF_DEPOT", args[2], "VCF Depot", "Fqdn", null)),
                "a missing address value must be rejected before traffic");

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
