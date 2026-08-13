/**
 * Single-file client for the two VCF Operations adapter operations named in {@code docs/contract.json}.
 *
 * <p>Onboarding an adapter instance is a two-step flow: {@code testConnection} is a precheck, and
 * {@code createAdapterInstance} is the mutation it gates. The mutation must not be issued unless the
 * precheck succeeded.
 *
 * <p>Implement every member below. The public shape is fixed: {@code TestMain} compiles against it.
 * Use only the JDK; no third-party libraries are available.
 */
public final class OpsAdapterClient {

    /**
     * @param origin scheme, host and port of the VCF Operations appliance, with no path
     *     (for example {@code http://127.0.0.1:8443}). The contract's server base path is not
     *     included and must be added by this client.
     * @param authorizationHeaderValue opaque value for the {@code Authorization} header; send it
     *     verbatim, do not re-encode or wrap it.
     */
    public OpsAdapterClient(String origin, String authorizationHeaderValue) {
        throw new UnsupportedOperationException("not implemented");
    }

    /** Equivalent to {@code onboard(spec, false)}: the optional query parameter is not sent. */
    public OnboardResult onboard(AdapterInstanceSpec spec) throws Exception {
        throw new UnsupportedOperationException("not implemented");
    }

    /**
     * Runs the precheck and, only if it succeeded, creates the adapter instance.
     *
     * @param spec the adapter instance to test and then create
     * @param extractIdentifierDefaults when {@code true}, request the corresponding optional query
     *     parameter on the mutation; when {@code false}, leave it off the request entirely
     * @return what happened in each phase; never {@code null}
     */
    public OnboardResult onboard(AdapterInstanceSpec spec, boolean extractIdentifierDefaults) throws Exception {
        throw new UnsupportedOperationException("not implemented");
    }

    /**
     * A {@code create-adapter-instance} payload. {@code name} and {@code adapterKindKey} are
     * required; every other property is optional and, when the caller did not set it, must be
     * absent from the serialized request body.
     */
    public static final class AdapterInstanceSpec {

        public static Builder builder(String name, String adapterKindKey) {
            throw new UnsupportedOperationException("not implemented");
        }

        public static final class Builder {

            public Builder description(String description) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Builder collectorId(String collectorId) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Builder collectorGroupId(String collectorGroupId) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Builder physicalDatacenterId(String physicalDatacenterId) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Builder monitoringInterval(int minutes) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Builder monitoringIntervalSeconds(int seconds) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Builder credential(Credential credential) {
                throw new UnsupportedOperationException("not implemented");
            }

            /** Appends one {@code name-value} entry to {@code resourceIdentifiers}, in call order. */
            public Builder addResourceIdentifier(String name, String value) {
                throw new UnsupportedOperationException("not implemented");
            }

            public AdapterInstanceSpec build() {
                throw new UnsupportedOperationException("not implemented");
            }
        }
    }

    /**
     * A {@code credential} payload. {@code name}, {@code adapterKindKey} and
     * {@code credentialKindKey} are required; {@code fields} is optional, and {@code id} and
     * {@code editable} are never sent by this client.
     */
    public static final class Credential {

        public static Builder builder(String name, String adapterKindKey, String credentialKindKey) {
            throw new UnsupportedOperationException("not implemented");
        }

        public static final class Builder {

            /** Appends one {@code name-value} entry to {@code fields}, in call order. */
            public Builder addField(String name, String value) {
                throw new UnsupportedOperationException("not implemented");
            }

            public Credential build() {
                throw new UnsupportedOperationException("not implemented");
            }
        }
    }

    /** Outcome of one onboarding attempt. */
    public static final class OnboardResult {

        /** {@code true} when the precheck returned its success status. */
        public boolean precheckPassed() {
            throw new UnsupportedOperationException("not implemented");
        }

        /** HTTP status the precheck returned. */
        public int precheckStatus() {
            throw new UnsupportedOperationException("not implemented");
        }

        /** Explanation the service gave for a failed precheck, or {@code null} when it passed. */
        public String precheckDetail() {
            throw new UnsupportedOperationException("not implemented");
        }

        /** {@code true} when the adapter instance was created. */
        public boolean created() {
            throw new UnsupportedOperationException("not implemented");
        }

        /** HTTP status the mutation returned, or {@code 0} when it was not attempted. */
        public int createStatus() {
            throw new UnsupportedOperationException("not implemented");
        }

        /** Explanation the service gave for a failed mutation, or {@code null} otherwise. */
        public String createDetail() {
            throw new UnsupportedOperationException("not implemented");
        }

        /** Identifier of the created adapter instance, or {@code null} when none was created. */
        public String adapterInstanceId() {
            throw new UnsupportedOperationException("not implemented");
        }
    }
}
