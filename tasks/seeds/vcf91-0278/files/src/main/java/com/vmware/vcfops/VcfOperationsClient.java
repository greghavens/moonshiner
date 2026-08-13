package com.vmware.vcfops;

/**
 * Client for the VMware Cloud Foundation Operations API (VCF 9.1).
 *
 * <p>TODO: implement. Everything lives in this one file — no build system, no
 * third-party dependencies, JDK only. See README.md for the contract you have to
 * derive and the wire shape the harness expects.
 */
public final class VcfOperationsClient {

    private final String baseUrl;

    /**
     * @param baseUrl scheme, host and port of the VCF Operations node, with no
     *                trailing slash and no path (e.g. {@code http://127.0.0.1:8443}).
     */
    public VcfOperationsClient(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    /**
     * Acquires an API token and stores it as this client's current credential.
     *
     * @param authSource optional auth source name; {@code null} means "not specified"
     * @return the token string returned by the server
     */
    public String acquireToken(String username, String password, String authSource) throws Exception {
        throw new UnsupportedOperationException("not implemented");
    }

    /**
     * Reconciles a custom group so that exactly one group with the given name
     * exists with the requested membership rule. Safe to call repeatedly.
     *
     * @param policy optional policy identifier; {@code null} means "not specified"
     * @return the identifier of the reconciled group
     */
    public String ensureCustomGroup(String name,
                                    String adapterKindKey,
                                    String resourceKindKey,
                                    boolean autoResolveMembership,
                                    String policy,
                                    String ruleAdapterKind,
                                    String ruleResourceKind,
                                    String nameContains) throws Exception {
        throw new UnsupportedOperationException("not implemented");
    }

    /** {@code "created"} or {@code "updated"} — what the last reconcile did. */
    public String lastAction() {
        throw new UnsupportedOperationException("not implemented");
    }
}
