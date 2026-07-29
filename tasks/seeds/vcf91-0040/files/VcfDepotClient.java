import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Objects;

/**
 * Minimal client for the VCF 9.1 SDDC Manager operation selected in
 * docs/contract.json. This exercise intentionally uses only the Java standard
 * library.
 */
public final class VcfDepotClient {
    private final URI baseUri;
    private final String accessToken;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record DepotAccount(
            String username,
            String password,
            String status,
            String message,
            String downloadToken,
            String downloadActivationCode) {
    }

    public VcfDepotClient(URI baseUri, String accessToken, Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.accessToken = Objects.requireNonNull(accessToken, "accessToken");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Replaces the online VMware depot account with updateDepotSettings.
     *
     * <p>The selected operation is PUT, so one identical retry is safe when the
     * first attempt returns the documented transient HTTP 500 used by the
     * protected scenario.
     */
    public void updateDepotSettings(DepotAccount account)
            throws IOException, InterruptedException {
        // TODO: serialize the focused DepotSettings body and perform the retry-safe PUT.
        throw new UnsupportedOperationException("Not implemented");
    }
}
