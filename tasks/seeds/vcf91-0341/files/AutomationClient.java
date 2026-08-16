import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;
import java.util.Objects;

/** Minimal VCF Automation 9.1 Project Service client. */
public final class AutomationClient {
    private final URI baseUri;
    private final String bearerToken;
    private final int pageSize;
    private final HttpClient httpClient;

    public AutomationClient(String baseUrl, String bearerToken, int pageSize) {
        Objects.requireNonNull(baseUrl, "baseUrl");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        if (pageSize < 1) {
            throw new IllegalArgumentException("pageSize must be positive");
        }
        this.baseUri = URI.create(baseUrl.endsWith("/")
                ? baseUrl.substring(0, baseUrl.length() - 1)
                : baseUrl);
        this.pageSize = pageSize;
        this.httpClient = HttpClient.newHttpClient();
    }

    public record Project(String id, String name) {
        public Project {
            Objects.requireNonNull(id, "id");
            Objects.requireNonNull(name, "name");
        }
    }

    /** Retrieves all project pages and returns projects in stable name/id order. */
    public List<Project> listAllProjects() throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
