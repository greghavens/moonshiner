import java.io.IOException;
import java.net.URI;
import java.util.Objects;

/** Minimal client for the pinned VCF Installer operation in docs/contract.json. */
public final class VcfInstallerClient {
    private final URI baseUri;

    public VcfInstallerClient(URI baseUri) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
    }

    public String updateDepotSettings(String downloadToken)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }
}
