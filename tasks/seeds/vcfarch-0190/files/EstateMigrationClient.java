import java.nio.file.Path;

public final class EstateMigrationClient {
    private EstateMigrationClient() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "usage: EstateMigrationClient <inventory.json> <snapshot.json> <installer-spec.json> <output.json>");
        }

        Path inventory = Path.of(args[0]);
        Path snapshot = Path.of(args[1]);
        Path installerSpec = Path.of(args[2]);
        Path output = Path.of(args[3]);

        // Implement the architecture generator here.
        throw new UnsupportedOperationException(
                "migration architecture generation is not implemented: "
                        + inventory + ", " + snapshot + ", " + installerSpec + " -> " + output);
    }
}
