import java.net.URI;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * Fixed integration harness. The Python verifier supplies a loopback URL,
 * runtime bearer token, and runtime-generated expected records.
 */
public final class TestMain {
    private static final int DOMAIN_FIELDS = 4;

    public static void main(String[] args) throws Exception {
        if (args.length < 6 || (args.length - 2) % DOMAIN_FIELDS != 0) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <access-token>"
                            + " (<id> <name> <status> <type>)+");
        }

        VcfDomainInventoryClient client = new VcfDomainInventoryClient(
                URI.create(args[0]),
                args[1],
                2,
                Duration.ofSeconds(3));

        List<VcfDomainInventoryClient.Domain> expected = new ArrayList<>();
        for (int index = 2; index < args.length; index += DOMAIN_FIELDS) {
            expected.add(new VcfDomainInventoryClient.Domain(
                    args[index],
                    args[index + 1],
                    args[index + 2],
                    args[index + 3]));
        }
        expected.sort(Comparator
                .comparing(VcfDomainInventoryClient.Domain::name)
                .thenComparing(VcfDomainInventoryClient.Domain::id));
        expected = List.copyOf(expected);

        List<VcfDomainInventoryClient.Domain> first = client.listAllDomains();
        List<VcfDomainInventoryClient.Domain> second = client.listAllDomains();

        require(expected.equals(first), "first traversal was incomplete or not globally sorted");
        require(expected.equals(second), "second traversal was incomplete or not globally sorted");
        require(first.equals(second), "collection order changed between traversals");
        expectUnsupported(
                () -> first.add(first.get(0)),
                "result list must be unmodifiable");

        expectIllegalArgument(
                () -> new VcfDomainInventoryClient(
                        URI.create(args[0]), args[1], 0, Duration.ofSeconds(3)),
                "non-positive page size must be rejected locally");
        expectIllegalArgument(
                () -> new VcfDomainInventoryClient(
                        URI.create(args[0]), "   ", 2, Duration.ofSeconds(3)),
                "blank access token must be rejected locally");
        expectIllegalArgument(
                () -> new VcfDomainInventoryClient(
                        URI.create(args[0] + "unexpected"), args[1], 2, Duration.ofSeconds(3)),
                "non-origin base URI must be rejected locally");

        System.out.println("SUCCESSFUL");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void expectIllegalArgument(ThrowingAction action, String message)
            throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    private static void expectUnsupported(ThrowingAction action, String message)
            throws Exception {
        try {
            action.run();
        } catch (UnsupportedOperationException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
