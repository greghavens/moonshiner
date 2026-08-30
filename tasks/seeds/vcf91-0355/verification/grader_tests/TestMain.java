import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

public final class TestMain {
    private static final String DEPLOYMENT_ID = "11111111-1111-4111-8111-111111111191";

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <base-uri> <scenario>");
        }

        String scenario = args[1];
        Duration interval = scenario.equals("interrupted")
                ? Duration.ofSeconds(30) : Duration.ZERO;
        VcfAutomationClient client = new VcfAutomationClient(
                URI.create(args[0]), "test-token-91", interval);

        if (scenario.equals("interrupted")) {
            verifyInterruption(client);
        } else if (scenario.startsWith("terminal-")) {
            String terminal = scenario.substring("terminal-".length());
            verifyResult(client, terminal);
        } else if (scenario.equals("submit-terminal")) {
            verifyResult(client, "ABORTED");
        } else {
            verifyIOException(client);
        }
    }

    private static Map<String, String> inputs() {
        Map<String, String> inputs = new LinkedHashMap<>();
        inputs.put("force\"mode", "false\nwith-newline");
        inputs.put("ticket\\id", "CHG-9100\tapproved");
        return inputs;
    }

    private static VcfAutomationClient.RequestResult invoke(VcfAutomationClient client)
            throws Exception {
        return client.submitDeploymentActionAndWait(
                DEPLOYMENT_ID,
                "Deployment.\"PowerOff\\safe\u2603",
                inputs(),
                "VCF 9.1 maintenance\nwindow");
    }

    private static void verifyResult(VcfAutomationClient client, String terminal)
            throws Exception {
        VcfAutomationClient.RequestResult result = invoke(client);
        String expected = "RESULT|22222222-2222-4222-8222-222222222291|"
                + DEPLOYMENT_ID + "|" + terminal;
        String actual = "RESULT|" + result.requestId() + "|"
                + result.deploymentId() + "|" + result.status();
        if (!expected.equals(actual)) {
            throw new AssertionError("unexpected terminal result: " + actual);
        }
        System.out.println(actual);
    }

    private static void verifyIOException(VcfAutomationClient client) throws Exception {
        try {
            invoke(client);
            throw new AssertionError("expected IOException");
        } catch (IOException expected) {
            System.out.println("IOEXCEPTION");
        }
    }

    private static void verifyInterruption(VcfAutomationClient client) throws Exception {
        AtomicReference<Throwable> outcome = new AtomicReference<>();
        Thread worker = new Thread(() -> {
            try {
                invoke(client);
                outcome.set(new AssertionError("call completed before interruption"));
            } catch (Throwable thrown) {
                outcome.set(thrown);
            }
        }, "vcf-client-interruption-test");
        worker.start();
        Thread.sleep(1000);
        worker.interrupt();
        worker.join(5000);
        if (worker.isAlive()) {
            throw new AssertionError("client ignored interruption");
        }
        if (!(outcome.get() instanceof InterruptedException)) {
            throw new AssertionError("interruption was not propagated", outcome.get());
        }
        System.out.println("INTERRUPTED");
    }
}
