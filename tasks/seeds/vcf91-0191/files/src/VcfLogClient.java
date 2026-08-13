import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;

/**
 * Minimal client surface for the VCF Operations Log Management exercise.
 * Implement this class without adding third-party dependencies.
 */
public final class VcfLogClient {
    public record AgentGroup(String id, String name, boolean autoUpdate) {}

    public VcfLogClient(URI baseUri, String jwtToken, int pageSize) {
        // TODO: initialize the client.
    }

    public List<AgentGroup> listAllAgentGroups() throws Exception {
        throw new UnsupportedOperationException("Not implemented");
    }
}
