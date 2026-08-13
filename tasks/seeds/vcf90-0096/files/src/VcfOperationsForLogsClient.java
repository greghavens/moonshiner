import java.io.IOException;
import java.net.URI;

/**
 * Minimal VCF Operations for Logs 9.0 client used by the integration harness.
 */
public final class VcfOperationsForLogsClient {
    private final URI baseUri;

    public VcfOperationsForLogsClient(URI baseUri) {
        this.baseUri = baseUri;
    }

    public JoinResult joinAndWait(String masterFQDN) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }

    public record JoinResult(
            String masterAddress,
            String workerAddress,
            int workerPort,
            String workerToken,
            int masterUiPort) {
    }
}
