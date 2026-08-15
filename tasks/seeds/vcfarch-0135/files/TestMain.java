import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Fixed harness for the single-file ArchitectureClient. */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <estate-inventory.json>");
        }
        String inventory = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        String artifact = new ArchitectureClient().design(inventory);
        if (artifact == null || artifact.isBlank()) {
            throw new IllegalStateException("ArchitectureClient returned no artifact");
        }
        System.out.println(artifact);
    }
}
