import java.io.IOException;
import java.net.URI;
import java.util.Objects;

/** Focused VCF Installer 9.0 client. */
public final class VcfInstallerClient {
    private final URI baseUri;

    public record Task(String id, String name, String status, String creationTimestamp) {}

    public VcfInstallerClient(URI baseUri) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
    }

    public Task downloadBundleNowAndWait(String bundleId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
