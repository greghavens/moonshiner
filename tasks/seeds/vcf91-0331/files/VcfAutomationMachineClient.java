import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Map;

/**
 * Single-file, dependency-free client for the VCF 9.1 VCF Automation machine-provisioning contract
 * projected in {@code docs/contract.json}.
 *
 * <p>Machine creation is accepted asynchronously: {@code createMachine} answers 202 with a
 * RequestTracker, and the provisioned machine is only observable once that tracker has been driven
 * to a terminal status through {@code getRequestTracker}.
 */
public final class VcfAutomationMachineClient {

    /** Path component every operation in the contract sits under. */
    public static final String SERVICE_BASE_PATH = "/iaas/api";

    public static final String OP_RETRIEVE_AUTH_TOKEN = "retrieveAuthToken";
    public static final String OP_GET_ABOUT_PAGE = "getAboutPage";
    public static final String OP_CREATE_MACHINE = "createMachine";
    public static final String OP_GET_REQUEST_TRACKER = "getRequestTracker";
    public static final String OP_GET_MACHINE = "getMachine";

    /** RequestTracker status values, as the reference page lists them. */
    public static final String STATUS_FINISHED = "FINISHED";

    public static final String STATUS_INPROGRESS = "INPROGRESS";
    public static final String STATUS_FAILED = "FAILED";

    public static final Duration DEFAULT_POLL_INTERVAL = Duration.ofSeconds(5);
    public static final Duration DEFAULT_POLL_TIMEOUT = Duration.ofMinutes(20);

    /**
     * Client configuration. A null {@code pollInterval}, {@code pollTimeout} or {@code httpClient}
     * takes the documented default; a null or blank {@code machineSelect} suppresses the optional
     * {@code $select} query parameter of {@code getMachine}.
     */
    public record Config(
            String baseUrl,
            String refreshToken,
            String machineSelect,
            Duration pollInterval,
            Duration pollTimeout,
            HttpClient httpClient) {

        public Config(String baseUrl, String refreshToken) {
            this(baseUrl, refreshToken, null, null, null, null);
        }
    }

    /** A Tag of the MachineSpecification. The reference page documents both members as required. */
    public record Tag(String key, String value) {}

    /**
     * One machine to provision. {@code name}, {@code projectId}, {@code image} and {@code flavor}
     * are always sent; {@code description}, {@code tags}, {@code customProperties} and
     * {@code deploymentId} are the optional members of MachineSpecification and are only sent when
     * they carry a value.
     */
    public record MachineRequest(
            String name,
            String projectId,
            String image,
            String flavor,
            String description,
            List<Tag> tags,
            Map<String, String> customProperties,
            String deploymentId) {

        public MachineRequest {
            tags = tags == null ? List.of() : List.copyOf(tags);
            customProperties =
                    customProperties == null ? Map.of() : new java.util.LinkedHashMap<>(customProperties);
        }

        public MachineRequest(String name, String projectId, String image, String flavor) {
            this(name, projectId, image, flavor, null, null, null, null);
        }
    }

    /** The subset of the Machine document this project reads. */
    public record MachineRef(
            String id,
            String name,
            String powerState,
            String address,
            String externalId,
            String projectId) {}

    /**
     * The outcome of one provisioning run. {@code trackerReads} is how many times
     * {@code getRequestTracker} was called before the terminal status was observed.
     */
    public record ProvisionResult(
            String requestId,
            String requestSelfLink,
            String terminalStatus,
            int progress,
            String apiVersion,
            int trackerReads,
            MachineRef machine) {}

    /** Base of every failure of a provisioning run. */
    public abstract static class ProvisioningException extends Exception {
        private final String operationId;
        private final String requestId;

        protected ProvisioningException(
                String message, String operationId, String requestId, Throwable cause) {
            super(message, cause);
            this.operationId = operationId;
            this.requestId = requestId;
        }

        /** The contract operationId whose call failed. */
        public final String operationId() {
            return operationId;
        }

        /** The tracked request id, or null when the failure happened before one existed. */
        public final String requestId() {
            return requestId;
        }
    }

