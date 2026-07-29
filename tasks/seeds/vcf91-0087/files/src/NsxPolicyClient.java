import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/**
 * Minimal VCF 9.1 NSX Policy client used by the exercise harness.
 *
 * <p>This file intentionally has no package and may use only JDK classes.
 */
public final class NsxPolicyClient {
    public record PolicySummary(String id, String displayName) {
        public PolicySummary {
            Objects.requireNonNull(id, "id");
            Objects.requireNonNull(displayName, "displayName");
        }
    }

    @FunctionalInterface
    public interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    private final URI baseUri;
    private final String username;
    private final String password;
    private final HttpClient httpClient;
    private final Sleeper sleeper;

    public NsxPolicyClient(
            URI baseUri,
            String username,
            String password,
            HttpClient httpClient,
            Sleeper sleeper) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.username = Objects.requireNonNull(username, "username");
        this.password = Objects.requireNonNull(password, "password");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
        this.sleeper = Objects.requireNonNull(sleeper, "sleeper");
    }

    public List<PolicySummary> upsertWaitAndList(
            String orgId,
            String projectId,
            String domainId,
            String policyId,
            String displayName,
            Duration timeout,
            Duration pollInterval) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the NSX Policy workflow");
    }
}
