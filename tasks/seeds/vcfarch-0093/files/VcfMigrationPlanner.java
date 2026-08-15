import java.nio.file.Path;

/**
 * Brownfield VCF architecture client.
 *
 * Invocation: VcfMigrationPlanner <inventory.json> <compatibility-snapshot.json> <output.json>
 */
public final class VcfMigrationPlanner {
    private VcfMigrationPlanner() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "usage: VcfMigrationPlanner <inventory.json> <compatibility-snapshot.json> <output.json>");
        }

        Path inventory = Path.of(args[0]);
        Path compatibilitySnapshot = Path.of(args[1]);
        Path output = Path.of(args[2]);
        writePlan(inventory, compatibilitySnapshot, output);
    }

    static void writePlan(Path inventory, Path compatibilitySnapshot, Path output) throws Exception {
        throw new UnsupportedOperationException("Implement the VCF migration architecture generator");
    }
}
