import java.nio.file.Path;

public final class EstateMigrationClient {
    private EstateMigrationClient() {}

    public static String buildPlan(
            Path inventoryPath,
            Path installerSpecPath,
            Path compatibilitySnapshotPath) throws Exception {
        throw new UnsupportedOperationException("Implement buildPlan");
    }
}
