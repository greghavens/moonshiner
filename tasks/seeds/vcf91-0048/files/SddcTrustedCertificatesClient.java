import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;
import java.util.Objects;

/**
 * Minimal VCF 9.1 SDDC Manager trusted-certificate client.
 *
 * <p>Implement this file against the protected contract in docs/contract.json.
 */
public final class SddcTrustedCertificatesClient {
    public record TrustedCertificate(String alias, String certificate) {
        public TrustedCertificate {
            Objects.requireNonNull(alias, "alias");
            Objects.requireNonNull(certificate, "certificate");
        }
    }

    private final URI baseUri;
    private final String bearerToken;
    private final HttpClient httpClient;

    public SddcTrustedCertificatesClient(
            URI baseUri, String bearerToken, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    public List<TrustedCertificate> listTrustedCertificates()
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement from docs/contract.json");
    }

    public List<TrustedCertificate> ensureTrustedCertificate(String pemCertificate)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement retry-safe ensure-present");
    }
}
