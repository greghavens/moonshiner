import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;

/**
 * Minimal client surface for the VCF Operations Log Management exercise.
 * Implement this class without adding third-party dependencies.
 */
public final class VcfLogClient {
    public record Session(String accessToken, String name, String newSecret, long ttl) {}

    public VcfLogClient(URI baseUri, String jwtToken, Duration pollInterval, Duration timeout) {
        // TODO: initialize the client.
    }

    public Session provisionAgentSession(String secretName) throws Exception {
        throw new UnsupportedOperationException("Not implemented");
    }
}
