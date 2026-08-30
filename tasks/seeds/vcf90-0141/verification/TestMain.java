import java.net.URI;
import java.time.Duration;

public final class TestMain {
    private static final String CERTIFICATE =
            "-----BEGIN CERTIFICATE-----\nfixture-cert\n-----END CERTIFICATE-----";
    private static final String PRIVATE_KEY =
            "-----BEGIN PRIVATE KEY-----\nfixture-key\n-----END PRIVATE KEY-----";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <api-base-uri>");
        }

        VcfNetworksClient client = new VcfNetworksClient(
                URI.create(args[0]), "fixture-token", Duration.ofMillis(5));
        VcfNetworksClient.CertificateUpdateStatus result =
                client.updateCertificateAndWait(
                        "platform cert/primary", CERTIFICATE, PRIVATE_KEY, null);

        require("update-42".equals(result.id()), "wrong update id: " + result.id());
        require("platform cert/primary".equals(result.name()), "wrong name: " + result.name());
        require("SUCCESS".equals(result.status()), "not terminal success: " + result.status());
        require(result.errorMessage() == null, "unexpected error: " + result.errorMessage());
        System.out.println("terminal=SUCCESS updateId=update-42");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
