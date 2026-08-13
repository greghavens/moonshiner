import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

/**
 * Single-file, dependency-free client for the focused VCF 9.1 SDDC LCM support-bundle contract.
 *
 * <p>Support-bundle generation and deletion are accepted asynchronously and hand back a {@code
 * Task}; the collected result is only observable once that task has been driven to a terminal
 * status through {@code getTask}.
 */
public final class SddcLcmSupportBundleClient {

    /** Path component of the specification's only server URL. */
    public static final String SERVICE_BASE_PATH = "/sddc-lcm";

    public static final String OP_GENERATE_COMPONENT_SUPPORT_BUNDLE =
            "generateComponentSupportBundle";
    public static final String OP_GET_TASK = "getTask";
    public static final String OP_GET_COMPONENT_SUPPORT_BUNDLES = "getComponentSupportBundles";
    public static final String OP_DELETE_COMPONENT_SUPPORT_BUNDLE = "deleteComponentSupportBundle";

    /** TaskStatus values, in specification declaration order. */
    public static final String STATUS_PENDING = "PENDING";

    public static final String STATUS_SCHEDULED = "SCHEDULED";
    public static final String STATUS_RUNNING = "RUNNING";
    public static final String STATUS_SUCCEEDED = "SUCCEEDED";
    public static final String STATUS_FAILED = "FAILED";
    public static final String STATUS_CANCELED = "CANCELED";

    public static final Duration DEFAULT_POLL_INTERVAL = Duration.ofSeconds(2);
    public static final Duration DEFAULT_POLL_TIMEOUT = Duration.ofMinutes(30);

    /**
     * Client configuration. A null {@code pollInterval}, {@code pollTimeout} or {@code httpClient}
     * takes the documented default; a null or empty {@code correlationId} suppresses the optional
     * {@code X-Correlation-Id} header everywhere.
     */
    public record Config(
            String baseUrl,
            String token,
            String correlationId,
            Duration pollInterval,
            Duration pollTimeout,
            HttpClient httpClient) {

        public Config(String baseUrl, String token) {
            this(baseUrl, token, null, null, null, null);
        }
    }

    /**
     * One component to collect from. {@code lookBackWindow} is the only member of the
     * specification's {@code ComponentSupportBundleSpec} and it is optional: null means the member
     * is absent from the request entirely, while an explicit zero is a value that must survive.
     */
    public record ComponentRequest(String componentId, Integer lookBackWindow) {}

    /**
     * A collection run. {@code retainNewestPerComponent} of zero or less disables pruning; any
     * larger value keeps that many newest bundles per component and deletes the rest.
     */
    public record CollectionPlan(List<ComponentRequest> components, int retainNewestPerComponent) {
        public CollectionPlan {
            components = List.copyOf(components);
        }
    }

    /** A SupportBundle entry as returned by {@code getComponentSupportBundles}. */
    public record BundleRef(
            String id, String name, long sizeBytes, String createdTimestamp, String url) {}

    /**
     * The outcome for one component. {@code bundle} is populated only when the generation task
     * reached {@code SUCCEEDED}; {@code failedStage} and {@code message} are populated only when it
     * reached a terminal status that is not {@code SUCCEEDED}.
     */
    public record ComponentOutcome(
            String componentId,
            String generationTaskId,
            String terminalStatus,
            BundleRef bundle,
            String failedStage,
            String message,
            List<String> prunedBundleIds) {

        public ComponentOutcome {
            prunedBundleIds = List.copyOf(prunedBundleIds);
        }
    }

    /** One outcome per planned component, in plan order. */
    public record CollectionReport(List<ComponentOutcome> outcomes) {
        public CollectionReport {
            outcomes = List.copyOf(outcomes);
        }
    }

    /** Base of every failure that aborts a collection run. */
    public abstract static class CollectionException extends Exception {
        private final String operationId;
        private final String componentId;
        private final CollectionReport report;

        protected CollectionException(
                String message,
                String operationId,
                String componentId,
                CollectionReport report,
                Throwable cause) {
            super(message, cause);
            this.operationId = operationId;
            this.componentId = componentId;
            this.report = report;
        }

        /** The specification operationId whose call failed. */
        public final String operationId() {
            return operationId;
        }

        /** The component being processed, or null when the failure is not component-scoped. */
        public final String componentId() {
            return componentId;
        }

        /** Outcomes completed before the failure. Never null. */
        public final CollectionReport report() {
            return report;
        }
    }

    /** The service answered with a status the contract does not document as success. */
    public static final class SddcLcmApiException extends CollectionException {
        private final int statusCode;
        private final String errorCode;
        private final String apiMessage;
        private final String referenceId;

        public SddcLcmApiException(
                String operationId,
                String componentId,
                int statusCode,
                String errorCode,
                String apiMessage,
                String referenceId,
                CollectionReport report) {
            super(
                    operationId + " failed with HTTP status " + statusCode,
                    operationId,
                    componentId,
                    report,
                    null);
            this.statusCode = statusCode;
            this.errorCode = errorCode;
            this.apiMessage = apiMessage;
            this.referenceId = referenceId;
        }

        public int statusCode() {
            return statusCode;
        }

        public String errorCode() {
            return errorCode;
        }

        public String apiMessage() {
            return apiMessage;
        }

        public String referenceId() {
            return referenceId;
        }
    }

    /** A task did not reach a terminal status within the configured poll timeout. */
    public static final class TaskPollTimeoutException extends CollectionException {
        private final String taskId;
        private final String lastStatus;

        public TaskPollTimeoutException(
                String componentId,
                String taskId,
                String lastStatus,
                Duration pollTimeout,
                CollectionReport report) {
            super(
                    "task " + taskId + " was still " + lastStatus + " after " + pollTimeout,
                    OP_GET_TASK,
                    componentId,
                    report,
                    null);
            this.taskId = taskId;
            this.lastStatus = lastStatus;
        }

        public String taskId() {
            return taskId;
        }

        /** The last non-terminal status observed before the deadline passed. */
        public String lastStatus() {
            return lastStatus;
        }
    }

    /** The reply could not be understood as the document the contract projects. */
    public static final class SddcLcmProtocolException extends CollectionException {
        public SddcLcmProtocolException(
                String operationId, String componentId, String detail, CollectionReport report) {
            super(operationId + " returned an unusable reply: " + detail,
                    operationId, componentId, report, null);
        }
    }

    /** The request could not be delivered or the reply could not be read. */
    public static final class SddcLcmTransportException extends CollectionException {
        public SddcLcmTransportException(
                String operationId, String componentId, CollectionReport report, Throwable cause) {
            super(operationId + " transport failed", operationId, componentId, report, cause);
        }
    }

    /**
     * Validates the configuration and builds a client. Nothing is sent from this call.
     *
     * @throws IllegalArgumentException if the configuration is unusable
     */
    public static SddcLcmSupportBundleClient create(Config config) {
        throw new UnsupportedOperationException("not implemented");
    }

    private SddcLcmSupportBundleClient() {
        // TODO: retain the validated configuration.
    }

    /**
     * Collects a support bundle for every planned component in order, driving each accepted task to
     * a terminal status before reading its result.
     */
    public CollectionReport collect(CollectionPlan plan) throws CollectionException {
        throw new UnsupportedOperationException("not implemented");
    }
}
