import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;
import java.util.Objects;

/**
 * Minimal client surface used by the VCF Installer integration.
 *
 * <p>The implementation intentionally belongs in this single source file and
 * may use only the Java 17 standard library.</p>
 */
public final class VcfInstallerClient {
    private final URI baseUri;
    private final HttpClient httpClient;

    public VcfInstallerClient(URI baseUri, HttpClient httpClient) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
    }

    public record Task(
            String id,
            String name,
            String type,
            String status,
            String creationTimestamp,
            String completionTimestamp) {
    }

    /** Optional filters and paging controls from the getTasks operation. */
    public static final class TaskQuery {
        private Integer limit;
        private String taskStatus;
        private String taskType;
        private String resourceId;
        private String resourceType;
        private Long completedAfter;
        private int pageSize = 100;
        private String orderDirection;
        private String orderBy;
        private String taskName;
        private Boolean doLiveRefresh;

        public TaskQuery limit(Integer value) { this.limit = value; return this; }
        public TaskQuery taskStatus(String value) { this.taskStatus = value; return this; }
        public TaskQuery taskType(String value) { this.taskType = value; return this; }
        public TaskQuery resourceId(String value) { this.resourceId = value; return this; }
        public TaskQuery resourceType(String value) { this.resourceType = value; return this; }
        public TaskQuery completedAfter(Long value) { this.completedAfter = value; return this; }
        public TaskQuery pageSize(int value) { this.pageSize = value; return this; }
        public TaskQuery orderDirection(String value) { this.orderDirection = value; return this; }
        public TaskQuery orderBy(String value) { this.orderBy = value; return this; }
        public TaskQuery taskName(String value) { this.taskName = value; return this; }
        public TaskQuery doLiveRefresh(Boolean value) { this.doLiveRefresh = value; return this; }
    }

    /** Retrieve every page of tasks and return a stable snapshot. */
    public List<Task> getAllTasks(TaskQuery query) throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement getTasks pagination");
    }
}
