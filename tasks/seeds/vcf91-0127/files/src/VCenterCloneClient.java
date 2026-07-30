import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/**
 * Minimal VCF 9.1 vSphere Automation API client used by the exercise harness.
 *
 * <p>This file intentionally has no package and may use only JDK classes.
 */
public final class VCenterCloneClient {
    public record VmSummary(String vm, String name, String powerState) {
        public VmSummary {
            Objects.requireNonNull(vm, "vm");
            Objects.requireNonNull(name, "name");
            Objects.requireNonNull(powerState, "powerState");
        }
    }

    @FunctionalInterface
    public interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    private final URI baseUri;
    private final String sessionToken;
    private final HttpClient httpClient;
    private final Sleeper sleeper;

    public VCenterCloneClient(
            URI baseUri,
            String sessionToken,
            HttpClient httpClient,
            Sleeper sleeper) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.sessionToken = Objects.requireNonNull(sessionToken, "sessionToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
        this.sleeper = Objects.requireNonNull(sleeper, "sleeper");
    }

    public List<VmSummary> cloneWaitAndList(
            String sourceVm,
            String cloneName,
            Duration timeout,
            Duration pollInterval) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the vCenter workflow");
    }
}
