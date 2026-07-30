import java.net.URI;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;

/**
 * Fixed integration harness. The Python verifier supplies a loopback URL and
 * runtime-generated fixture values.
 */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 12) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <username> <password>"
                            + " <dc-1> <dc-2> <dc-3>"
                            + " <vm-1> <vm-2> <vm-3>"
                            + " <name-1> <name-2> <name-3>");
        }

        VcenterVmClient client = new VcenterVmClient(
                URI.create(args[0]),
                Duration.ofSeconds(3));

        List<String> datacenters = List.of(args[3], args[4], args[5]);
        List<VcenterVmClient.VmSummary> result =
                client.collectByDatacenters(args[1], args[2], datacenters);

        require(result.size() == 3, "completed VM results were lost or duplicated");
        for (int index = 0; index < result.size(); index++) {
            VcenterVmClient.VmSummary vm = result.get(index);
            require(args[6 + index].equals(vm.vm()),
                    "VM order or identifier changed at index " + index);
            require(args[9 + index].equals(vm.name()),
                    "VM name was not decoded at index " + index);
            require("POWERED_ON".equals(vm.powerState()),
                    "unexpected VM power state at index " + index);
        }
        require(Long.valueOf(2).equals(result.get(0).cpuCount()),
                "first VM cpu_count was not decoded");
        require(Long.valueOf(2048).equals(result.get(0).memorySizeMib()),
                "first VM memory_size_mib was not decoded");
        require(result.get(1).cpuCount() == null,
                "missing cpu_count must remain unset");
        require(Long.valueOf(4096).equals(result.get(1).memorySizeMib()),
                "second VM memory_size_mib was not decoded");
        require(Long.valueOf(8).equals(result.get(2).cpuCount()),
                "third VM cpu_count was not decoded");
        require(result.get(2).memorySizeMib() == null,
                "null memory_size_mib must remain unset");

        expectUnsupported(
                () -> result.add(result.get(0)),
                "result list must be immutable");

        expectIllegalArgument(
                () -> client.collectByDatacenters(
                        args[1], args[2], List.of()),
                "empty datacenter list must be rejected before traffic");
        expectIllegalArgument(
                () -> client.collectByDatacenters(
                        args[1], args[2], List.of(args[3], args[3])),
                "duplicate datacenters must be rejected before traffic");
        expectIllegalArgument(
                () -> client.collectByDatacenters(
                        args[1], args[2], Arrays.asList(args[3], null)),
                "null datacenter must be rejected before traffic");
        expectIllegalArgument(
                () -> client.collectByDatacenters(
                        "bad:user", args[2], datacenters),
                "colon-bearing username must be rejected before traffic");

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
