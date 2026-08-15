import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String inventory = Files.readString(
                Path.of("fixtures", "estate-inventory.json"), StandardCharsets.UTF_8);
        String snapshot = Files.readString(
                Path.of("fixtures", "compatibility-snapshot.json"), StandardCharsets.UTF_8);
        String artifact = ArchitectureClient.buildArchitecture(inventory, snapshot);
        if (artifact == null) {
            throw new IllegalStateException("ArchitectureClient returned null");
        }
        System.out.print(artifact);
    }
}
