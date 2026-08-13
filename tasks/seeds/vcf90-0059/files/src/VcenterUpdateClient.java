import java.util.List;
import java.util.Map;

/**
 * Single-file client for the vCenter appliance-update operations of the vSphere
 * Automation API.
 *
 * The precheck must gate the install: when the precheck reports blocking errors the
 * client must not send the install request at all.
 *
 * Implement the body of {@link #applyFirstPendingUpdate}. Do not change the public
 * signatures below -- the TestMain harness compiles against them.
 */
public final class VcenterUpdateClient {

    /** Outcome of one apply attempt. */
    public static final class Result {
        /** True only when the install request was sent and succeeded. */
        public final boolean installed;
        /** Version the client acted on, or null when no update was pending. */
        public final String version;
        /** Ids of the precheck error notifications that blocked the install, in response order. */
        public final List<String> blockingIssues;

        public Result(boolean installed, String version, List<String> blockingIssues) {
            this.installed = installed;
            this.version = version;
            this.blockingIssues = blockingIssues;
        }
    }

    private final String baseUrl;
    private final String sessionId;

    /**
     * @param baseUrl   server base URL including the /api base path, with no trailing slash
     * @param sessionId value for the vmware-api-session-id header
     */
    public VcenterUpdateClient(String baseUrl, String sessionId) {
        this.baseUrl = baseUrl;
        this.sessionId = sessionId;
    }

    /**
     * Lists pending updates, prechecks the first one returned, and installs it only if the
     * precheck reports no blocking errors.
     *
     * @param sourceType          required source_type query value
     * @param url                 optional url query value; null means the parameter is not sent
     * @param listMajorUpgrades   optional enable_list_major_upgrade_versions query value; null
     *                            means the parameter is not sent
     * @param component           optional component body field; null means the field is not sent
     * @param userData            required user_data body field for the install operation
     */
    public Result applyFirstPendingUpdate(String sourceType,
                                          String url,
                                          Boolean listMajorUpgrades,
                                          String component,
                                          Map<String, String> userData) throws Exception {
        throw new UnsupportedOperationException("applyFirstPendingUpdate is not implemented yet");
    }
}
