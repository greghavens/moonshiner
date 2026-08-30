import java.net.URI;
import java.util.List;

/** Public harness used by the acceptance verifier. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <loopback-base-uri>");
        }

        VcfOperationsLogsClient client = new VcfOperationsLogsClient(
                URI.create(args[0]),
                "svc-logs",
                "p@ss\"word\\9",
                "Local");

        List<VcfOperationsLogsClient.Query> queries = List.of(
                new VcfOperationsLogsClient.Query(
                        "text/CONTAINS alpha+snow 雪%/timestamp/LAST 60000",
                        2,
                        2500,
                        "DEFAULT",
                        List.of("core fields", "", "ops/fields+雪?"),
                        "ASC"),
                new VcfOperationsLogsClient.Query(
                        "text/CONTAINS beta/timestamp/LAST 60000",
                        null,
                        null,
                        "",
                        null,
                        ""));

        List<String> actual = client.queryEventTexts(queries);
        List<String> expected = List.of(
                "alpha \"one\"\t雪",
                "path\\node/\b\f\r",
                "beta\nline 🚀");
        if (!actual.equals(expected)) {
            throw new AssertionError("event texts differ: " + actual);
        }
        System.out.println("TestMain OK");
    }
}
