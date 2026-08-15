import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpResponse;
import java.util.Objects;

public final class VcfAutomationClient {
    private final HttpClient httpClient;
    private final URI baseUri;
    private final String bearerToken;

    public VcfAutomationClient(HttpClient httpClient, URI baseUri, String bearerToken) {
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
    }

    public HttpResponse<String> patchDeployment(
            String deploymentId,
            String description,
            String iconId,
            String name) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the VCF Automation request");
    }
}
