package com.broadcom.vcfops.networks;

/**
 * Client for the vCenter data source onboarding flow of VCF Operations for Networks 9.1.
 *
 * <p>The wire contract this client must speak is {@code docs/contract.json}, which is derived from
 * the product's OpenAPI document (see {@code docs/official_sources.json}). Everything below the
 * public surface -- HTTP, JSON, gating -- is yours to implement in this one file.
 *
 * <p>The public surface declared here is what the harness compiles against. Keep the type names,
 * field names and the {@code onboardVcenter} signature exactly as they are; change the body.
 */
public final class VcfOpsNetworksClient {

    /** Stage names reported on {@link OnboardOutcome#stage}. */
    public static final String STAGE_AUTHENTICATE = "AUTHENTICATE";
    public static final String STAGE_RESOLVE_COLLECTOR = "RESOLVE_COLLECTOR";
    public static final String STAGE_PRECHECK = "PRECHECK";
    public static final String STAGE_CREATE = "CREATE";

    /** No collector node matched {@link VcenterOnboardRequest#collectorNodeName}. */
    public static final String FAILURE_COLLECTOR_NOT_FOUND = "COLLECTOR_NOT_FOUND";
    /** The precheck ran and refused the data source. */
    public static final String FAILURE_PRECHECK_REJECTED = "PRECHECK_REJECTED";
    /** The appliance answered with an unexpected HTTP status. */
    public static final String FAILURE_HTTP_ERROR = "HTTP_ERROR";

    private final String baseUrl;

    /**
     * @param baseUrl scheme, host and port of the appliance, with no trailing slash and no API
     *                base path -- for example {@code http://127.0.0.1:8443}. The base path from
     *                the contract still has to be applied to every request.
     */
    public VcfOpsNetworksClient(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    /** Base URL this client was constructed with. */
    public String baseUrl() {
        return baseUrl;
    }

    /**
     * Onboards one vCenter Server as a data source.
     *
     * <p>The flow is: obtain an auth token, resolve the named collector node to its id, run the
     * precheck for the data source, and only if the precheck passed, create it. A precheck that
     * refuses the data source must leave the appliance untouched.
     *
     * @param apiCredentials credentials for the appliance itself; never {@code null}
     * @param apiDomain      authentication domain, or {@code null} when the caller did not pick one
     * @param request        the vCenter to onboard
     * @return what happened, including how far the flow got
     * @throws IllegalArgumentException if the request is not self-consistent, before any request is
     *                                  sent -- in particular when neither or both of
     *                                  {@link VcenterOnboardRequest#ip} and
     *                                  {@link VcenterOnboardRequest#fqdn} are set, or when
     *                                  {@code collectorNodeName}, {@code nickname} or
     *                                  {@code vcenterCredentials} are missing
     */
    public OnboardOutcome onboardVcenter(Credentials apiCredentials,
                                         Domain apiDomain,
                                         VcenterOnboardRequest request) {
        throw new UnsupportedOperationException("onboardVcenter is not implemented yet");
    }

    // ------------------------------------------------------------------ types

    /** Username/password pair. A {@code null} password means the caller did not supply one. */
    public static final class Credentials {
        public final String username;
        public final String password;

        public Credentials(String username, String password) {
            this.username = username;
            this.password = password;
        }
    }

    /** Authentication domain. {@code value} is {@code null} when the caller did not supply one. */
    public static final class Domain {
        public final String domainType;
        public final String value;

        public Domain(String domainType, String value) {
            this.domainType = domainType;
            this.value = value;
        }
    }

    /**
     * What the caller wants onboarded. A {@code null} field means "the caller did not set this";
     * such fields must not reach the wire at all.
     */
    public static final class VcenterOnboardRequest {
        /** IP address of the vCenter. Exactly one of {@code ip} / {@code fqdn} must be set. */
        public String ip;
        /** Hostname of the vCenter. Exactly one of {@code ip} / {@code fqdn} must be set. */
        public String fqdn;
        /** Name of the collector node that should register this vCenter. Required. */
        public String collectorNodeName;
        /** Friendly nickname for the data source. Required. */
        public String nickname;
        /** Credentials the appliance should use against the vCenter. Required. */
        public Credentials vcenterCredentials;
        /** Optional free-text notes. */
        public String notes;
        /** Optional; whether data collection starts enabled. */
        public Boolean enabled;
        /** Optional; whether the appliance should configure the vCenter to send IPFIX. */
        public Boolean ipfixEnabled;
        /** Optional; whether this is a VMware Cloud operated vCenter. */
        public Boolean isVmc;
    }

    /** Result of {@link #onboardVcenter}. */
    public static final class OnboardOutcome {
        /** True only when the data source was created. */
        public final boolean succeeded;
        /** The stage the flow reached; one of the {@code STAGE_*} constants. */
        public final String stage;
        /** One of the {@code FAILURE_*} constants, or {@code null} on success. */
        public final String failureCode;
        /** Human readable failure detail, or {@code null} on success. */
        public final String failureMessage;
        /** Resolved collector node id, or {@code null} if the flow never got that far. */
        public final String proxyId;
        /** {@code code} from the precheck response, or {@code null} if the precheck never ran. */
        public final Integer precheckCode;
        /** {@code message} from the precheck response, or {@code null} if it never ran. */
        public final String precheckMessage;
        /** Entity id of the created data source, or {@code null} when nothing was created. */
        public final String entityId;

        public OnboardOutcome(boolean succeeded, String stage, String failureCode,
                              String failureMessage, String proxyId, Integer precheckCode,
                              String precheckMessage, String entityId) {
            this.succeeded = succeeded;
            this.stage = stage;
            this.failureCode = failureCode;
            this.failureMessage = failureMessage;
            this.proxyId = proxyId;
            this.precheckCode = precheckCode;
            this.precheckMessage = precheckMessage;
            this.entityId = entityId;
        }
    }
}
