import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Fixed harness for the single-file client. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain <greenfield-requirements.json> <existing-estate.json>");
        }
        String requirements = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        String estate = Files.readString(Path.of(args[1]), StandardCharsets.UTF_8);
        String artifact = ArchitectureClient.design(requirements, estate);
        if (artifact == null || artifact.isBlank()) {
            throw new IllegalStateException("ArchitectureClient returned no artifact");
        }
        System.out.print(artifact);
    }
}
