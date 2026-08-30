import java.net.URI;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * Fixed Java harness. The Python verifier supplies runtime-only fixture values.
 */
public final class TestMain {
    private static final int FIXED_ARGUMENTS = 6;

    public static void main(String[] args) throws Exception {
        if (args.length < FIXED_ARGUMENTS
                || (args.length - FIXED_ARGUMENTS) % 3 != 0) {
            throw new IllegalArgumentException(
                    "usage: TestMain <base> <token> <host-a> <host-b> "
                            + "<task-a> <task-b> [<id> <fqdn> <status>]...");
        }

        VcfHostRefreshClient client = new VcfHostRefreshClient(
                URI.create(args[0]),
                args[1],
                Duration.ofSeconds(3));
        List<String> selected = List.of(args[2], args[3], args[2]);

        VcfHostRefreshClient.RefreshResult first = client.refreshHostsAndWait(
                new VcfHostRefreshClient.RefreshRequest(selected, null),
                5,
                Duration.ZERO);
        VcfHostRefreshClient.RefreshResult second = client.refreshHostsAndWait(
                new VcfHostRefreshClient.RefreshRequest(selected, Boolean.FALSE),
                5,
                Duration.ZERO);

        require(args[4].equals(first.task().id()), "first task identity changed");
        require(args[5].equals(second.task().id()), "second task identity changed");
        require("SUCCESSFUL".equals(first.task().status()), "first task was not terminal");
        require("SUCCESSFUL".equals(second.task().status()), "second task was not terminal");
        require(first.task().completionTimestamp() != null, "first terminal task was not returned");
        require(second.task().completionTimestamp() != null, "second terminal task was not returned");

        List<VcfHostRefreshClient.Host> expected = new ArrayList<>();
        for (int index = FIXED_ARGUMENTS; index < args.length; index += 3) {
            expected.add(new VcfHostRefreshClient.Host(
                    args[index],
                    args[index + 1],
                    args[index + 2]));
        }
        require(expected.equals(first.hosts()), "first host view was not globally sorted");
        require(expected.equals(second.hosts()), "second host view was not globally sorted");
        require(first.hosts().equals(second.hosts()), "server order leaked into client output");
        expectUnmodifiable(first.hosts());

        ArrayList<String> tooMany = new ArrayList<>();
        for (int index = 0; index < 101; index++) {
            tooMany.add("host-" + index);
        }
        expectIllegalArgument(
                () -> client.refreshHostsAndWait(
                        new VcfHostRefreshClient.RefreshRequest(tooMany, null),
                        1,
                        Duration.ZERO),
                "hostIds upper bound was not validated locally");
        expectIllegalArgument(
                () -> client.refreshHostsAndWait(
                        new VcfHostRefreshClient.RefreshRequest(List.of(" host "), null),
                        1,
                        Duration.ZERO),
                "surrounding host-id whitespace was not rejected locally");
        expectIllegalArgument(
                () -> client.refreshHostsAndWait(
                        new VcfHostRefreshClient.RefreshRequest(List.of("host"), null),
                        0,
                        Duration.ZERO),
                "maxPolls was not validated locally");
        expectIllegalArgument(
                () -> client.refreshHostsAndWait(
                        new VcfHostRefreshClient.RefreshRequest(List.of("host"), null),
                        1,
                        Duration.ofMillis(-1)),
                "negative pollInterval was not validated locally");

        System.out.println("SUCCESSFUL");
    }

    private static void expectUnmodifiable(List<VcfHostRefreshClient.Host> hosts) {
        try {
            hosts.add(new VcfHostRefreshClient.Host("x", "x", "x"));
        } catch (UnsupportedOperationException expected) {
            return;
        }
        throw new AssertionError("returned host list is mutable");
    }

    private static void expectIllegalArgument(
            ThrowingAction action,
            String message) throws Exception {
        try {
            action.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError(message);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingAction {
        void run() throws Exception;
    }
}
