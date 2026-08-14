import java.io.IOException;
import java.net.URI;
import java.util.Objects;

/** Standard-library client for the task's pinned VCF Operations for Networks contract. */
public final class VcfOperationsNetworksClient {
    public record Application(String entityId, String name, String entityType) {}

    private final URI applianceBaseUri;
    private final String username;
    private final String password;

    public VcfOperationsNetworksClient(URI applianceBaseUri, String username, String password) {
        this.applianceBaseUri = Objects.requireNonNull(applianceBaseUri, "applianceBaseUri");
        this.username = Objects.requireNonNull(username, "username");
        this.password = Objects.requireNonNull(password, "password");
    }

    public Application createApplicationAndFetch(String name)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("VCF Operations for Networks client not implemented");
    }
}
