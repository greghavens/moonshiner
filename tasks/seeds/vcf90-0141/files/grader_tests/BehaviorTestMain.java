import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.Set;
import java.util.concurrent.atomic.AtomicReference;

/** Additional protected behavioral harness for the public client API. */
public final class BehaviorTestMain {
    private static final String CERTIFICATE = "cert \"quoted\"\\line\n雪";
    private static final String PRIVATE_KEY = "key\tvalue\r\n\\end";
    private static final String CHAIN = "chain\nvalue";
    private static final Set<String> IO_CASES = Set.of(
            "submit_http",
            "missing_submit_id",
            "nested_submit_id",
            "missing_submit_status",
            "unknown_submit_status",
            "poll_http",
            "missing_poll_id",
            "missing_poll_status",
            "unknown_poll_status");

    private BehaviorTestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: BehaviorTestMain <api-base-uri> <case>");
        }

        String testCase = args[1];
        if ("interrupt".equals(testCase)) {
            verifyInterruption(URI.create(args[0]));
        } else {
            VcfNetworksClient client = new VcfNetworksClient(
                    URI.create(args[0]), "fixture-token", Duration.ZERO);
            if ("chain_success".equals(testCase)) {
                verifyChainSuccess(client);
            } else if ("failed".equals(testCase)) {
                verifyFailed(client);
            } else if (IO_CASES.contains(testCase)) {
                verifyIOException(client);
            } else {
                throw new AssertionError("unknown test case: " + testCase);
            }
        }
        System.out.println("PASS " + testCase);
    }

    private static void verifyChainSuccess(VcfNetworksClient client) throws Exception {
        VcfNetworksClient.CertificateUpdateStatus result =
                client.updateCertificateAndWait(
                        "node/β ?#%", CERTIFICATE, PRIVATE_KEY, CHAIN);
        require("update /✓?#%".equals(result.id()), "wrong special update id");
        require("node\n\"quoted\"".equals(result.name()), "wrong decoded name");
        require("SUCCESS".equals(result.status()), "expected SUCCESS");
        require(result.errorMessage() == null, "unexpected success error message");
    }

    private static void verifyFailed(VcfNetworksClient client) throws Exception {
        VcfNetworksClient.CertificateUpdateStatus result =
                client.updateCertificateAndWait(
                        "failed certificate", CERTIFICATE, PRIVATE_KEY, null);
        require("failed-7".equals(result.id()), "wrong failed update id");
        require("failed certificate".equals(result.name()), "wrong failed name");
        require("FAILED".equals(result.status()), "expected FAILED");
        require(
                "certificate rejected".equals(result.errorMessage()),
                "wrong failure error message");
    }

    private static void verifyIOException(VcfNetworksClient client) throws Exception {
        try {
            client.updateCertificateAndWait(
                    "error certificate", CERTIFICATE, PRIVATE_KEY, null);
            throw new AssertionError("expected IOException");
        } catch (IOException expected) {
            // Required failure mode.
        }
    }

    private static void verifyInterruption(URI apiBaseUri) throws Exception {
        VcfNetworksClient client = new VcfNetworksClient(
                apiBaseUri, "fixture-token", Duration.ofSeconds(30));
        AtomicReference<Throwable> outcome = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            Thread.currentThread().interrupt();
            try {
                client.updateCertificateAndWait(
                        "interrupt certificate", CERTIFICATE, PRIVATE_KEY, null);
                outcome.set(new AssertionError("interrupted call returned normally"));
            } catch (Throwable result) {
                outcome.set(result);
            }
        });
        worker.start();
        worker.join(5_000);
        require(!worker.isAlive(), "interrupted client call did not terminate");
        require(
                outcome.get() instanceof InterruptedException,
                "interruption was not propagated: " + outcome.get());
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
