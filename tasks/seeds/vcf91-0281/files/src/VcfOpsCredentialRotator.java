import java.util.List;
import java.util.Map;

/**
 * Dependency-free, single-file client for the VMware Cloud Foundation
 * Operations 9.1 API.
 *
 * It performs a drain-safe rotation of an adapter credential: the replacement
 * credential is stood up alongside the outgoing one, every adapter instance is
 * repointed at it, and the outgoing credential is retired only once nothing is
 * using it any more.
 *
 * The operations, paths, base path, authorization header and request body
 * shapes this client must produce are pinned in docs/contract.json, which is
 * derived from the VCF Operations OpenAPI document recorded in
 * docs/official_sources.json.
 *
 * Only the Java SE standard library may be used. No third-party HTTP or JSON
 * dependency is available.
 */
public final class VcfOpsCredentialRotator {

    private final String baseUrl;
    private final String username;
    private final String password;
    private final String authSource;

    /**
     * @param baseUrl    origin of the VCF Operations node, e.g.
     *                   {@code https://vcfops.example.com}. The API base path
     *                   from the contract is appended to this.
     * @param username   account used to acquire a session token
     * @param password   password for {@code username}
     * @param authSource optional auth source name; {@code null} when the
     *                   account lives in the local source
     */
    public VcfOpsCredentialRotator(String baseUrl, String username,
                                   String password, String authSource) {
        this.baseUrl = baseUrl;
        this.username = username;
        this.password = password;
        this.authSource = authSource;
    }

    /**
     * Rotate {@code oldCredentialId} onto a fresh credential instance without
     * stranding in-flight work on the outgoing secret.
     *
     * @param oldCredentialId   identifier of the credential instance to retire
     * @param newCredentialName name for the replacement credential instance
     * @param newFields         credential field name to value, in order
     * @param maxDrainPolls     upper bound on drain polls before giving up
     * @return what the rotation did
     */
    public RotationResult rotate(String oldCredentialId, String newCredentialName,
                                 Map<String, String> newFields, int maxDrainPolls)
            throws Exception {
        throw new UnsupportedOperationException("rotate() is not implemented");
    }

    /** Outcome of a rotation. */
    public static final class RotationResult {

        /** Identifier of the credential instance that was created. */
        public final String newCredentialId;

        /**
         * Identifiers of the adapter instances repointed at the replacement
         * credential, in the order the client repointed them.
         */
        public final List<String> repointedAdapterIds;

        /**
         * How many times the client asked which adapter instances were still
         * using the outgoing credential, counting only the polls made after
         * the last adapter instance had been repointed.
         */
        public final int drainPolls;

        /** Whether the outgoing credential instance was deleted. */
        public final boolean oldCredentialDeleted;

        public RotationResult(String newCredentialId, List<String> repointedAdapterIds,
                              int drainPolls, boolean oldCredentialDeleted) {
            this.newCredentialId = newCredentialId;
            this.repointedAdapterIds = repointedAdapterIds;
            this.drainPolls = drainPolls;
            this.oldCredentialDeleted = oldCredentialDeleted;
        }
    }
}
