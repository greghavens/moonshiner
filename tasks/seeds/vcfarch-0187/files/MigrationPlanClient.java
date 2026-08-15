import java.nio.file.Files;
import java.nio.file.Path;

/** Builds the machine-readable VCF migration architecture. */
public final class MigrationPlanClient {
    private MigrationPlanClient() {}

    public static String buildPlan(String inventoryJson, String compatibilitySnapshotJson) {
        throw new UnsupportedOperationException("Implement buildPlan");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: java MigrationPlanClient <estate-inventory.json> <compatibility-snapshot.json>");
        }
        String inventory = Files.readString(Path.of(args[0]));
        String snapshot = Files.readString(Path.of(args[1]));
        System.out.println(buildPlan(inventory, snapshot));
    }
}
