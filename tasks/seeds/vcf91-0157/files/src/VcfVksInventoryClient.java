import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

/**
 * Single-file VCF 9.1 vSphere Supervisor and VKS inventory client.
 */
public final class VcfVksInventoryClient {
    public record Credentials(String vcenterSessionId, String kubernetesAccessToken) {
    }

    @FunctionalInterface
    public interface CredentialRefresher {
        Credentials refresh(Credentials expired) throws Exception;
    }

    public record ClusterRecord(
            String supervisorNamespace,
            URI supervisorEndpoint,
            String name,
            String uid,
            String kubernetesVersion,
            String phase) {
    }

    public VcfVksInventoryClient(
            URI vcenterApiBase,
            Credentials initialCredentials,
            CredentialRefresher credentialRefresher,
            Duration timeout,
            HttpClient httpClient) {
        throw new UnsupportedOperationException("TODO");
    }

    public List<ClusterRecord> listInventory()
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
