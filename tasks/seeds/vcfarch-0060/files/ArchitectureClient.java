import java.nio.file.Files;
import java.nio.file.Path;

/** Produces machine-readable VCF architecture artifacts. */
public final class ArchitectureClient {
    private ArchitectureClient() {}

    /** Return a VCF Installer SddcSpec JSON object for the greenfield design. */
    public static String greenfieldSpec() {
        throw new UnsupportedOperationException("greenfieldSpec is not implemented");
    }

    /** Return the ordered migration-plan JSON object for the supplied fixture data. */
    public static String migrationPlan(String inventoryJson, String compatibilitySnapshotJson) {
        throw new UnsupportedOperationException("migrationPlan is not implemented");
    }

    /** Return JSON recording the live Broadcom sources actually consulted. */
    public static String researchRecord() {
        throw new UnsupportedOperationException("researchRecord is not implemented");
    }

    public static void main(String[] args) throws Exception {
        throw new UnsupportedOperationException("CLI is not implemented");
    }
}
