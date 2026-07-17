import java.util.Map;

/**
 * Existing integration: kicks off a Document AI batch process. The operation
 * it returns is a google.longrunning.Operation; downstream tooling polls it
 * separately.
 */
final class DocAiClient {
    private final ApiHttp http;
    private final String baseUrl;
    private final String token;

    DocAiClient(ApiHttp http, String baseUrl, String token) {
        this.http = http;
        this.baseUrl = baseUrl;
        this.token = token;
    }

    /** Starts a batch process and returns the long-running operation name. */
    String startBatch(String processorPath, String gcsInputPrefix, String gcsOutputUri) {
        String url = baseUrl + "/v1/" + processorPath + ":batchProcess";
        String body = "{\"inputDocuments\":{\"gcsPrefix\":{\"gcsUriPrefix\":\"" + gcsInputPrefix
                + "\"}},\"documentOutputConfig\":{\"gcsOutputConfig\":{\"gcsUri\":\"" + gcsOutputUri + "\"}}}";
        ApiHttp.Response resp = http.post(url, token, body);
        if (resp.status() / 100 != 2) {
            throw ApiException.decode(resp.status(), resp.body());
        }
        Map<String, Object> operation = Json.object(Json.parse(resp.body()));
        Object name = operation.get("name");
        if (name == null) {
            throw new IllegalStateException("batchProcess response carried no operation name");
        }
        return name.toString();
    }
}
