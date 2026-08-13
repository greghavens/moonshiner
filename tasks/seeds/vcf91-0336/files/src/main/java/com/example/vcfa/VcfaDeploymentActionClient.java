package com.example.vcfa;

import java.util.Map;

/**
 * Client for the two VCF Automation 9.1 operations named in {@code docs/contract.json}:
 * the {@code getDeploymentActions} precheck and the {@code submitDeploymentActionRequest}
 * mutating call.
 *
 * <p>The whole client lives in this one file. {@link Json} is provided for you.
 *
 * <p>The public surface below is fixed - the harness in {@code test/} compiles against these exact
 * signatures, so do not rename or re-shape them. Everything inside the method bodies is yours.
 */
public final class VcfaDeploymentActionClient {

    private final String baseUrl;
    private final String bearerToken;

    /**
     * @param baseUrl origin of the VCF Automation appliance, with no trailing slash,
     *                e.g. {@code http://127.0.0.1:53421}
     * @param bearerToken raw JWT, without the {@code Bearer } prefix
     */
    public VcfaDeploymentActionClient(String baseUrl, String bearerToken) {
        this.baseUrl = baseUrl;
        this.bearerToken = bearerToken;
    }

    /**
     * Prechecks a day-2 action against a deployment and, only if the precheck clears it, submits it.
     *
     * <p>See {@code docs/contract.json} for the binding rules: {@code gating} says when the mutating
     * call may be sent at all, and {@code serialization} says what the request body may contain.
     *
     * @param deploymentId deployment to act on
     * @param actionName the {@code name} of the action to run, as it appears in the precheck
     *                   response (for example {@code Deployment.PowerOff})
     * @param reason optional reason to record against the request; may be null, empty or blank
     * @param inputs optional action inputs; may be null or empty
     * @return the outcome, describing either the submitted request or the gate that stopped it
     * @throws VcfaApiException if the appliance answers either operation with a non-2xx status
     */
    public ActionOutcome requestDeploymentAction(
            String deploymentId, String actionName, String reason, Map<String, Object> inputs)
            throws VcfaApiException {
        throw new UnsupportedOperationException("requestDeploymentAction is not implemented yet");
    }

    /** Result of {@link #requestDeploymentAction}. */
    public static final class ActionOutcome {
        private final boolean submitted;
        private final String gate;
        private final String actionId;
        private final String requestId;
        private final String status;

        private ActionOutcome(
                boolean submitted, String gate, String actionId, String requestId, String status) {
            this.submitted = submitted;
            this.gate = gate;
            this.actionId = actionId;
            this.requestId = requestId;
            this.status = status;
        }

        /** Outcome for a request that cleared the precheck and was sent. */
        public static ActionOutcome submitted(String actionId, String requestId, String status) {
            return new ActionOutcome(true, null, actionId, requestId, status);
        }

        /**
         * Outcome for a request the precheck refused. Nothing was sent to the appliance.
         *
         * @param gate one of the {@code gateConditions} codes in the contract
         * @param actionId the matched action id if one was matched, otherwise null
         */
        public static ActionOutcome gated(String gate, String actionId) {
            return new ActionOutcome(false, gate, actionId, null, null);
        }

        /** True only when the mutating call was actually sent. */
        public boolean isSubmitted() {
            return submitted;
        }

        /** Gate condition code when {@link #isSubmitted()} is false, otherwise null. */
        public String gate() {
            return gate;
        }

        /** Id of the matched action, or null when no action matched. */
        public String actionId() {
            return actionId;
        }

        /** {@code id} of the created request, or null when gated. */
        public String requestId() {
            return requestId;
        }

        /** {@code status} of the created request, or null when gated. */
        public String status() {
            return status;
        }

        @Override
        public String toString() {
            return submitted
                    ? "ActionOutcome[submitted requestId=" + requestId + " status=" + status + "]"
                    : "ActionOutcome[gated " + gate + "]";
        }
    }

    /** Raised when the appliance answers one of the contract operations with a non-2xx status. */
    public static final class VcfaApiException extends Exception {
        private static final long serialVersionUID = 1L;

        private final int statusCode;
        private final String operationId;

        public VcfaApiException(String operationId, int statusCode, String message) {
            super(operationId + " failed with HTTP " + statusCode + ": " + message);
            this.operationId = operationId;
            this.statusCode = statusCode;
        }

        /** HTTP status the appliance returned. */
        public int statusCode() {
            return statusCode;
        }

        /** Contract operationId that failed. */
        public String operationId() {
            return operationId;
        }
    }
}
