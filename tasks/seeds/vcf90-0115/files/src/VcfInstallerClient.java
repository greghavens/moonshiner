import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;

/** Minimal client for the VCF Installer getTasks operation. */
public final class VcfInstallerClient {
    public record Task(String id, String name, String status, String creationTimestamp) {}

    private final URI baseUri;
    private final int pageSize;
    private final HttpClient httpClient;

    public VcfInstallerClient(URI baseUri, int pageSize) {
        this.baseUri = baseUri;
        this.pageSize = pageSize;
        this.httpClient = HttpClient.newHttpClient();
    }

    /**
     * Retrieves the complete task collection.
     *
     * @return every task in stable creationTimestamp/id order
     */
    public List<Task> listAllTasks() throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }
}