    /** The service answered with a status the contract does not document as success. */
    public static final class VcfAutomationApiException extends ProvisioningException {
        private final int statusCode;
        private final String apiMessage;
        private final Integer errorCode;
        private final String serverErrorId;

        public VcfAutomationApiException(
                String operationId,
                String requestId,
                int statusCode,
                String apiMessage,
                Integer errorCode,
                String serverErrorId) {
            super(operationId + " failed with HTTP status " + statusCode, operationId, requestId, null);
            this.statusCode = statusCode;
            this.apiMessage = apiMessage;
            this.errorCode = errorCode;
            this.serverErrorId = serverErrorId;
        }

        public int statusCode() {
            return statusCode;
        }

        /** ServiceErrorResponse.message, or null when the reply carried none. */
        public String apiMessage() {
            return apiMessage;
        }

        /** ServiceErrorResponse.errorCode, or null when the reply carried none. */
        public Integer errorCode() {
            return errorCode;
        }

        /** ServiceErrorResponse.serverErrorId, or null when the reply carried none. */
        public String serverErrorId() {
            return serverErrorId;
        }
    }

    /** The tracked request reached a terminal status that is not FINISHED. */
    public static final class RequestFailedException extends ProvisioningException {
        private final String terminalStatus;
        private final String apiMessage;
        private final int progress;

        public RequestFailedException(
                String requestId, String terminalStatus, String apiMessage, int progress) {
            super(
                    "request " + requestId + " ended " + terminalStatus,
                    OP_GET_REQUEST_TRACKER,
                    requestId,
                    null);
            this.terminalStatus = terminalStatus;
            this.apiMessage = apiMessage;
            this.progress = progress;
        }

        public String terminalStatus() {
            return terminalStatus;
        }

        /** RequestTracker.message as the service reported it, or null when it carried none. */
        public String apiMessage() {
            return apiMessage;
        }

        public int progress() {
            return progress;
        }
    }

    /** The tracked request did not reach a terminal status within the configured poll timeout. */
    public static final class RequestPollTimeoutException extends ProvisioningException {
        private final String lastStatus;
        private final int lastProgress;

        public RequestPollTimeoutException(
                String requestId, String lastStatus, int lastProgress, Duration pollTimeout) {
            super(
                    "request " + requestId + " was still " + lastStatus + " after " + pollTimeout,
                    OP_GET_REQUEST_TRACKER,
                    requestId,
                    null);
            this.lastStatus = lastStatus;
            this.lastProgress = lastProgress;
        }

        /** The last non-terminal status observed before the deadline passed. */
        public String lastStatus() {
            return lastStatus;
        }

        public int lastProgress() {
            return lastProgress;
        }
    }

    /** The reply could not be understood as the document the contract projects. */
    public static final class VcfAutomationProtocolException extends ProvisioningException {
        public VcfAutomationProtocolException(String operationId, String requestId, String detail) {
            super(operationId + " returned an unusable reply: " + detail, operationId, requestId, null);
        }
    }

    /** The request could not be delivered or the reply could not be read. */
    public static final class VcfAutomationTransportException extends ProvisioningException {
        public VcfAutomationTransportException(String operationId, String requestId, Throwable cause) {
            super(operationId + " transport failed", operationId, requestId, cause);
        }
    }

    /**
     * Validates the configuration and builds a client. Nothing is sent from this call.
     *
     * @throws IllegalArgumentException if the configuration is unusable
     */
    public static VcfAutomationMachineClient create(Config config) {
        throw new UnsupportedOperationException("not implemented");
    }

    private VcfAutomationMachineClient() {
        // TODO: retain the validated configuration.
    }

    /**
     * The apiVersion discovered from {@code getAboutPage}, or null while the client has not yet
     * read the about page.
     */
    public String apiVersion() {
        throw new UnsupportedOperationException("not implemented");
    }

    /**
     * Provisions one machine: creates it, drives the accepted request to a terminal status, and
     * reads back the machine the terminal tracker points at.
     */
    public ProvisionResult provision(MachineRequest request) throws ProvisioningException {
        throw new UnsupportedOperationException("not implemented");
    }
}
