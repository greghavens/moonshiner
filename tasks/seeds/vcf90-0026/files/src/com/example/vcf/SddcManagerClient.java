package com.example.vcf;

import java.util.Map;

/**
 * A single-file client for the VMware Cloud Foundation 9.0 SDDC Manager REST API.
 *
 * <p>Everything the client needs lives in this file. {@code com.example.vcf.harness.MiniJson} is
 * available on the classpath if you want a JSON reader/writer; there are no other dependencies and
 * no network access at build time.
 *
 * <p>TODO: implement {@link #downloadPendingSddcManagerBundle()}.
 */
public final class SddcManagerClient {

    private final String baseUrl;
    private final String username;
    private final String password;

    /**
     * @param baseUrl origin of the SDDC Manager appliance, e.g. {@code http://127.0.0.1:54321}, with
     *     no trailing slash and no path
     * @param username SSO user name to sign in with
     * @param password password for {@code username}
     */
    public SddcManagerClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl;
        this.username = username;
        this.password = password;
    }

    /**
     * Signs in, finds the one SDDC Manager bundle that is still waiting to be downloaded, starts its
     * download and follows the resulting task until it reaches a terminal status.
     *
     * <p>The access token minted at sign-in expires part way through this workflow. When that
     * happens the run must recover by refreshing the token and carrying on from where it was — no
     * repeated sign-in, and no restarting the download that is already running.
     *
     * @return a map with these keys:
     *     <ul>
     *       <li>{@code bundleId} — id of the bundle whose download was started
     *       <li>{@code taskId} — id of the task returned when the download was started
     *       <li>{@code taskStatus} — terminal status the task reached
     *       <li>{@code accessTokenRefreshes} — how many times the access token had to be refreshed
     *     </ul>
     *
     * @throws Exception if the workflow cannot be completed
     */
    public Map<String, Object> downloadPendingSddcManagerBundle() throws Exception {
        throw new UnsupportedOperationException("not implemented yet");
    }

    public String baseUrl() {
        return baseUrl;
    }

    public String username() {
        return username;
    }

    public String password() {
        return password;
    }
}
