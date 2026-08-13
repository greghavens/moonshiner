/**
 * Client for the VCF 9.1 SDDC LCM service.
 *
 * Drives a fleet component upgrade against the operations named in docs/contract.json and
 * returns a rollout report in the format described in docs/client_api.md.
 *
 * Not implemented yet.
 */
public final class VcfLcmClient {

    private VcfLcmClient() {
    }

    /**
     * Runs the rollout described by {@code requestJson} against the SDDC LCM service at
     * {@code baseUrl} and returns the rollout report as a JSON document.
     */
    public static String run(String baseUrl, String bearerToken, String requestJson) throws Exception {
        throw new UnsupportedOperationException("VcfLcmClient.run is not implemented");
    }
}
