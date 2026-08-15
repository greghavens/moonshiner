import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Test harness entry point. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: TestMain <estate-inventory.json> <compatibility-snapshot.json> <output.json>");
        }

        String inventory = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        String compatibility = Files.readString(Path.of(args[1]), StandardCharsets.UTF_8);
        String artifact = ArchitectureClient.createArchitecture(inventory, compatibility);
        if (artifact == null || artifact.isBlank()) {
            throw new IllegalStateException("ArchitectureClient returned an empty artifact");
        }

        Path output = Path.of(args[2]);
        Files.createDirectories(output.getParent());
        Files.writeString(output, artifact, StandardCharsets.UTF_8);
    }
}
