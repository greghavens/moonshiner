package com.example.vcf;

import java.util.List;

/**
 * Client for the cluster software remediation flow of the vSphere Automation API on a
 * VMware Cloud Foundation 9.0 vCenter Server.
 *
 * <p>The three operations this client is allowed to use, their paths, status codes, security
 * schemes and request shapes are pinned in {@code docs/contract.json}:
 *
 * <ul>
 *   <li>{@code Cis.Session_create} — exchange Basic credentials for a session token</li>
 *   <li>{@code Esx.Settings.Clusters.Software_apply$Task} — start the remediation, which returns
 *       a task identifier rather than a result</li>
 *   <li>{@code Cis.Tasks_get} — read the task until it reaches a terminal status</li>
 * </ul>
 *
 * <p>The public API below is what the test harness compiles against. Keep the class name, the
 * nested types, the constructor and the method signatures exactly as they are; everything else
 * about the implementation is yours.
 */
public final class VcenterRemediationClient {

    /**
     * Caller supplied values for {@code Esx.Settings.Clusters.Software.ApplySpec}.
     *
     * <p>Every property is optional. A field left {@code null} — and, for {@link #commit} and
     * {@link #hosts}, a field left blank or empty — means the caller supplied nothing, and the
     * corresponding JSON member must not appear in the request body at all.
     *
     * <p>{@code acceptEula} is different: {@link Boolean#FALSE} is a value the caller supplied and
     * must be sent as the JSON literal {@code false}. Only {@code null} means unset.
     */
    public static final class ApplyOptions {
        /** Minimum desired-state commit to remediate to; unset means "use the latest commit". */
        public String commit;
        /** Hosts to remediate; unset means "every host in the cluster". */
        public List<String> hosts;
        /** Whether the caller accepts the VMware EULA. */
        public Boolean acceptEula;
    }

    /**
     * Caller supplied values for the optional {@code spec} query parameter of {@code Cis.Tasks_get}
     * ({@code Cis.Tasks.GetSpec}).
     *
     * <p>Both properties are optional and {@code null} means unset. The parameter is declared
     * {@code style=form, explode=true}, so each property that is set becomes its own top level
     * query parameter named after the property, and a property that is unset contributes nothing
     * to the query string.
     */
    public static final class PollOptions {
        /** Include operation specific data beyond {@code Cis.Task.Info}. */
        public Boolean returnAll;
        /** Leave the operation result out of the task information. */
        public Boolean excludeResult;
    }

    /** What the remediation settled on. */
    public static final class Outcome {
        /** The task identifier returned by the apply operation. */
        public String taskId;
        /** The terminal {@code Cis.Task.Status}: {@code SUCCEEDED} or {@code FAILED}. */
        public String status;
        /** How many {@code Cis.Tasks_get} requests were issued, including the terminal one. */
        public int pollCount;
        /**
         * {@code error.messages[0].default_message} from the failed task, or {@code null} when the
         * task succeeded or reported no message.
         */
        public String errorMessage;
    }

    private final String baseUrl;
    private final long pollIntervalMillis;
    private final int maxPolls;

    /**
     * @param baseUrl            scheme and authority of the appliance, with no trailing slash and
     *                           no path — for example {@code https://vcenter.example.com}. The
     *                           {@code /api} prefix that the specification's server URL carries is
     *                           this client's job to add.
     * @param pollIntervalMillis how long to wait between task polls
     * @param maxPolls           give up after this many polls without reaching a terminal status
     */
    public VcenterRemediationClient(String baseUrl, long pollIntervalMillis, int maxPolls) {
        this.baseUrl = baseUrl;
        this.pollIntervalMillis = pollIntervalMillis;
        this.maxPolls = maxPolls;
    }

    public String baseUrl() {
        return baseUrl;
    }

    public long pollIntervalMillis() {
        return pollIntervalMillis;
    }

    public int maxPolls() {
        return maxPolls;
    }

    /**
     * Creates a session with the appliance and remembers the token for later calls.
     *
     * @return the session token the appliance issued
     * @throws Exception if the appliance does not answer with the status the contract declares
     */
    public String login(String username, String password) throws Exception {
        throw new UnsupportedOperationException("TODO: implement Cis.Session_create");
    }

    /**
     * Starts remediation of {@code clusterId} and polls the resulting task until it settles.
     *
     * <p>The apply operation answers with a task identifier, not with a result, so this method must
     * not report success on the strength of that response. It reads {@code Cis.Tasks_get} until
     * {@code Cis.Task.Info.status} is {@code SUCCEEDED} or {@code FAILED}. {@code PENDING},
     * {@code RUNNING} and {@code BLOCKED} are all states the task can still move out of.
     *
     * @param applyOptions caller supplied ApplySpec values; never {@code null}, though every field
     *                     inside it may be unset
     * @param pollOptions  caller supplied GetSpec values to send with each poll; never {@code null},
     *                     though every field inside it may be unset
     * @return the terminal outcome; a task that ends {@code FAILED} is a result, not an exception
     * @throws IllegalStateException if no session has been created yet
     * @throws Exception             if the appliance answers unexpectedly, or if the task has not
     *                               settled after {@link #maxPolls()} polls
     */
    public Outcome remediateCluster(String clusterId, ApplyOptions applyOptions, PollOptions pollOptions)
            throws Exception {
        throw new UnsupportedOperationException(
                "TODO: implement Esx.Settings.Clusters.Software_apply$Task followed by Cis.Tasks_get polling");
    }
}
