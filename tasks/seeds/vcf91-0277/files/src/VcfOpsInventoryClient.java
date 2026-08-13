import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;

/**
 * Dependency-free, single-file Java client for the VMware Cloud Foundation Operations
 * API 9.1 (the Operations API itself, not the Log Management API that ships beside it).
 *
 * <p>The wire contract this client must honour is {@code docs/contract.json}, derived
 * from the OpenAPI document recorded in {@code docs/official_sources.json}. It names
 * exactly two operations:
 *
 * <ul>
 *   <li>{@code acquireToken} - {@code POST /suite-api/api/auth/token/acquire}</li>
 *   <li>{@code getResources} - {@code GET /suite-api/api/resources}</li>
 * </ul>
 *
 * <p>Only the JDK may be used. Keep the whole implementation in this one file.
 */
public final class VcfOpsInventoryClient {

    /** One projected row of the {@code getResources} collection. */
    public record Resource(String identifier, String name, String adapterKindKey,
                           String resourceKindKey, String resourceHealth) {}

    private final URI baseUri;
    private final HttpClient http;

    /**
     * @param baseUri scheme, host and port of the VCF Operations appliance, with no
     *                path component; the contract's {@code basePath} is appended by
     *                this client.
     */
    public VcfOpsInventoryClient(URI baseUri) {
        this.baseUri = baseUri;
        this.http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();
    }

    /**
     * Invokes {@code acquireToken} and remembers the credential returned by the service.
     *
     * @param username  required
     * @param password  required
     * @param authSource optional; {@code null} or blank means the caller did not set it
     * @return the {@code token} property of the {@code auth-token} response
     */
    public String authenticate(String username, String password, String authSource)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("authenticate is not implemented yet");
    }

    /**
     * Invokes {@code getResources} until the paginated collection has been retrieved
     * completely, then returns it in a stable total order.
     *
     * @param names         optional {@code name} filter; {@code null} or empty means unset
     * @param adapterKinds  optional {@code adapterKind} filter; {@code null} or empty means unset
     * @param resourceKinds optional {@code resourceKind} filter; {@code null} or empty means unset
     * @param pageSize      the {@code pageSize} to request, at least 1
     */
    public List<Resource> listResources(List<String> names, List<String> adapterKinds,
                                        List<String> resourceKinds, int pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("listResources is not implemented yet");
    }
}
