/**
 * Produces a deterministic VMware Cloud Foundation migration architecture.
 *
 * <p>The acceptance harness calls {@link #buildPlan(String, String)} with the
 * supplied estate inventory and pinned compatibility snapshot.</p>
 */
public final class ArchitectureClient {
    private ArchitectureClient() {
    }

    public static String buildPlan(String estateInventoryJson, String compatibilitySnapshotJson) {
        throw new UnsupportedOperationException("Migration architecture is not implemented");
    }
}
