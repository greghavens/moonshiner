import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Builds the migration architecture artifact from the protected estate inputs. */
public final class MigrationPlanClient {
    private MigrationPlanClient() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: MigrationPlanClient <inventory.json> <snapshot.json> <output.json>");
        }

        // Read both inputs so missing or unreadable authority files fail explicitly.
        Files.readString(Path.of(args[0]), StandardCharsets.UTF_8);
        Files.readString(Path.of(args[1]), StandardCharsets.UTF_8);

        // The architecture implementation belongs here.
        Files.writeString(Path.of(args[2]), "{\"schemaVersion\":\"1.0\"}\n", StandardCharsets.UTF_8);
    }
}
