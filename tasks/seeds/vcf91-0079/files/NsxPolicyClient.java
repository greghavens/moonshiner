import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.Objects;

/**
 * Minimal NSX Policy client. Implement patchSegmentAndWait using docs/contract.json.
 */
public final class NsxPolicyClient {
    @FunctionalInterface
    public interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    private final URI baseUri;
    private final String username;
    private final String password;
    private final Duration requestTimeout;
    private final Sleeper sleeper;

    public NsxPolicyClient(
            URI baseUri,
            String username,
            String password,
            Duration requestTimeout,
            Sleeper sleeper) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.username = Objects.requireNonNull(username, "username");
        this.password = Objects.requireNonNull(password, "password");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.sleeper = Objects.requireNonNull(sleeper, "sleeper");
    }

    /**
     * Creates or updates a Tier-1 segment and waits for its intent to be realized.
     *
     * @return the terminal string {@code REALIZED}
     */
    public String patchSegmentAndWait(
            String tier1Id,
            String segmentId,
            String displayName,
            String gatewayAddress,
            String description,
            String dhcpConfigPath,
            Duration pollInterval,
            int maxPolls) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
