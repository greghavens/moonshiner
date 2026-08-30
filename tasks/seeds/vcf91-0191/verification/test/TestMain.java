import java.net.URI;
import java.util.List;

public final class TestMain {
    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain BASE_URI");
        }

        VcfLogClient client = new VcfLogClient(
                URI.create(args[0]), "integration-jwt-91", 2);
        List<VcfLogClient.AgentGroup> groups = client.listAllAgentGroups();

        List<VcfLogClient.AgentGroup> expected = List.of(
                new VcfLogClient.AgentGroup("ag-10", "Alpha", true),
                new VcfLogClient.AgentGroup("ag-20", "Alpha", false),
                new VcfLogClient.AgentGroup("ag-25", "Kappa", false),
                new VcfLogClient.AgentGroup("ag-30", "Zulu", false),
                new VcfLogClient.AgentGroup("ag-40", "Éclair", true));
        require(expected.equals(groups), "collection is incomplete or not stably ordered: " + groups);

        try {
            groups.add(new VcfLogClient.AgentGroup("ag-99", "mutated", false));
            throw new AssertionError("returned list must be unmodifiable");
        } catch (UnsupportedOperationException expectedFailure) {
            // Expected.
        }

        System.out.println("TestMain: all agent groups collected in stable order");
    }
}
