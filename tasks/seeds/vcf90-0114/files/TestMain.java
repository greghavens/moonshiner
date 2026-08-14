import java.net.URI;
import java.util.List;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <base-uri> <scenario>");
        }

        VcfInstallerClient client = new VcfInstallerClient(URI.create(args[0]));
        try {
            List<String> actual = client.listAllTaskIds(
                    "administrator@vsphere.local",
                    "P@ss\"word\\one\nline\t\u0001",
                    2);
            if (!args[1].equals("happy")) {
                throw new AssertionError("scenario should have failed: " + args[1]);
            }
            List<String> expected = List.of(
                    "task-001", "task-002", "task-003", "task-004", "task-005");
            if (!actual.equals(expected)) {
                throw new AssertionError(
                        "task IDs differ: expected=" + expected + " actual=" + actual);
            }
            System.out.println("TASK_IDS=" + String.join(",", actual));
        } catch (Exception exception) {
            if (args[1].equals("happy")) {
                throw exception;
            }
            System.out.println("EXPECTED_FAILURE=" + args[1]);
        }
    }
}
