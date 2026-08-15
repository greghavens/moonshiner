import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Protected entry point used by the acceptance verifier. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <artifact-path>");
        }
        VcfArchitecture client = new VcfArchitecture();
        String architecture = client.design(
                Path.of("fixtures", "estate-inventory.json"),
                Path.of("fixtures", "compatibility-snapshot.json"));
        if (architecture == null || architecture.isBlank()) {
            throw new AssertionError("design returned no architecture JSON");
        }
        Files.writeString(Path.of(args[0]), architecture, StandardCharsets.UTF_8);
    }
}
