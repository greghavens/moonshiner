import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Minimal dependency-free harness for the single-file client. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain INVENTORY OUTPUT");
        }
        String inventory = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        String artifact = new VcfArchitectureClient().design(inventory);
        if (artifact == null || artifact.isBlank()) {
            throw new AssertionError("client returned an empty architecture");
        }
        Files.writeString(Path.of(args[1]), artifact, StandardCharsets.UTF_8);
    }
}
