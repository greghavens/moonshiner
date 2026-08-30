import java.io.IOException;
import java.net.URI;
import java.util.List;

public final class TestMain {
    private static final List<String> PRIMARY_URLS = List.of(
            "https://hooks.example.com/vcf-alerts",
            "https://backup.example.com/vcf-alerts?source=operations&severity=warning");
    private static final List<String> ALTERNATE_URLS = List.of(
            "https://z.example.net/one?raw=\"quoted\"\\tail",
            "https://a.example.net/two?value=backslash%5Ctail",
            "https://middle.example.net/three#fragment");

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <base-uri> <scenario>");
        }

        String scenario = args[1];
        List<String> urls = scenario.equals("direct") ? ALTERNATE_URLS : PRIMARY_URLS;
        String sessionId = scenario.equals("direct") ? "alternate-session-token" : "session-test-token+/=";
        VcfOperationsForLogsClient client = new VcfOperationsForLogsClient(URI.create(args[0]), sessionId);

        if (scenario.equals("failure")) {
            try {
                client.replaceWebhookUrls(urls);
            } catch (IOException expected) {
                System.out.println("request failed with IOException");
                return;
            }
            throw new AssertionError("expected replaceWebhookUrls to throw IOException");
        }

        if (!scenario.equals("retry") && !scenario.equals("direct")) {
            throw new IllegalArgumentException("unknown scenario: " + scenario);
        }
        client.replaceWebhookUrls(urls);
        System.out.println("webhook URLs replaced");
    }
}
