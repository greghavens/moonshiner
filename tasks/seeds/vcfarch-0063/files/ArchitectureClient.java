/**
 * Produces a VCF architecture artifact from the supplied local fixtures.
 *
 * Keep this implementation dependency-free: TestMain compiles it with only the
 * Java standard library available.
 */
public final class ArchitectureClient {
    private ArchitectureClient() {
    }

    public static String createArchitecture(String estateInventoryJson,
                                            String compatibilitySnapshotJson) {
        throw new UnsupportedOperationException("Implement the architecture client");
    }
}
