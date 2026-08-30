import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

public final class TestMain {
    private static final class TrackingTokenProvider
            implements NsxPolicyClient.AccessTokenProvider {
        private final String initial;
        private final String refreshed;
        private int initialCalls;
        private int refreshCalls;

        TrackingTokenProvider(String initial, String refreshed) {
            this.initial = initial;
            this.refreshed = refreshed;
        }

        @Override
        public synchronized String initialAccessToken() {
            initialCalls++;
            return initial;
        }

        @Override
        public synchronized String refreshAccessToken(String expiredToken)
                throws IOException {
            if (!initial.equals(expiredToken)) {
                throw new IOException("refresh received the wrong expired token");
            }
            refreshCalls++;
            return refreshed;
        }

        synchronized int initialCalls() {
            return initialCalls;
        }

        synchronized int refreshCalls() {
            return refreshCalls;
        }
    }

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

    private static void assertConstructorValidation(
            String baseUrl,
            TrackingTokenProvider provider) {
        int initialBefore = provider.initialCalls();
        try {
            new NsxPolicyClient(
                    baseUrl + "/already-a-path",
                    provider,
                    Duration.ofSeconds(2));
            throw new AssertionError("non-origin base URL was accepted");
        } catch (IllegalArgumentException expected) {
            // Expected.
        }
        check(
                provider.initialCalls() == initialBefore,
                "constructor called the token provider");
    }

    private static void assertReadableLog(Path requestLog) throws Exception {
        List<String> lines = Files.readAllLines(requestLog);
        check(lines.size() == 5, "unexpected request-log record count");
        for (String line : lines) {
            check(
                    line.contains("\"operationId\":\"ListAllInfraSegments\""),
                    "request log omitted the contract operationId");
        }
        long unauthorized = lines.stream()
                .filter(line -> line.contains("\"response_status\":401"))
                .count();
        check(unauthorized == 1, "expected exactly one expiry challenge");
    }

    public static void main(String[] args) throws Exception {
        check(args.length == 2, "usage: TestMain BASE_URL REQUEST_LOG");
        String baseUrl = args[0];
        Path requestLog = Path.of(args[1]);

        TrackingTokenProvider provider = new TrackingTokenProvider(
                env("NSX_INITIAL_TOKEN"),
                env("NSX_REFRESHED_TOKEN"));
        assertConstructorValidation(baseUrl, provider);

        NsxPolicyClient client = new NsxPolicyClient(
                baseUrl,
                provider,
                Duration.ofSeconds(4));
        check(
                provider.initialCalls() == 0 && provider.refreshCalls() == 0,
                "client construction was not lazy");

        List<NsxPolicyClient.Segment> expected = expectedSegments();
        List<NsxPolicyClient.Segment> first = client.listAllSegments();
        check(first.equals(expected), "first inventory is not globally sorted");
        assertImmutable(first);
        check(provider.initialCalls() == 1, "initial token call count");
        check(provider.refreshCalls() == 1, "refresh token call count");

        List<NsxPolicyClient.Segment> second = client.listAllSegments();
        check(second.equals(expected), "second inventory is not globally sorted");
        assertImmutable(second);
        check(
                provider.initialCalls() == 1,
                "initial token was acquired more than once");
        check(
                provider.refreshCalls() == 1,
                "refreshed token was not reused");

        assertReadableLog(requestLog);
        System.out.println("TEST_MAIN_OK");
    }
}
