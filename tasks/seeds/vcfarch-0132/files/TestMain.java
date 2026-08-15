import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Protected acceptance entry point. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Path root = Path.of(".").toAbsolutePath().normalize();
        String inventory = Files.readString(
                root.resolve("fixtures/estate-inventory.json"),
                StandardCharsets.UTF_8);
        String artifact = VcfArchitectureClient.design(inventory);
        if (artifact == null || artifact.isBlank()) {
            throw new AssertionError("VcfArchitectureClient.design returned no artifact");
        }
        System.out.print(artifact);
    }
}
