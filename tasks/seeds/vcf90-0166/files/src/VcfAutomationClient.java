import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;

/** Minimal client for the VCF Automation deployment collection. */
public final class VcfAutomationClient {
    private final URI baseUri;
    private final String authorization;
    private final HttpClient httpClient;

    public VcfAutomationClient(URI baseUri, String authorization) {
        this.baseUri = baseUri;
        this.authorization = authorization;
        this.httpClient = HttpClient.newHttpClient();
    }

    public record Deployment(String id, String name, String projectId) {}

    /** Returns every deployment, sorted by id. */
    public List<Deployment> listAllDeployments(int pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement paginated Get Deployments");
    }
}
