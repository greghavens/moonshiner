import java.io.IOException;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Objects;

/**
 * Single-file client for the VMware Cloud Foundation 9.0 SDDC Manager host commission workflow.
 *
 * <p>The wire contract is docs/contract.json, projected from
 * specifications/sddc-manager/sddc-manager-openapi.json at tag 9.0.0.0 of vmware/vcf-api-specs.
 * Only the operationIds validateHostCommissionSpec, getHostCommissionValidationByID and
 * commissionHosts are in scope.
 *
 * <p>The public surface below is fixed - TestMain compiles against it. The workflow is not
 * implemented yet.
 */
public final class HostCommissionClient {

    private final String baseUrl;
    private final HttpClient http;
    private final Duration pollInterval;
    private final int maxPolls;

    /** Uses a five second poll interval and at most sixty polls. Performs no I/O. */
    public HostCommissionClient(String baseUrl, HttpClient http) {
        this(baseUrl, http, Duration.ofSeconds(5), 60);
    }

    public HostCommissionClient(String baseUrl, HttpClient http, Duration pollInterval, int maxPolls) {
        this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl");
        this.http = Objects.requireNonNull(http, "http");
        this.pollInterval = Objects.requireNonNull(pollInterval, "pollInterval");
        if (maxPolls < 0) {
            throw new IllegalArgumentException("maxPolls must not be negative");
        }
        this.maxPolls = maxPolls;
    }

    /**
     * Prechecks the supplied hosts and commissions them only when the precheck passes.
     *
     * @throws SddcApiException when SDDC Manager answers with anything other than 202, or when the
     *                          validation never reaches a terminal execution status
     */
    public CommissionOutcome commission(List<HostCommissionSpec> hosts)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("the host commission workflow is not implemented yet");
    }

    /**
     * One entry of the HostCommissionSpec array. Required properties come from the constructor;
     * optional properties are set fluently and are omitted from the wire when never set.
     */
    public static final class HostCommissionSpec {

        public HostCommissionSpec(String fqdn, String username, String password,
                                  String storageType, String networkPoolId) {
        }

        public HostCommissionSpec vvolStorageProtocolType(String value) {
            return this;
        }

        public HostCommissionSpec networkPoolName(String value) {
            return this;
        }

        public HostCommissionSpec sshThumbprint(String value) {
            return this;
        }

        public HostCommissionSpec sslThumbprint(String value) {
            return this;
        }
    }

    /** Outcome of the precheck, whether or not it opened the gate. */
    public static final class PrecheckResult {

        public final String validationId;
        public final String description;
        public final String executionStatus;
        public final String resultStatus;
        public final List<String> failedChecks;
        public final int pollCount;

        public PrecheckResult(String validationId, String description, String executionStatus,
                              String resultStatus, List<String> failedChecks, int pollCount) {
            this.validationId = validationId;
            this.description = description;
            this.executionStatus = executionStatus;
            this.resultStatus = resultStatus;
            this.failedChecks = Collections.unmodifiableList(new ArrayList<>(failedChecks));
            this.pollCount = pollCount;
        }

        /** True only for executionStatus COMPLETED together with resultStatus SUCCEEDED. */
        public boolean passed() {
            throw new UnsupportedOperationException("the precheck gate is not implemented yet");
        }
    }

    /** What the workflow did: the precheck result and, when the gate opened, the commission task. */
    public static final class CommissionOutcome {

        public final boolean commissioned;
        public final PrecheckResult precheck;
        public final String taskId;
        public final String taskName;
        public final String taskStatus;

        public CommissionOutcome(boolean commissioned, PrecheckResult precheck,
                                 String taskId, String taskName, String taskStatus) {
            this.commissioned = commissioned;
            this.precheck = precheck;
            this.taskId = taskId;
            this.taskName = taskName;
            this.taskStatus = taskStatus;
        }
    }

    /** An SDDC Manager error response, or a precheck that never reached a terminal status. */
    public static final class SddcApiException extends IOException {

        private static final long serialVersionUID = 1L;

        public final int statusCode;
        public final String errorCode;

        public SddcApiException(int statusCode, String errorCode, String message) {
            super(message);
            this.statusCode = statusCode;
            this.errorCode = errorCode;
        }
    }
}
