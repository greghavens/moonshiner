import java.io.IOException;
import java.net.http.HttpClient;

/**
 * Client for creating a vSAN Data Protection protection group snapshot and waiting for
 * the resulting task to reach a terminal state.
 *
 * The wire contract this class must speak is docs/contract.json, which is derived from the
 * VCF 9.1 vSAN Data Protection OpenAPI document recorded in docs/official_sources.json.
 * Three operations are in scope:
 *
 *   Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task
 *     POST {base}/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots?vmw-task=true
 *     Body: Snapshots.CreateSpec. Answers 202 with the task identifier as a bare JSON string.
 *
 *   Snapservice.Tasks_get
 *     GET {base}/snapservice/tasks/{task}
 *     Answers 200 with Snapservice.Tasks.Info.
 *
 *   Snapservice.Clusters.ProtectionGroups.Snapshots_get
 *     GET {base}/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots/{snapshot}
 *     Answers 200 with Snapshots.Info.
 *
 * Every request carries the session token in the vmware-api-session-id header.
 *
 * This class must not use any dependency outside the JDK.
 */
public final class SnapshotProtectionClient {

    /** A protection group snapshot as read back from Snapshots_get. */
    public static final class Snapshot {
        /** Snapshot identifier, taken from the succeeded task's result. */
        public final String id;
        /** Snapshots.Info.name. */
        public final String name;
        /** Snapshots.Info.snapshot_type. */
        public final String snapshotType;
        /** Snapshots.Info.expires_at, or null when the property is absent. */
        public final String expiresAt;

        public Snapshot(String id, String name, String snapshotType, String expiresAt) {
            this.id = id;
            this.name = name;
            this.snapshotType = snapshotType;
            this.expiresAt = expiresAt;
        }

        @Override
        public String toString() {
            return "Snapshot{id=" + id + ", name=" + name + ", snapshotType=" + snapshotType
                    + ", expiresAt=" + expiresAt + "}";
        }
    }

    /** Raised when the snapshot task reaches the terminal FAILED status. */
    public static final class TaskFailedException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        /** Identifier of the task that failed. */
        public final String taskId;
        /** Terminal status observed, that is, FAILED. */
        public final String status;
        /** Human readable reason drawn from Tasks.Info.error. */
        public final String detail;

        public TaskFailedException(String taskId, String status, String detail) {
            super("task " + taskId + " ended with status " + status + ": " + detail);
            this.taskId = taskId;
            this.status = status;
            this.detail = detail;
        }
    }

    private final String baseUrl;
    private final String sessionId;
    private final long pollIntervalMillis;
    private final int maxPollAttempts;
    private final HttpClient http;

    /**
     * @param baseUrl            appliance base URL including the /api base path, with no trailing slash,
     *                           for example http://127.0.0.1:8443/api
     * @param sessionId          value for the vmware-api-session-id header
     * @param pollIntervalMillis pause between two consecutive task polls
     * @param maxPollAttempts    maximum number of Tasks_get calls before giving up
     */
    public SnapshotProtectionClient(String baseUrl, String sessionId, long pollIntervalMillis,
                                    int maxPollAttempts) {
        this(baseUrl, sessionId, pollIntervalMillis, maxPollAttempts, HttpClient.newHttpClient());
    }

    /** Package-private transport injection used by the deterministic in-process harness. */
    SnapshotProtectionClient(String baseUrl, String sessionId, long pollIntervalMillis,
                             int maxPollAttempts, HttpClient http) {
        this.baseUrl = baseUrl;
        this.sessionId = sessionId;
        this.pollIntervalMillis = pollIntervalMillis;
        this.maxPollAttempts = maxPollAttempts;
        this.http = http;
    }

    /**
     * Creates a protection group snapshot and returns it once the task has succeeded.
     *
     * The snapshot is created as an asynchronous task: the create call only yields a task
     * identifier, so the task must be polled until its status is terminal. PENDING, RUNNING
     * and BLOCKED are not terminal. Only once the task reports SUCCEEDED is the snapshot
     * identifier available in the task result, and only then may the snapshot be read back.
     *
     * Retention is optional in the CreateSpec. When both retentionUnit and retentionDuration
     * are null the request body must carry no retention property at all.
     *
     * @param cluster           cluster identifier for the path
     * @param pg                protection group identifier for the path
     * @param snapshotName      CreateSpec.name
     * @param retentionUnit     RetentionPeriod.unit, or null for no retention
     * @param retentionDuration RetentionPeriod.duration, or null for no retention
     * @return the snapshot read back after the task succeeded
     * @throws TaskFailedException  if the task reaches the terminal FAILED status
     * @throws IOException          on a transport error, an unexpected HTTP status, or when
     *                              maxPollAttempts is exhausted without a terminal status
     * @throws InterruptedException if the polling pause is interrupted
     */
    public Snapshot createProtectionGroupSnapshot(String cluster, String pg, String snapshotName,
                                                  String retentionUnit, Long retentionDuration)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("createProtectionGroupSnapshot is not implemented yet");
    }
}
