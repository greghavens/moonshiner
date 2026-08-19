import java.io.IOException;
import java.nio.file.Path;

/** Builds the VCF migration architecture consumed by TestMain. */
public final class MigrationPlanClient {
    private MigrationPlanClient() {}

    public static String buildPlan(
            Path inventoryPath,
            Path compatibilitySnapshotPath,
            Path installerSpecPath) throws IOException {
        // Return one JSON object conforming to installer-spec.json.
        return "{\"schemaVersion\":\"1.0\"}";
    }
}
