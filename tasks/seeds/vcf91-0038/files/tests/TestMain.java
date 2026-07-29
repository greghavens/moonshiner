import java.net.URI;
import java.time.Duration;
import java.util.List;

/**
 * Fixed integration harness. The Python verifier supplies a loopback URL and
 * runtime-generated fixture values.
 */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 9) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <username> <password>"
                            + " <domain-1> <domain-2> <domain-3>"
                            + " <name-1> <name-2> <name-3>");
        }

        VcfDomainClient client = new VcfDomainClient(
                URI.create(args[0]),
                Duration.ofSeconds(3));

        List<String> requested = List.of(args[3], args[4], args[5]);
        List<VcfDomainClient.Domain> result = client.collectDomains(
                args[1],
                args[2],
                null,
                null,
                requested);

        require(result.size() == 3, "completed domains were lost or duplicated");
        for (int index = 0; index < result.size(); index++) {
            VcfDomainClient.Domain domain = result.get(index);
            require(requested.get(index).equals(domain.id()),
                    "domain order or id changed at index " + index);
            require(args[6 + index].equals(domain.name()),
                    "domain name was not decoded at index " + index);
            require("ACTIVE".equals(domain.status()),
                    "unexpected domain status at index " + index);
            require((index == 0 ? "MANAGEMENT" : "VI").equals(domain.type()),
                    "unexpected domain type at index " + index);
        }

        expectUnsupported(
                () -> result.add(result.get(0)),
                "result list must be unmodifiable");
        expectIllegalArgument(
                () -> client.collectDomains(
                        args[1], args[2], null, null, List.of()),
                "empty domain list must be rejected before traffic");
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
