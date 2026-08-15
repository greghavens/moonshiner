import java.nio.file.Files;
import java.nio.file.Path;

/** Generates the machine-readable target architecture for the supplied estate. */
public final class VcfMigrationPlanner {
    private VcfMigrationPlanner() {
    }

    public static String generate(String inventoryJson) {
        throw new UnsupportedOperationException("TODO: generate the VCF migration architecture");
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: VcfMigrationPlanner <estate-inventory.json>");
        }
        System.out.print(generate(Files.readString(Path.of(args[0]))));
    }
}
