import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.List;

/**
 * Protected harness for NsxPolicyClient. The Python verifier supplies a
 * loopback mock URL and inspects that mock's request log after this process
 * exits.
 */
public final class TestMain {
    private static final List<NsxPolicyClient.Segment> EXPECTED =
            List.of(
                    new NsxPolicyClient.Segment(
                            "a", "app", "/infra/segments/a"),
                    new NsxPolicyClient.Segment(
                            "b", "app", "/infra/segments/b"),
                    new NsxPolicyClient.Segment(
                            "db", "database", "/infra/segments/db"),
                    new NsxPolicyClient.Segment(
                            "zeta", "web", "/infra/segments/zeta"));

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain BASE_URI SCENARIO");
        }
        NsxPolicyClient client =
                new NsxPolicyClient(
                        URI.create(args[0]),
                        "admin",
                        "secret",
                        Duration.ofSeconds(3));

        switch (args[1]) {
            case "unset" ->
                    assertInventory(
                            client.listAllSegments(
                                    new NsxPolicyClient.ListOptions(
                                            null, "", null, "")));
            case "set" ->
                    assertInventory(
                            client.listAllSegments(
                                    new NsxPolicyClient.ListOptions(
                                            2,
                                            "DVPortgroup",
                                            Boolean.FALSE,
                                            "id,display_name,path")));
            case "repeated" -> assertRepeatedCursor(client);
            case "invalid" -> assertValidation(client);
            case "malformed" -> assertIOException(client, "malformed JSON");
            case "http-error" -> assertIOException(client, "HTTP 503");
            default ->
                    throw new IllegalArgumentException(
                            "unknown scenario: " + args[1]);
        }
        System.out.println("OK " + args[1]);
    }

    private static void assertInventory(
            List<NsxPolicyClient.Segment> actual) {
        if (!EXPECTED.equals(actual)) {
            throw new AssertionError(
                    "inventory order/content mismatch\nactual="
                            + actual
                            + "\nexpected="
                            + EXPECTED);
        }
    }

    private static void assertRepeatedCursor(NsxPolicyClient client)
            throws Exception {
        try {
            client.listAllSegments(NsxPolicyClient.ListOptions.unset());
            throw new AssertionError(
                    "expected RepeatedCursorException");
        } catch (NsxPolicyClient.RepeatedCursorException expected) {
            if (!expected.getMessage().contains("repeat-me")) {
                throw new AssertionError(
                        "repeated cursor missing from error", expected);
            }
        }
    }

    private static void assertValidation(NsxPolicyClient client)
            throws Exception {
        List<NsxPolicyClient.ListOptions> invalid =
                List.of(
                        new NsxPolicyClient.ListOptions(
                                -1, null, null, null),
                        new NsxPolicyClient.ListOptions(
                                1001, null, null, null),
                        new NsxPolicyClient.ListOptions(
                                null, "OVERLAY", null, null));
        for (NsxPolicyClient.ListOptions options : invalid) {
            try {
                client.listAllSegments(options);
                throw new AssertionError(
                        "invalid options were accepted: " + options);
            } catch (IllegalArgumentException expected) {
                // Expected before a request is made.
            }
        }
    }

    private static void assertIOException(
            NsxPolicyClient client, String expectedText)
            throws InterruptedException {
        try {
            client.listAllSegments(NsxPolicyClient.ListOptions.unset());
            throw new AssertionError("expected IOException");
        } catch (IOException expected) {
            if (!expected.getMessage().contains(expectedText)) {
                throw new AssertionError(
                        "unexpected IOException: " + expected.getMessage(),
                        expected);
            }
        }
    }
}
