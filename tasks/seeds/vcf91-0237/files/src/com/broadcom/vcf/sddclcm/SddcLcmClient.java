package com.broadcom.vcf.sddclcm;

import java.io.IOException;
import java.net.http.HttpClient;

/**
 * Focused VMware Cloud Foundation 9.1 SDDC LCM client.
 *
 * <p>The client drives one fleet-scoped component upgrade against the five
 * operations projected in {@code docs/contract.json}. Everything below the
 * public surface is yours to implement; the public surface itself is fixed
 * because the protected harness compiles against it.
 */
public final class SddcLcmClient {

    /**
     * Supplies the SDDC LCM access token and renews it on demand.
     *
     * <p>The pinned SDDC LCM specification declares the {@code bearerToken}
     * security scheme but no token issuance or refresh route, so renewal happens
     * here rather than as an SDDC LCM request.
     */
    public interface AccessTokenSource {

        /** Returns the access token currently held for this session. */
        String currentAccessToken();

        /** Renews the session and returns the replacement access token. */
        String refreshAccessToken();
    }

    /** Inputs for one fleet component upgrade. */
    public record UpgradeRequest(
            String componentType,
            String targetVersion,
            String fleetDepotFqdn,
            String fleetDepotCertificate,
            String correlationId,
            boolean performBackup) {
    }

    /** Result of one completed fleet component upgrade. */
    public record UpgradeOutcome(
            String componentId,
            String componentType,
            String previousVersion,
            String targetVersion,
            String resolvedBinaryUrl,
            String precheckTaskId,
            String precheckStatus,
            String applyTaskId,
            String applyStatus,
            int accessTokenRefreshes) {
    }

    /** Base type for every failure the workflow reports. */
    public abstract static sealed class SddcLcmException extends Exception
            permits ApiException, ProtocolException, TaskFailureException {
        private static final long serialVersionUID = 1L;

        SddcLcmException(String message) {
            super(message);
        }
    }

    /** A contract operation answered with an unexpected HTTP status. */
    public static final class ApiException extends SddcLcmException {
        private static final long serialVersionUID = 1L;

        private final String operationId;
        private final int statusCode;
        private final String errorCode;

        public ApiException(String operationId, int statusCode, String errorCode) {
            super(operationId + " answered with HTTP status " + statusCode
                    + (errorCode == null || errorCode.isEmpty() ? "" : " and error code " + errorCode));
            this.operationId = operationId;
            this.statusCode = statusCode;
            this.errorCode = errorCode;
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }

        /** The {@code ErrorResponse.code} member, or {@code null} when the body carried none. */
        public String errorCode() {
            return errorCode;
        }
    }

    /** A successful response violated the projected contract. */
    public static final class ProtocolException extends SddcLcmException {
        private static final long serialVersionUID = 1L;

        private final String operationId;
        private final String problem;

        public ProtocolException(String operationId, String problem) {
            super(operationId + " protocol error: " + problem);
            this.operationId = operationId;
            this.problem = problem;
        }

        public String operationId() {
            return operationId;
        }

        public String problem() {
            return problem;
        }
    }

    /** A polled lifecycle task reached a terminal status other than {@code SUCCEEDED}. */
    public static final class TaskFailureException extends SddcLcmException {
        private static final long serialVersionUID = 1L;

        private final String taskId;
        private final String taskType;
        private final String status;

        public TaskFailureException(String taskId, String taskType, String status) {
            super(taskType + " task " + taskId + " finished with status " + status);
            this.taskId = taskId;
            this.taskType = taskType;
            this.status = status;
        }

        public String taskId() {
            return taskId;
        }

        public String taskType() {
            return taskType;
        }

        public String status() {
            return status;
        }
    }

    /** Signals that the exercise stub has not been implemented yet. */
    static final class NotImplemented extends UnsupportedOperationException {
        private static final long serialVersionUID = 1L;

        NotImplemented() {
            super("the SDDC LCM fleet component upgrade workflow is not implemented");
        }
    }

    private final String serviceRootUrl;
    private final AccessTokenSource tokens;
    private final HttpClient httpClient;

    private SddcLcmClient(String serviceRootUrl, AccessTokenSource tokens, HttpClient httpClient) {
        this.serviceRootUrl = serviceRootUrl;
        this.tokens = tokens;
        this.httpClient = httpClient;
    }

    /**
     * Creates a client for the SDDC LCM service root, for example
     * {@code https://vcf.broadcom.com/sddc-lcm}.
     *
     * @throws IllegalArgumentException when an argument is unusable
     */
    public static SddcLcmClient create(String serviceRootUrl, AccessTokenSource tokens, HttpClient httpClient) {
        return new SddcLcmClient(serviceRootUrl, tokens, httpClient);
    }

    /**
     * Runs the fleet component upgrade: health probe, fleet component lookup,
     * depot resolution, precheck, and apply, polling each lifecycle task to a
     * terminal status.
     */
    public UpgradeOutcome upgradeFleetComponent(UpgradeRequest request)
            throws SddcLcmException, IOException, InterruptedException {
        throw new NotImplemented();
    }
}
