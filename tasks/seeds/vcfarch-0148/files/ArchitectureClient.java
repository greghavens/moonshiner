/**
 * Implement the two methods below.  The research record is a separate JSON
 * document so all verification remains offline and architecture content never
 * depends on mutable live web content.
 */
public final class ArchitectureClient {
    private ArchitectureClient() {
    }

    public static String architecture() {
        return "{}";
    }

    public static String researchRecord() {
        return "[]";
    }
}
