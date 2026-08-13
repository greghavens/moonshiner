import java.net.http.HttpClient;
import java.util.ArrayList;
import java.util.List;

/**
 * Client for the application-tier slice of the VCF Operations for Networks 9.1 API.
 *
 * <p>The pinned contract in {@code docs/contract.json} names exactly three operations:
 * {@code getApplicationById}, {@code listApplicationTiers} and {@code addTier}. Only
 * {@code addTier} mutates, and the server does not deduplicate: two successful {@code addTier}
 * calls carrying the same tier name create two tiers. {@link #ensureTier} is the retry-safe
 * wrapper that callers use instead.
 *
 * <p>Only the JDK and the supplied {@link Json} codec are available; there is no third-party
 * HTTP or JSON dependency.
 */
public final class NiTierClient {

    private final String baseUrl;
    private final String apiToken;
    private final HttpClient http;

    /**
     * @param baseUrl  origin of the VCF Operations for Networks appliance, for example
     *                 {@code http://127.0.0.1:8080}. It may or may not carry a trailing slash.
     * @param apiToken bare token value; the {@code Authorization} header format is applied here.
     */
    public NiTierClient(String baseUrl, String apiToken) {
        this(baseUrl, apiToken, HttpClient.newHttpClient());
    }

    /** Package-private transport seam used by the deterministic in-process contract fixture. */
    NiTierClient(String baseUrl, String apiToken, HttpClient http) {
        this.baseUrl = baseUrl;
        this.apiToken = apiToken;
        this.http = http;
    }

    /**
     * Bring the named tier into existence inside {@code applicationId} exactly once.
     *
     * <p>Repeating this call with the same arguments must converge on the same tier and must not
     * create a second one. See {@code docs/contract.json} for the wire encoding and the
     * conflict signal the API uses.
     *
     * @throws NiApiException when the application does not exist, when the API reports an error
     *                        that is not a name conflict, or when the transport fails.
     */
    public EnsureResult ensureTier(String applicationId, TierSpec spec) {
        // TODO: implement against docs/contract.json.
        throw new UnsupportedOperationException("ensureTier is not implemented");
    }

    /** Raised for API and transport failures. {@code statusCode} is 0 when no response arrived. */
    public static class NiApiException extends RuntimeException {
        private final int statusCode;

        public NiApiException(int statusCode, String message) {
            super(message);
            this.statusCode = statusCode;
        }

        public NiApiException(int statusCode, String message, Throwable cause) {
            super(message, cause);
            this.statusCode = statusCode;
        }

        public int statusCode() {
            return statusCode;
        }
    }

    /** Outcome of {@link #ensureTier}. */
    public static final class EnsureResult {
        private final boolean created;
        private final String tierId;
        private final String tierName;

        public EnsureResult(boolean created, String tierId, String tierName) {
            this.created = created;
            this.tierId = tierId;
            this.tierName = tierName;
        }

        /** True only when this call is the one that created the tier. */
        public boolean created() {
            return created;
        }

        public String tierId() {
            return tierId;
        }

        public String tierName() {
            return tierName;
        }
    }

    /** A member of a tier's member list. {@code name} is optional. */
    public static final class Member {
        private final String entityId;
        private final String entityType;
        private final String name;

        public Member(String entityId, String entityType, String name) {
            this.entityId = entityId;
            this.entityType = entityType;
            this.name = name;
        }

        public String entityId() {
            return entityId;
        }

        public String entityType() {
            return entityType;
        }

        public String name() {
            return name;
        }
    }

    /**
     * Desired state of one tier. Every field except {@code name} is optional; a field left unset
     * here must not reach the wire at all.
     */
    public static final class TierSpec {
        private final String name;
        private String searchEntityType;
        private String searchFilter;
        private List<String> ipAddresses = List.of();
        private List<Member> vms = List.of();
        private List<Member> physicalIps = List.of();
        private List<Member> kubernetesServices = List.of();
        private List<String> sourceGroupEntityIds = List.of();

        public TierSpec(String name) {
            this.name = name;
        }

        /** Adds a search membership criterion. Both arguments are required together. */
        public TierSpec searchCriteria(String entityType, String filter) {
            this.searchEntityType = entityType;
            this.searchFilter = filter;
            return this;
        }

        /** Adds an IP address membership criterion covering the given addresses, CIDRs or ranges. */
        public TierSpec ipAddresses(List<String> values) {
            this.ipAddresses = copy(values);
            return this;
        }

        public TierSpec vms(List<Member> values) {
            this.vms = copyMembers(values);
            return this;
        }

        public TierSpec physicalIps(List<Member> values) {
            this.physicalIps = copyMembers(values);
            return this;
        }

        public TierSpec kubernetesServices(List<Member> values) {
            this.kubernetesServices = copyMembers(values);
            return this;
        }

        public TierSpec sourceGroupEntityIds(List<String> values) {
            this.sourceGroupEntityIds = copy(values);
            return this;
        }

        public String name() {
            return name;
        }

        public String searchEntityType() {
            return searchEntityType;
        }

        public String searchFilter() {
            return searchFilter;
        }

        public List<String> ipAddresses() {
            return ipAddresses;
        }

        public List<Member> vms() {
            return vms;
        }

        public List<Member> physicalIps() {
            return physicalIps;
        }

        public List<Member> kubernetesServices() {
            return kubernetesServices;
        }

        public List<String> sourceGroupEntityIds() {
            return sourceGroupEntityIds;
        }

        private static List<String> copy(List<String> values) {
            return values == null ? List.of() : List.copyOf(values);
        }

        private static List<Member> copyMembers(List<Member> values) {
            return values == null ? List.of() : List.copyOf(new ArrayList<>(values));
        }
    }
}
