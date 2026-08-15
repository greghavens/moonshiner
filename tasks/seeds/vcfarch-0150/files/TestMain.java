import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Fixed harness for the single-file client. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: TestMain <estate-inventory.json> <compatibility-snapshot.json>");
        }
        String inventory = Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        String compatibility = Files.readString(Path.of(args[1]), StandardCharsets.UTF_8);
        String artifact = ArchitectureClient.build(inventory, compatibility);
        if (artifact == null) {
            throw new IllegalStateException("ArchitectureClient returned null");
        }
        System.out.print(artifact);
    }
}
