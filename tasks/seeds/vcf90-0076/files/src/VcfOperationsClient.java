import java.io.IOException;
import java.net.URI;
import java.time.Duration;
import java.util.Objects;

/** Minimal VCF Operations action client. */
public final class VcfOperationsClient {
    private final URI baseUri;
    private final String authorization;
    private final Duration pollInterval;

    public VcfOperationsClient(URI baseUri, String authorization, Duration pollInterval) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.authorization = Objects.requireNonNull(authorization, "authorization");
        this.pollInterval = Objects.requireNonNull(pollInterval, "pollInterval");
    }

    public ActionStatus performActionAndWait(String actionId, String resourceId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("not implemented");
    }

    public record ActionStatus(String taskId, String state) {}
}
