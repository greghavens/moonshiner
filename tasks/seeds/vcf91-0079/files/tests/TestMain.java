import java.net.URI;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <base-uri>");
        }

        AtomicInteger sleeps = new AtomicInteger();
        NsxPolicyClient client = new NsxPolicyClient(
                URI.create(args[0]),
                "api-user",
                "p@ss:word",
                Duration.ofSeconds(3),
                duration -> {
                    if (!Duration.ofMillis(7).equals(duration)) {
                        throw new AssertionError("wrong poll interval: " + duration);
                    }
                    sleeps.incrementAndGet();
                });

        String status = client.patchSegmentAndWait(
                "tier 1",
                "orders/blue",
                "Orders \"blue\" \\ primary",
                "10.42.0.1/24",
                null,
                null,
                Duration.ofMillis(7),
                5);

        if (!"REALIZED".equals(status)) {
            throw new AssertionError("expected REALIZED, got " + status);
        }

        String secondStatus = client.patchSegmentAndWait(
                "tier/qa",
                "billing green",
                "Billing\nGreen",
                "10.43.0.1/24",
                "Temporary \"QA\" segment",
                "/infra/dhcp-server-configs/shared",
                Duration.ofMillis(7),
                2);
        if (!"REALIZED".equals(secondStatus)) {
            throw new AssertionError("expected second segment REALIZED, got " + secondStatus);
        }
        if (sleeps.get() != 2) {
            throw new AssertionError(
                    "expected two sleeps between the delayed polls only, got " + sleeps.get());
        }
        System.out.println("TEST_MAIN_OK");
    }
}
