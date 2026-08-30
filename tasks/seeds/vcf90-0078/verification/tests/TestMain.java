import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain <base-uri> <request-log> <scenario>");
        }

        VcfOperationsClient client = new VcfOperationsClient(
                URI.create(args[0]),
                "vcf-ops-test-token",
                2,
                Duration.ofSeconds(3));

        if (!"complete".equals(args[2])) {
            assertIOException(client, args[2]);
            System.out.println("TEST_MAIN_OK");
            return;
        }

        List<VcfOperationsClient.SymptomDefinition> actual =
                client.listAllSymptomDefinitions();
        List<VcfOperationsClient.SymptomDefinition> expected = List.of(
                new VcfOperationsClient.SymptomDefinition(
                        "sym-10", "Alpha", "VMWARE", "HostSystem"),
                new VcfOperationsClient.SymptomDefinition(
                        "sym-10", "Beta", "VMWARE", "VirtualMachine"),
                new VcfOperationsClient.SymptomDefinition(
                        "sym-20", "Alpha \"CPU\"", "VMWARE", "VirtualMachine"),
                new VcfOperationsClient.SymptomDefinition(
                        "sym-30", "Zulu", "VMWARE", "VirtualMachine"),
                new VcfOperationsClient.SymptomDefinition(
                        "sym-40", "Métric \\ memory", "VMWARE", "HostSystem"),
                new VcfOperationsClient.SymptomDefinition(
                        "sym-50", "Escapes /\b\f\n\r\t", "VMWARE", "VirtualMachine"));
        if (!expected.equals(actual)) {
            throw new AssertionError("wrong complete stable collection: " + actual);
        }

        String emitted = actual.stream()
                .map(item -> item.id() + "\t" + item.name())
                .reduce((left, right) -> left + "\n" + right)
                .orElse("");
        String expectedEmission = String.join("\n",
                "sym-10\tAlpha",
                "sym-10\tBeta",
                "sym-20\tAlpha \"CPU\"",
                "sym-30\tZulu",
                "sym-40\tMétric \\ memory",
                "sym-50\tEscapes /\b\f\n\r\t");
        if (!expectedEmission.equals(emitted)) {
            throw new AssertionError("unstable emitted order: " + emitted);
        }

        List<String> requests = Files.readAllLines(
                Path.of(args[1]), StandardCharsets.UTF_8);
        if (requests.size() != 3) {
            throw new AssertionError("expected three logged page requests, got " + requests.size());
        }
        System.out.println("TEST_MAIN_OK");
    }

    private static void assertIOException(
            VcfOperationsClient client,
            String scenario) throws InterruptedException {
        try {
            client.listAllSymptomDefinitions();
        } catch (IOException expected) {
            return;
        }
        throw new AssertionError(scenario + " must fail with IOException");
    }
}
