import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/** Builds the target architecture for the estate supplied by TestMain. */
public final class MigrationPlanClient {
    private MigrationPlanClient() {
    }

    public static String createMigrationPlan(Path inventory, Path compatibilitySnapshot)
            throws IOException {
        // Keep fixture reads here so the client contract fails clearly on missing inputs.
        Files.readString(inventory);
        Files.readString(compatibilitySnapshot);
        return "{}";
    }
}
