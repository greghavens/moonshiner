import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;
import java.util.Objects;

/**
 * Minimal client for the operation subset documented in docs/contract.json.
 *
 * Keep this client dependency-free and in this single source file.
 */
public final class VcfSddcClient {
    private final URI baseUri;
    private final String username;
    private final String password;
    private final HttpClient httpClient;

    public record Domain(String id, String name) {
        public Domain {
            Objects.requireNonNull(id, "id");
            Objects.requireNonNull(name, "name");
        }
    }

    public VcfSddcClient(URI baseUri, String username, String password) {
        this(baseUri, username, password, HttpClient.newHttpClient());
    }

    VcfSddcClient(URI baseUri, String username, String password, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.username = Objects.requireNonNull(username, "username");
        this.password = Objects.requireNonNull(password, "password");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    /**
     * Authenticates, retrieves every domain page, refreshes an expired access token,
     * and returns the complete collection sorted by domain name and then id.
     */
    public List<Domain> listDomainsSorted() throws IOException, InterruptedException {
        return List.of();
    }
}
