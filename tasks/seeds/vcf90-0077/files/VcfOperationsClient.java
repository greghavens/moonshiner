import java.io.IOException;
import java.net.URI;
import java.util.List;

/**
 * Minimal VCF Operations 9.0 client used by the supplied integration harness.
 */
public final class VcfOperationsClient {
    public VcfOperationsClient(
            URI baseUri, String username, String password, String authSource) {
        throw new UnsupportedOperationException("Not implemented");
    }

    /**
     * Returns every matching resource identifier in server page order.
     * Nullable filters are unset and must be omitted from the request.
     */
    public List<String> listResourceIds(
            String adapterKind, String resourceKind, String name, int pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }
}
