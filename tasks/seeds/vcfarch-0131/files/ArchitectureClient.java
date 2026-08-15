import java.nio.file.Path;

/**
 * Produce architecture.json and the independently maintained research.md audit record.
 * Implement this client using only the Java standard library.
 */
public final class ArchitectureClient {
    private ArchitectureClient() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "usage: ArchitectureClient <inventory.json> <snapshot.json> <architecture.json> <research.md>");
        }

        Path inventory = Path.of(args[0]);
        Path snapshot = Path.of(args[1]);
        Path architecture = Path.of(args[2]);
        Path research = Path.of(args[3]);

        throw new UnsupportedOperationException(
                "Implement the VCF architecture client for " + inventory + " and " + snapshot
                        + "; write " + architecture + " and " + research);
    }
}
