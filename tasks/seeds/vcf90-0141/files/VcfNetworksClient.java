import java.io.IOException;
import java.net.URI;
import java.time.Duration;

/** Client exercise: implement this class without adding external dependencies. */
public final class VcfNetworksClient {
    public VcfNetworksClient(URI apiBaseUri, String token, Duration pollInterval) {
        throw new UnsupportedOperationException("TODO");
    }

    public CertificateUpdateStatus updateCertificateAndWait(
            String certificateId,
            String certificatePem,
            String privateKeyPem,
            String chainPem) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }

    public record CertificateUpdateStatus(
            String id,
            String name,
            String status,
            String errorMessage) {
    }
}
