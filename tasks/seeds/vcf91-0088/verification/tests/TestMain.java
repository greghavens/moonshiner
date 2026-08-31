import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public final class TestMain {
    private static String env(String name) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) {
            throw new AssertionError("missing environment value: " + name);
        }
        return value;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static List<NsxPolicyClient.Segment> expectedSegments() {
        List<NsxPolicyClient.Segment> expected = new ArrayList<>();
        for (int index = 1; index <= 4; index++) {
            expected.add(new NsxPolicyClient.Segment(
                    env("NSX_SEGMENT_" + index + "_ID"),
                    env("NSX_SEGMENT_" + index + "_NAME")));
        }
        expected.sort(Comparator
                .comparing(NsxPolicyClient.Segment::displayName)
                .thenComparing(NsxPolicyClient.Segment::id));
        return List.copyOf(expected);
    }

    private static void assertImmutable(
            List<NsxPolicyClient.Segment> segments) {
        try {
            segments.add(new NsxPolicyClient.Segment("mutant", "mutant"));
            throw new AssertionError("segment result is mutable");
        } catch (UnsupportedOperationException expected) {
            // Expected.
        }
    }

    private static void assertConstructorValidation(String baseUrl) {
        try {
            new NsxPolicyClient(
                    baseUrl + "/already-a-path",
                    env("NSX_USERNAME"),
                    env("NSX_PASSWORD"),
                    Duration.ofSeconds(2));
            throw new AssertionError("non-origin base URL was accepted");
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
    }

    private static void assertReadableLog(Path requestLog) throws Exception {
        List<String> lines = Files.readAllLines(requestLog);
        check(lines.size() == 5, "unexpected request-log record count");
        for (String line : lines) {
            check(
                    line.contains("\"operationId\":\"ListAllInfraSegments\""),
                    "request log omitted the contract operationId");
        }
        long forbidden = lines.stream()
                .filter(line -> line.contains("\"response_status\":403"))
                .count();
        check(forbidden == 1, "expected one live-shaped auth failure");
    }

    public static void main(String[] args) throws Exception {
        check(args.length == 2, "usage: TestMain BASE_URL REQUEST_LOG");
        String baseUrl = args[0];
        Path requestLog = Path.of(args[1]);

        assertConstructorValidation(baseUrl);

        NsxPolicyClient rejected = new NsxPolicyClient(
                baseUrl,
                env("NSX_USERNAME"),
                env("NSX_PASSWORD") + "-wrong",
                Duration.ofSeconds(4));
        try {
            rejected.listAllSegments();
            throw new AssertionError("invalid Basic credentials were accepted");
        } catch (NsxPolicyClient.NsxPolicyException expected) {
            check(expected.statusCode() == 403, "auth failure status");
            check(
                    expected.responseBody().contains("\"error_code\":403")
                            && expected.responseBody().contains(
                                    "\"module_name\":\"common-services\""),
                    "auth failure envelope");
        }

        NsxPolicyClient client = new NsxPolicyClient(
                baseUrl,
                env("NSX_USERNAME"),
                env("NSX_PASSWORD"),
                Duration.ofSeconds(4));

        List<NsxPolicyClient.Segment> expected = expectedSegments();
        List<NsxPolicyClient.Segment> first = client.listAllSegments();
        check(first.equals(expected), "first inventory is not globally sorted");
        assertImmutable(first);

        List<NsxPolicyClient.Segment> second = client.listAllSegments();
        check(second.equals(expected), "second inventory is not globally sorted");
        assertImmutable(second);

        assertReadableLog(requestLog);
        System.out.println("TEST_MAIN_OK");
    }
}
