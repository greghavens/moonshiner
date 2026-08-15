import java.nio.file.Files;
import java.nio.file.Path;

/** Minimal harness for the single-file client. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain <inventory.json> <compatibility-snapshot.json> <output.json>");
        }

        Path output = Path.of(args[2]);
        Files.deleteIfExists(output);
        VcfMigrationPlanner.main(args);

        if (!Files.isRegularFile(output) || Files.size(output) == 0) {
            throw new AssertionError("VcfMigrationPlanner did not create a non-empty artifact");
        }
    }
}
