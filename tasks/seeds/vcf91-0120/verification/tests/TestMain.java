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
        if (args.length != 6) {
            throw new IllegalArgumentException(
                    "usage: TestMain <loopback-base-uri> <username> <password>"
                            + " <dc-1> <dc-2> <dc-3>");
        }

        VcenterVmClient client = new VcenterVmClient(
                URI.create(args[0]),
                Duration.ofSeconds(3));

        List<String> datacenters = List.of(args[3], args[4], args[5]);
        List<VcenterVmClient.VmSummary> result =
                client.collectByDatacenters(args[1], args[2], datacenters);

        String[] expectedIds = {
                "vm-19", "vm-20", "vm-28", "vm-33", "vm-34", "vm-35",
                "vm-36", "vm-37", "vm-38", "vm-39", "vm-43",
        };
        String[] expectedNames = {
                "sddcm01", "vc01", "nsx01a", "vcf-msr01-nxpxf",
                "vcf-msr01-5ghdn", "vcf-msr01-x6j88", "vcf-msr01-6zpgq",
                "vcf01", "vcf-proxy01", "vcf-lic01", "vcf-asr01-szwjz",
        };
        long[] expectedCpu = {4, 4, 6, 4, 8, 8, 8, 4, 4, 2, 8};
        long[] expectedMemory = {
                16384, 21504, 24576, 10240, 24576, 24576,
                24576, 16384, 16384, 4096, 98304,
        };
        require(result.size() == 13, "completed VM results were lost or duplicated");
        for (int index = 0; index < expectedIds.length; index++) {
            VcenterVmClient.VmSummary vm = result.get(index);
            require(expectedIds[index].equals(vm.vm()),
                    "VM order or identifier changed at index " + index);
            require(expectedNames[index].equals(vm.name()),
                    "VM name was not decoded at index " + index);
            require("POWERED_ON".equals(vm.powerState()),
                    "unexpected VM power state at index " + index);
            require(Long.valueOf(expectedCpu[index]).equals(vm.cpuCount()),
                    "cpu_count mismatch at index " + index);
            require(Long.valueOf(expectedMemory[index]).equals(vm.memorySizeMib()),
                    "memory_size_MiB mismatch at index " + index);
        }
        require(result.get(11).cpuCount() == null,
                "missing cpu_count must remain unset");
        require(Long.valueOf(4096).equals(result.get(11).memorySizeMib()),
                "contract-coverage memory_size_MiB was not decoded");
        require(Long.valueOf(8).equals(result.get(12).cpuCount()),
                "contract-coverage cpu_count was not decoded");
        require(result.get(12).memorySizeMib() == null,
                "null memory_size_MiB must remain unset");

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
