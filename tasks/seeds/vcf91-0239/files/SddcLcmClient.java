/**
 * Client for the VMware Cloud Foundation 9.1 SDDC LCM Service.
 *
 * Requests a support bundle for a FLEET-scoped component in a way that is safe to
 * re-run: resubmitting with the same correlation key must adopt the task already in
 * flight instead of starting a second bundle generation.
 *
 * The wire contract is docs/contract.json; its provenance is docs/official_sources.json.
 * The whole client belongs in this one compilation unit.
 */
public final class SddcLcmClient {

    public SddcLcmClient(String baseUrl, String bearerToken) {
        throw new UnsupportedOperationException(
                "Implement the contract-pinned SDDC LCM support-bundle client.");
    }

    /**
     * Requests a support bundle for the fleet component with the given FQDN, exactly
     * once per correlation key.
     *
     * @param componentFqdn  FQDN of a FLEET-scoped component
     * @param correlationId  the caller's idempotency key
     * @param lookBackWindow optional look-back window; null leaves it off the wire
     */
    public SupportBundleRequestResult requestSupportBundle(String componentFqdn,
                                                           String correlationId,
                                                           Integer lookBackWindow)
            throws SddcLcmException {
        throw new UnsupportedOperationException(
                "Implement the contract-pinned SDDC LCM support-bundle client.");
    }

    /** Outcome of a support-bundle request: either a fresh submission or an adopted task. */
    public static final class SupportBundleRequestResult {
        public final String componentId;
        public final String taskId;
        public final String taskStatus;
        /** True when an existing task carrying the same correlation key was reused. */
        public final boolean adopted;

        SupportBundleRequestResult(String componentId, String taskId, String taskStatus,
                                   boolean adopted) {
            this.componentId = componentId;
            this.taskId = taskId;
            this.taskStatus = taskStatus;
            this.adopted = adopted;
        }
    }

    /** Any failure talking to SDDC LCM. httpStatus is 0 for purely client-side failures. */
    public static final class SddcLcmException extends Exception {
        private static final long serialVersionUID = 1L;

        public final int httpStatus;
        public final String errorCode;
        public final String referenceId;

        SddcLcmException(int httpStatus, String errorCode, String referenceId, String message) {
            super(message);
            this.httpStatus = httpStatus;
            this.errorCode = errorCode;
            this.referenceId = referenceId;
        }
    }
}
