import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/**
 * Minimal client for the VCF 9.1 SDDC Manager operations selected in
 * docs/contract.json. This exercise intentionally uses only the Java standard
 * library.
 */
public final class VcfIdentityProviderClient {
    private final URI baseUri;
    private final String accessToken;
    private final Duration requestTimeout;
    private final HttpClient http;

    /**
     * Focused projection of IdentityProviderSpec. The other contract optionals
     * (ldap, oidc, and fedIdpSpec) are deliberately unset in this workflow.
     */
    public record IdentityProviderSpec(
            String name,
            String type,
            List<String> certChain) {
    }

    public VcfIdentityProviderClient(
            URI baseUri,
            String accessToken,
            Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Runs the identity-provider precheck and performs the enrollment only when
     * the precheck status is SUCCESS.
     *
     * @return true when the provider was enrolled, or false when a contract-valid
     *         WARNING/FAILURE precheck gated the mutation
     */
    public boolean addExternalIdentityProviderIfSafe(
            IdentityProviderSpec provider,
            String precheckType) throws IOException, InterruptedException {
        // TODO: implement the spec-derived precheck gate and enrollment.
        throw new UnsupportedOperationException("Not implemented");
    }
}
