import java.nio.file.Files;
import java.nio.file.Path;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException("expected INVENTORY SNAPSHOT INSTALLER_SPEC OUTPUT");
        }
        Path inventory = Path.of(args[0]);
        Path snapshot = Path.of(args[1]);
        Path specification = Path.of(args[2]);
        Path output = Path.of(args[3]);
        MigrationPlanner.generate(inventory, snapshot, specification, output);
        if (!Files.isRegularFile(output) || Files.size(output) == 0) {
            throw new AssertionError("MigrationPlanner did not create a non-empty artifact");
        }
    }
}
