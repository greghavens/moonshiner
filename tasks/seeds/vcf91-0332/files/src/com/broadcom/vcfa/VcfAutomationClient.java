package com.broadcom.vcfa;

import java.io.IOException;
import java.util.List;

/**
 * A client for the three VCF Automation 9.1 operations named in {@code docs/contract.json}.
 *
 * <p>This is the only file you need to change. Everything the wire format has to satisfy is written
 * down in {@code docs/contract.json}, which was transcribed from the xAPIs reference pages listed in
 * {@code docs/official_sources.json}.
 *
 * <p>The appliance issues a short-lived access token. It will expire partway through a run, and the
 * Deployment API will answer 401 for the request that hits the expiry. Recover from that without
 * discarding the pages already fetched.
 */
public final class VcfAutomationClient {

    private final String baseUrl;
    private final String refreshToken;

    /**
     * @param baseUrl      origin of the appliance, e.g. {@code http://127.0.0.1:8443}, no trailing slash
     * @param refreshToken the long-lived refresh token this client was provisioned with
     */
    public VcfAutomationClient(String baseUrl, String refreshToken) {
        this.baseUrl = baseUrl;
        this.refreshToken = refreshToken;
    }

    /** Origin of the appliance this client talks to. */
    public String baseUrl() {
        return baseUrl;
    }

    /** The refresh token this client was provisioned with. */
    public String refreshToken() {
        return refreshToken;
    }

    /**
     * Fetches every page of {@code listDeployments} and returns the deployments in server order.
     *
     * @param pageSize value for the {@code size} query parameter
     * @return every deployment, in the order the server returned them, with no duplicates and none
     *     dropped
     */
    public List<Deployment> listAllDeployments(int pageSize) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("listAllDeployments is not implemented yet");
    }

    /**
     * Updates one deployment through {@code patchDeployment}.
     *
     * <p>A null argument means the caller is not changing that field.
     *
     * @param deploymentId the deployment to update
     * @param name         new name, or null to leave it alone
     * @param description  new description, or null to leave it alone
     * @param iconId       new icon id, or null to leave it alone
     * @return the deployment as the server returned it after the update
     */
    public Deployment updateDeployment(String deploymentId, String name, String description, String iconId)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("updateDeployment is not implemented yet");
    }

    /** The subset of the Deployment object this project cares about. */
    public record Deployment(String id, String name, String description, String status) {}
}
