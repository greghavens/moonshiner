import java.util.List;

/**
 * Single-file REST client for the VMware Cloud Foundation Operations 9.1 suite-api.
 *
 * <p>The seven operations, their wire shapes and the request/response schemas are pinned by
 * {@code docs/contract.json}, which was derived from the vmware/vcf-api-specs OpenAPI document
 * recorded in {@code docs/official_sources.json}.
 *
 * <p>Implement every method below. Keep it to this one file and to the JDK standard library
 * (java.net.http is available); no third-party dependencies and no extra source files.
 *
 * <p>Method signatures are fixed - {@code harness/TestMain.java} compiles against them.
 */
public class VcfOpsClient implements AutoCloseable {

    /**
     * @param baseUrl absolute base URL including the suite-api base path,
     *                e.g. {@code http://127.0.0.1:8443/suite-api}
     */
    public VcfOpsClient(String baseUrl) {
        throw new UnsupportedOperationException("VcfOpsClient is not implemented yet");
    }

    /**
     * operationId {@code acquireToken}. Stores the returned token for subsequent calls.
     *
     * @param authSource null when the caller did not supply one
     */
    public void acquireToken(String username, String password, String authSource) {
        throw new UnsupportedOperationException("acquireToken is not implemented yet");
    }

    /**
     * operationId {@code getMatchingResources}. Returns the raw JSON response body.
     *
     * @param names         null or empty when the caller did not supply the filter
     * @param resourceKinds null or empty when the caller did not supply the filter
     */
    public String getMatchingResources(List<String> names, List<String> resourceKinds) {
        throw new UnsupportedOperationException("getMatchingResources is not implemented yet");
    }

    /**
     * operationId {@code queryAlert}. Returns the raw JSON response body.
     *
     * @param resourceIds   null or empty when the caller did not supply the filter
     * @param activeOnly    always supplied, including when false
     * @param criticalities null or empty when the caller did not supply the filter
     */
    public String queryAlert(List<String> resourceIds, boolean activeOnly, List<String> criticalities) {
        throw new UnsupportedOperationException("queryAlert is not implemented yet");
    }

    /** operationId {@code getAlertContributingSymptoms}. Returns the raw JSON response body. */
    public String getAlertContributingSymptoms(List<String> alertIds) {
        throw new UnsupportedOperationException("getAlertContributingSymptoms is not implemented yet");
    }

    /**
     * operationId {@code getSymptoms}. Returns the raw JSON response body.
     *
     * @param resourceIds      null or empty when the caller did not supply the filter
     * @param activeOnly       null when the caller did not supply it
     * @param includeAlarmInfo null when the caller did not supply it
     */
    public String getSymptoms(List<String> resourceIds, Boolean activeOnly, Boolean includeAlarmInfo) {
        throw new UnsupportedOperationException("getSymptoms is not implemented yet");
    }

    /**
     * operationId {@code getTasksStatus}. Returns the raw JSON response body.
     *
     * @param taskStates null or empty when the caller did not supply the filter
     * @param taskIds    null or empty when the caller did not supply the filter
     */
    public String getTasksStatus(List<String> taskStates, List<String> taskIds) {
        throw new UnsupportedOperationException("getTasksStatus is not implemented yet");
    }

    /** operationId {@code releaseToken}. Terminates the session held by this client. */
    public void releaseToken() {
        throw new UnsupportedOperationException("releaseToken is not implemented yet");
    }

    @Override
    public void close() {
        // Release any resources held by the client. Must be safe to call more than once.
    }
}
