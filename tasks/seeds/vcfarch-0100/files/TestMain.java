import java.nio.file.Path;

/** Protected harness for the single-file client. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String artifact = MigrationPlanClient.createMigrationPlan(
                Path.of("fixtures", "estate-inventory.json"),
                Path.of("fixtures", "compatibility-snapshot.json"));
        if (artifact == null || artifact.isBlank()) {
            throw new AssertionError("MigrationPlanClient returned no artifact");
        }
        System.out.print(artifact);
    }
}
