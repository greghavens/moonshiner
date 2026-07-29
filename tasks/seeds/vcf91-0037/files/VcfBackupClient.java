import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Objects;

/**
 * Minimal client for the VCF 9.1 SDDC Manager operations selected in
 * docs/contract.json. This exercise intentionally uses only the Java standard
 * library.
 */
public final class VcfBackupClient {
    private final URI baseUri;
    private final String accessToken;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record BackupLocation(
            String server,
            int port,
            String protocol,
            String username,
            String directoryPath,
            String password,
            String sshFingerprint) {
    }

    public record Task(
            String id,
            String name,
            String status,
            String creationTimestamp,
            String completionTimestamp) {
    }

    public VcfBackupClient(URI baseUri, String accessToken, Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Starts updateBackupConfiguration and follows its returned task through
     * getTask until a terminal task status is observed.
     */
    public Task updateBackupConfigurationAndWait(
            BackupLocation location,
            int maxPolls,
            Duration pollInterval) throws IOException, InterruptedException {
        // TODO: implement the spec-derived request body, PATCH, and polling loop.
        throw new UnsupportedOperationException("Not implemented");
    }
}
