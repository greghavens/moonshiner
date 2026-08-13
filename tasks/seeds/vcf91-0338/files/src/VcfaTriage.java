/**
 * VCF Automation 9.1 deployment triage client.
 *
 * Implement this class. It is the only file you need to change.
 *
 * The contract you are coding against is docs/contract.json; its provenance is
 * docs/official_sources.json. Do not add source files, libraries or build
 * tooling - the harness compiles exactly this file together with
 * harness/TestMain.java and harness/Json.java on the stock JDK.
 *
 * Json (from the harness) is available for reading responses:
 *   Object doc = Json.parse(body);
 *   String name = Json.str(doc, "name");
 *   for (Object item : Json.asArray(Json.get(doc, "content"))) { ... }
 * and Json.quote(s) escapes a string for a JSON document you build yourself.
 */
public final class VcfaTriage {

    private final String baseUrl;
    private final String bearerToken;

    /**
     * @param baseUrl     origin of the VCF Automation API, no trailing slash,
     *                    for example "https://automation.vcf.example.com"
     * @param bearerToken the token to present as "Authorization: Bearer &lt;token&gt;"
     */
    public VcfaTriage(String baseUrl, String bearerToken) {
        this.baseUrl = baseUrl;
        this.bearerToken = bearerToken;
    }

    /**
     * Diagnose the failed deployment and submit the remediating day-2 action.
     *
     * @param deploymentId the deployment to triage
     * @return the report, in the exact format described in README.md
     */
    public String triage(String deploymentId) throws Exception {
        throw new UnsupportedOperationException("VcfaTriage.triage is not implemented yet");
    }
}
