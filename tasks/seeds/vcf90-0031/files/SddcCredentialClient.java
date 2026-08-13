import java.util.List;

/**
 * Rotates the ESXi SSH passwords held by an SDDC Manager appliance.
 *
 * <p>The five operations this client is allowed to use are described in {@code docs/contract.json},
 * which is transcribed from the VMware Cloud Foundation 9.0 SDDC Manager OpenAPI document.
 *
 * <p>Not implemented yet.
 */
public final class SddcCredentialClient implements AutoCloseable {

    private final String baseUrl;
    private final String username;
    private final String password;

    /**
     * @param baseUrl  scheme, host and port of the appliance, with no trailing slash
     * @param username the SSO account used to mint the first access token
     * @param password the password for {@code username}
     */
    public SddcCredentialClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl;
        this.username = username;
        this.password = password;
    }

    /**
     * Rotates the SSH password of the USER account on each of the given ESXi hosts and follows the
     * resulting credentials task to a terminal status.
     *
     * @param resourceNames the ESXi resource names to rotate, in the order they should appear in the
     *                      rotation spec
     * @return what the run produced
     */
    public RotationResult rotateSshPasswords(List<String> resourceNames) throws Exception {
        throw new UnsupportedOperationException("SddcCredentialClient.rotateSshPasswords is not implemented");
    }

    @Override
    public void close() {
        // Release anything the implementation allocated.
    }
}
