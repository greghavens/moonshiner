import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;

public final class TestMain {
    private static int checks;

    public static void main(String[] args) throws Exception {
        testSortedCollectionAndSuccessfulMutation();
        testFailedPrecheck("http-fail");
        testFailedPrecheck("invalid-action");
        testFailedPrecheck("absent-action");
        testFailedPrecheck("malformed");
        testUnsuccessfulListResponse();
        testUnsuccessfulMutationResponse();
        System.out.println("all " + checks + " checks passed");
    }

    private static void testSortedCollectionAndSuccessfulMutation() throws Exception {
        Path log = Path.of(".verify-build", "ok-requests.jsonl");
        try (Mock mock = Mock.start("ok", log)) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "loopback-test-token");
            List<VcfAutomationClient.Resource> expected = List.of(
                    new VcfAutomationClient.Resource(
                            "resource-upper", "Alpha node", "Disk"),
                    new VcfAutomationClient.Resource(
                            "resource-a", "alpha node", "Network"),
                    new VcfAutomationClient.Resource(
                            "resource-b", "alpha node", "VirtualMachine"),
                    new VcfAutomationClient.Resource(
                            "resource-z", "zeta node", "VirtualMachine"));

            List<VcfAutomationClient.Resource> first =
                    client.listDeploymentResources("dep/blue");
            List<VcfAutomationClient.Resource> second =
                    client.listDeploymentResources("dep/blue");
            check(first.equals(expected), "first collection response was not sorted");
            check(second.equals(expected), "flipped collection response was not sorted");

            VcfAutomationClient.ActionRequest request =
                    client.submitResourceActionIfAvailable(
                            "dep/blue",
                            "vm one/α?#%",
                            "Snapshot",
                            "operator said \"pause\"\nnow");
            check("request-001".equals(request.id()), "request id was not decoded");
            check("Snapshot".equals(request.actionId()), "action id was not decoded");
            check("CREATED".equals(request.status()), "request status was not decoded");
        }

        List<String> lines = Files.readAllLines(log, StandardCharsets.UTF_8);
        check(count(lines, "GET", "/resources\"") >= 2,
                "each collection call must perform the documented GET");
        int precheck = firstIndexOf(lines, "GET", "/actions\"");
        int mutation = firstIndexOf(lines, "POST", "/requests\"");
        check(precheck >= 0, "precheck path or encoding is wrong");
        check(mutation > precheck, "mutation did not follow the successful precheck");
        for (String line : lines) {
            check(hasConfiguredAuthorization(line),
                    "a request omitted the bearer token");
        }
    }

    private static void testFailedPrecheck(String mode) throws Exception {
        Path log = Path.of(".verify-build", mode + "-requests.jsonl");
        try (Mock mock = Mock.start(mode, log)) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "loopback-test-token");
            boolean rejected = false;
            try {
                client.submitResourceActionIfAvailable(
                        "dep/blue", "vm one/α?#%", "PowerOff", "maintenance");
            } catch (VcfAutomationClient.PrecheckFailedException expected) {
                rejected = true;
            }
            check(rejected, mode + " precheck did not raise PrecheckFailedException");
        }

        List<String> lines = Files.readAllLines(log, StandardCharsets.UTF_8);
        check(count(lines, "GET", "/actions\"") >= 1,
                mode + " flow did not perform the precheck GET");
        check(lines.stream().noneMatch(line -> line.contains("\"method\": \"POST\"")),
                mode + " flow changed state after a failed precheck");
        check(lines.stream().allMatch(TestMain::hasConfiguredAuthorization),
                mode + " flow omitted the bearer token");
    }

    private static void testUnsuccessfulListResponse() throws Exception {
        Path log = Path.of(".verify-build", "list-fail-requests.jsonl");
        try (Mock mock = Mock.start("list-fail", log)) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "loopback-test-token");
            boolean failed = false;
            try {
                client.listDeploymentResources("dep/blue");
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "unsuccessful collection response did not raise IOException");
        }

        List<String> lines = Files.readAllLines(log, StandardCharsets.UTF_8);
        check(count(lines, "GET", "/resources\"") >= 1,
                "list failure did not call the documented collection operation");
        check(lines.stream().allMatch(TestMain::hasConfiguredAuthorization),
                "list failure request omitted the bearer token");
    }

    private static void testUnsuccessfulMutationResponse() throws Exception {
        Path log = Path.of(".verify-build", "mutation-fail-requests.jsonl");
        try (Mock mock = Mock.start("mutation-fail", log)) {
            VcfAutomationClient client =
                    new VcfAutomationClient(mock.baseUri(), "loopback-test-token");
            boolean failed = false;
            try {
                client.submitResourceActionIfAvailable(
                        "dep/blue", "vm one/α?#%", "PowerOff", "maintenance");
            } catch (IOException expected) {
                failed = true;
            }
            check(failed, "unsuccessful mutation response did not raise IOException");
        }

        List<String> lines = Files.readAllLines(log, StandardCharsets.UTF_8);
        int precheck = firstIndexOf(lines, "GET", "/actions\"");
        int mutation = firstIndexOf(lines, "POST", "/requests\"");
        check(precheck >= 0, "mutation failure flow omitted the precheck");
        check(mutation > precheck, "mutation failure did not occur after the precheck");
        check(lines.stream().allMatch(TestMain::hasConfiguredAuthorization),
                "mutation failure flow omitted the bearer token");
    }

    private static boolean has(String logLine, String method, String pathSuffix) {
        return logLine.contains("\"method\": \"" + method + "\"")
                && logLine.contains(pathSuffix);
    }

    private static boolean hasConfiguredAuthorization(String logLine) {
        return logLine.contains(
                "\"authorization\": \"Bearer loopback-test-token\"");
    }

    private static long count(List<String> lines, String method, String pathSuffix) {
        return lines.stream().filter(line -> has(line, method, pathSuffix)).count();
    }

    private static int firstIndexOf(List<String> lines, String method, String pathSuffix) {
        for (int index = 0; index < lines.size(); index++) {
            if (has(lines.get(index), method, pathSuffix)) {
                return index;
            }
        }
        return -1;
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class Mock implements AutoCloseable {
        private final Process process;
        private final URI baseUri;

        private Mock(Process process, URI baseUri) {
            this.process = process;
            this.baseUri = baseUri;
        }

        static Mock start(String mode, Path log) throws Exception {
            ProcessBuilder builder = new ProcessBuilder(
                    "python3",
                    "-B",
                    "tests/mock_vcf.py",
                    "--contract",
                    "docs/contract.json",
                    "--mode",
                    mode,
                    "--log",
                    log.toString());
            builder.redirectError(ProcessBuilder.Redirect.INHERIT);
            Process process = builder.start();
            BufferedReader output = new BufferedReader(
                    new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
            String portLine = output.readLine();
            if (portLine == null) {
                process.waitFor(5, TimeUnit.SECONDS);
                throw new IOException("loopback mock failed to start");
            }
            int port = Integer.parseInt(portLine);
            return new Mock(process, URI.create("http://127.0.0.1:" + port));
        }

        URI baseUri() {
            return baseUri;
        }

        @Override
        public void close() throws Exception {
            process.destroy();
            if (!process.waitFor(5, TimeUnit.SECONDS)) {
                process.destroyForcibly();
                process.waitFor(5, TimeUnit.SECONDS);
            }
        }
    }
}
