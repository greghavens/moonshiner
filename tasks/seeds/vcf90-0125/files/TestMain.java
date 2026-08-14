import java.net.URI;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain BASE_URI");
        }

        AtomicInteger refreshes = new AtomicInteger();
        VsanDataProtectionClient client = new VsanDataProtectionClient(
                URI.create(args[0]),
                "access-old",
                () -> {
                    if (refreshes.incrementAndGet() != 1) {
                        throw new AssertionError("token was refreshed more than once");
                    }
                    return "access-new";
                });

        VsanDataProtectionClient.ProtectionGroup group =
                client.createProtectionGroupAndReadBack(
                        "domain c8/blue",
                        "Nightly \"critical\"\nset",
                        List.of("vm-101", "vm\\202"));

        assertEquals("pg 42/blue", group.id(), "protection-group id");
        assertEquals("Nightly \"critical\"\nset", group.name(), "protection-group name");
        assertEquals("ACTIVE", group.status(), "protection-group status");
        assertEquals(1, refreshes.get(), "refresh count");
        System.out.println("PASS");
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (!expected.equals(actual)) {
            throw new AssertionError(label + ": expected " + expected + ", got " + actual);
        }
    }
}
