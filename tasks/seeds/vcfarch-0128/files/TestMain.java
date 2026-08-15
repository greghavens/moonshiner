import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Protected launcher for the single-file architecture client. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        String inventory = Files.readString(
                Path.of("fixtures", "estate-inventory.json"), StandardCharsets.UTF_8);
        String artifact = ArchitectureClient.design(inventory);
        if (artifact == null || artifact.isBlank()) {
            throw new AssertionError("ArchitectureClient.design returned no artifact");
        }
        System.out.print(artifact);
    }
}
