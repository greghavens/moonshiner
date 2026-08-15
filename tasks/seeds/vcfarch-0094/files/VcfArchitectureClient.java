import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Builds the checked-in estate's VCF 9.1 migration architecture. */
public final class VcfArchitectureClient {
    private VcfArchitectureClient() {
    }

    public static String buildMigrationPlan(Path inventoryPath, Path compatibilitySnapshotPath)
            throws IOException {
        throw new UnsupportedOperationException("TODO: build the migration architecture");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: VcfArchitectureClient <inventory.json> <compatibility-snapshot.json> <output.json>");
        }
        String plan = buildMigrationPlan(Path.of(args[0]), Path.of(args[1]));
        Files.writeString(Path.of(args[2]), plan, StandardCharsets.UTF_8);
    }
}
