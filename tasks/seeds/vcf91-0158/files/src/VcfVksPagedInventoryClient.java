import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

/**
 * Single-file VCF 9.1 vSphere Supervisor and paginated VKS inventory client.
 */
public final class VcfVksPagedInventoryClient {
    public record ClusterRecord(
            String supervisorNamespace,
            URI supervisorEndpoint,
            String name,
            String uid,
            String kubernetesVersion,
            String phase) {
    }

    public VcfVksPagedInventoryClient(
            URI vcenterApiBase,
            String vcenterSessionId,
            String kubernetesAccessToken,
            int pageSize,
            Duration timeout,
            HttpClient httpClient) {
        throw new UnsupportedOperationException("TODO");
    }

    public List<ClusterRecord> listInventory()
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
