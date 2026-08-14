import java.io.IOException;
import java.net.URI;
import java.util.List;

/** Client implementation exercise for the pinned VCF Installer contract. */
public final class VcfInstallerClient {
    public VcfInstallerClient(URI baseUri) {
        // TODO: retain the endpoint and initialize a standard Java HTTP client.
    }

    public List<String> listAllTaskIds(String username, String password, int pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement VCF Installer client");
    }
}
