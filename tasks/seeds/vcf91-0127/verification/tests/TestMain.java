import java.net.URI;
import java.net.http.HttpClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

public final class TestMain {
    private static final String CLONE = "Vcenter.VM_clone$Task";
    private static final String TASK_GET = "Cis.Tasks_get";
    private static final String VM_LIST = "Vcenter.VM_list";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "expected BASE_URI, REQUEST_LOG, SESSION_TOKEN, and NONCE");
        }

        Path requestLog = Path.of(args[1]);
        String sessionToken = args[2];
        String nonce = args[3];
        String sourceVm = "template/\u00fc & " + nonce;
        String tangoName = "tango clone " + nonce;
        String betaName = "beta clone " + nonce;

        VCenterCloneClient client = new VCenterCloneClient(
                URI.create(args[0]),
                sessionToken,
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofSeconds(2))
                        .build(),
                duration -> {
                    long millis = Math.max(1L, duration.toMillis());
                    Thread.sleep(millis);
                });

        List<VCenterCloneClient.VmSummary> first = client.cloneWaitAndList(
                sourceVm,
                tangoName,
                Duration.ofSeconds(3),
                Duration.ofMillis(2));
        List<VCenterCloneClient.VmSummary> expectedFirst = List.of(
                new VCenterCloneClient.VmSummary("vm-100", "alpha", "POWERED_ON"),
                new VCenterCloneClient.VmSummary("vm-200", "mike", "SUSPENDED"),
                new VCenterCloneClient.VmSummary("clone-1", tangoName, "POWERED_OFF"),
                new VCenterCloneClient.VmSummary("vm-300", "zulu", "POWERED_OFF"));
        assertCollection("first", expectedFirst, first);

        List<VCenterCloneClient.VmSummary> second = client.cloneWaitAndList(
                sourceVm,
                betaName,
                Duration.ofSeconds(3),
                Duration.ofMillis(2));
        List<VCenterCloneClient.VmSummary> expectedSecond = List.of(
                new VCenterCloneClient.VmSummary("vm-100", "alpha", "POWERED_ON"),
                new VCenterCloneClient.VmSummary("clone-2", betaName, "POWERED_OFF"),
                new VCenterCloneClient.VmSummary("vm-200", "mike", "SUSPENDED"),
                new VCenterCloneClient.VmSummary("clone-1", tangoName, "POWERED_OFF"),
                new VCenterCloneClient.VmSummary("vm-300", "zulu", "POWERED_OFF"));
        assertCollection("second", expectedSecond, second);

        List<String> lines = Files.readAllLines(requestLog);
        List<String> operations = new ArrayList<>();
        for (String line : lines) {
            if (line.contains("\"operationId\":\"" + CLONE + "\"")) {
                operations.add(CLONE);
            } else if (line.contains("\"operationId\":\"" + TASK_GET + "\"")) {
                operations.add(TASK_GET);
            } else if (line.contains("\"operationId\":\"" + VM_LIST + "\"")) {
                operations.add(VM_LIST);
            } else {
                throw new AssertionError("mock received a non-contract operation: " + line);
            }
        }

        List<String> oneWorkflow = List.of(
                CLONE, TASK_GET, TASK_GET, TASK_GET, VM_LIST);
        List<String> expectedOperations = new ArrayList<>();
        expectedOperations.addAll(oneWorkflow);
        expectedOperations.addAll(oneWorkflow);
        if (!operations.equals(expectedOperations)) {
            throw new AssertionError(
                    "client did not poll each clone to terminal success: " + operations);
        }

        String completeLog = String.join("\n", lines);
        if (!completeLog.contains("\"list_orientation\":\"REVERSED\"")
                || !completeLog.contains("\"list_orientation\":\"FORWARD\"")) {
            throw new AssertionError(
                    "mock did not flip collection element order on every response");
        }
        if (!completeLog.contains("/api/cis/tasks/task%201%2Fblue")
                || !completeLog.contains("/api/cis/tasks/task%202%2Fblue")) {
            throw new AssertionError(
                    "task identifier was not encoded as one RFC 3986 path segment");
        }
        if (!completeLog.contains("\"action\":[\"clone\"]")
                || !completeLog.contains("\"vmw-task\":[\"true\"]")) {
            throw new AssertionError("fixed asynchronous clone query was missing");
        }
        if (!completeLog.contains("\"source\":\"template/\u00fc & " + nonce + "\"")
                || !completeLog.contains("\"name\":\"" + tangoName + "\"")
                || !completeLog.contains("\"name\":\"" + betaName + "\"")) {
            throw new AssertionError("clone request JSON was missing runtime values");
        }

        System.out.println("PASS vcf91-0127");
    }

    private static void assertCollection(
            String run,
            List<VCenterCloneClient.VmSummary> expected,
            List<VCenterCloneClient.VmSummary> actual) {
        if (!actual.equals(expected)) {
            throw new AssertionError(
                    "collection must be locally sorted; run=" + run
                            + " expected=" + expected + " actual=" + actual);
        }
        try {
            actual.add(new VCenterCloneClient.VmSummary("x", "x", "POWERED_OFF"));
            throw new AssertionError("returned collection must be immutable");
        } catch (UnsupportedOperationException expectedException) {
            // Expected.
        }
    }
}
