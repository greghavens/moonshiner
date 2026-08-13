import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;

/**
 * Standard-library-only client for the SDDC Manager 9.0 capacity-onboarding contract projected in
 * {@code docs/contract.json}.
 *
 * <p>One call to {@link #onboard(CapacityRequest)} walks a multi-step change: sign in, create a
 * network pool, read its networks back, extend some of them with IP pool ranges, and commission
 * hosts into the pool. SDDC Manager applies those steps one at a time and does not undo the earlier
 * ones when a later one is refused, so the returned {@link OnboardingReport} always describes what
 * actually took effect.
 */
public final class VcfCapacityOnboarding {

    static final String CREATE_TOKEN_OPERATION = "createToken";
    static final String CREATE_NETWORK_POOL_OPERATION = "createNetworkPool";
    static final String GET_NETWORKS_OPERATION = "getNetworksOfNetworkPool";
    static final String ADD_IP_POOL_OPERATION = "addIpPoolToNetworkOfNetworkPool";
    static final String COMMISSION_HOSTS_OPERATION = "commissionHosts";

    static final String TOKENS_PATH = "/v1/tokens";
    static final String NETWORK_POOLS_PATH = "/v1/network-pools";
    static final String HOSTS_PATH = "/v1/hosts";

    private static final String JSON_MEDIA_TYPE = "application/json";

    /** {@code Network.type} values enumerated by the specification. */
    static final Set<String> NETWORK_TYPES =
            Set.of("VSAN", "VMOTION", "VXLAN", "NFS", "ISCSI", "VSAN_EXTERNAL");

    /** {@code HostCommissionSpec.storageType} values enumerated by the specification. */
    static final Set<String> STORAGE_TYPES =
            Set.of("VSAN", "VSAN_ESA", "VSAN_REMOTE", "VSAN_MAX", "NFS", "VMFS_FC", "VVOL", "VMFS");

    /** {@code HostCommissionSpec.vvolStorageProtocolType} values enumerated by the specification. */
    static final Set<String> VVOL_PROTOCOL_TYPES = Set.of("ISCSI", "NFS", "FC");

    private final String baseUrl;
    private final Credentials credentials;
    private final HttpClient http;

    public VcfCapacityOnboarding(String baseUrl, Credentials credentials) {
        this.baseUrl = normalizeBaseUrl(baseUrl);
        this.credentials = Objects.requireNonNull(credentials, "credentials");
        this.http = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build();
    }

    // ------------------------------------------------------------------ public API

    /** Credentials for {@code createToken}; only the members that are set reach the wire. */
    public static final class Credentials {
        private final String username;
        private final String password;
        private final String apiKey;
        private final String idToken;

        public Credentials(String username, String password) {
            this(username, password, null, null);
        }

        public Credentials(String username, String password, String apiKey, String idToken) {
            this.username = username;
            this.password = password;
            this.apiKey = apiKey;
            this.idToken = idToken;
        }

        public String username() {
            return username;
        }

        public String password() {
            return password;
        }

        public String apiKey() {
            return apiKey;
        }

        public String idToken() {
            return idToken;
        }
    }

    /** One {@code IpPool} range. */
    public record IpRange(String start, String end) {
        @Override
        public String toString() {
            return start + "-" + end;
        }
    }

    /** One {@code Network} of the pool being created. */
    public static final class NetworkSpec {
        private final String type;
        private final int vlanId;
        private final int mtu;
        private final String subnet;
        private final String mask;
        private final String gateway;
        private final List<IpRange> ipPools;

        public NetworkSpec(String type, int vlanId, int mtu, String subnet, String mask, String gateway,
                List<IpRange> ipPools) {
            this.type = type;
            this.vlanId = vlanId;
            this.mtu = mtu;
            this.subnet = subnet;
            this.mask = mask;
            this.gateway = gateway;
            this.ipPools = ipPools == null ? null : List.copyOf(ipPools);
        }

        public String type() {
            return type;
        }

        public List<IpRange> ipPools() {
            return ipPools;
        }
    }

    /** One {@code addIpPoolToNetworkOfNetworkPool} call, addressed by network type. */
    public record IpPoolAddition(String networkType, IpRange range) {}

    /** One {@code HostCommissionSpec}; the network pool id is supplied by the change itself. */
    public static final class HostCommission {
        private final String fqdn;
        private final String username;
        private final String password;
        private final String storageType;
        private final String vvolStorageProtocolType;
        private final String networkPoolName;
        private final String sshThumbprint;
        private final String sslThumbprint;

        public HostCommission(String fqdn, String username, String password, String storageType) {
            this(fqdn, username, password, storageType, null, null, null, null);
        }

        public HostCommission(String fqdn, String username, String password, String storageType,
                String vvolStorageProtocolType, String networkPoolName, String sshThumbprint, String sslThumbprint) {
            this.fqdn = fqdn;
            this.username = username;
            this.password = password;
            this.storageType = storageType;
            this.vvolStorageProtocolType = vvolStorageProtocolType;
            this.networkPoolName = networkPoolName;
            this.sshThumbprint = sshThumbprint;
            this.sslThumbprint = sslThumbprint;
        }

        public String fqdn() {
            return fqdn;
        }

        public String storageType() {
            return storageType;
        }

        public String vvolStorageProtocolType() {
            return vvolStorageProtocolType;
        }
    }

    /** The whole capacity change, in the order its steps are applied. */
    public static final class CapacityRequest {
        private final String poolName;
        private final List<NetworkSpec> networks;
        private final List<IpPoolAddition> ipPoolAdditions;
        private final List<HostCommission> hosts;

        public CapacityRequest(String poolName, List<NetworkSpec> networks, List<IpPoolAddition> ipPoolAdditions,
                List<HostCommission> hosts) {
            this.poolName = poolName;
            this.networks = networks == null ? null : List.copyOf(networks);
            this.ipPoolAdditions = ipPoolAdditions == null ? List.of() : List.copyOf(ipPoolAdditions);
            this.hosts = hosts == null ? null : List.copyOf(hosts);
        }

        public String poolName() {
            return poolName;
        }

        public List<NetworkSpec> networks() {
            return networks;
        }

        public List<IpPoolAddition> ipPoolAdditions() {
            return ipPoolAdditions;
        }

        public List<HostCommission> hosts() {
            return hosts;
        }
    }

    /** How one planned step of the change ended. */
    public enum StepOutcome {
        SUCCEEDED,
        FAILED,
        NOT_ATTEMPTED
    }

    /** One planned step of the change. */
    public record Step(int index, String operationId, StepOutcome outcome, boolean changedState, String detail) {}

    /** Why the change stopped. */
    public record StepFailure(int stepIndex, String operationId, int httpStatus, String errorCode, String message,
            String referenceToken) {}

    /** What the change actually did to the estate. */
    public static final class OnboardingReport {
        private final List<Step> steps;
        private final boolean completed;
        private final boolean partiallyApplied;
        private final String networkPoolId;
        private final String networkPoolName;
        private final Map<String, String> networkIdsByType;
        private final List<String> appliedIpPoolRanges;
        private final String commissionTaskId;
        private final String commissionTaskStatus;
        private final StepFailure failure;

        OnboardingReport(List<Step> steps, boolean completed, boolean partiallyApplied, String networkPoolId,
                String networkPoolName, Map<String, String> networkIdsByType, List<String> appliedIpPoolRanges,
                String commissionTaskId, String commissionTaskStatus, StepFailure failure) {
            this.steps = List.copyOf(steps);
            this.completed = completed;
            this.partiallyApplied = partiallyApplied;
            this.networkPoolId = networkPoolId;
            this.networkPoolName = networkPoolName;
            this.networkIdsByType = Map.copyOf(networkIdsByType);
            this.appliedIpPoolRanges = List.copyOf(appliedIpPoolRanges);
            this.commissionTaskId = commissionTaskId;
            this.commissionTaskStatus = commissionTaskStatus;
            this.failure = failure;
        }

        public List<Step> steps() {
            return steps;
        }

        public boolean completed() {
            return completed;
        }

        public boolean partiallyApplied() {
            return partiallyApplied;
        }

        public String networkPoolId() {
            return networkPoolId;
        }

        public String networkPoolName() {
            return networkPoolName;
        }

        public Map<String, String> networkIdsByType() {
            return networkIdsByType;
        }

        public List<String> appliedIpPoolRanges() {
            return appliedIpPoolRanges;
        }

        public String commissionTaskId() {
            return commissionTaskId;
        }

        public String commissionTaskStatus() {
            return commissionTaskStatus;
        }

        public StepFailure failure() {
            return failure;
        }
    }

    /** Base type for everything that stops the change without an answer from SDDC Manager. */
    public static class VcfApiException extends RuntimeException {
        private final String operationId;

        public VcfApiException(String operationId, String message) {
            this(operationId, message, null);
        }

        public VcfApiException(String operationId, String message, Throwable cause) {
            super(message, cause);
            this.operationId = operationId;
        }

        public String operationId() {
            return operationId;
        }
    }

    /** The request never produced an HTTP response. */
    public static final class TransportException extends VcfApiException {
        public TransportException(String operationId, String message, Throwable cause) {
            super(operationId, message, cause);
        }
    }

    /** A response arrived but did not match the contract. */
    public static final class ProtocolException extends VcfApiException {
        public ProtocolException(String operationId, String message) {
            super(operationId, message);
        }
    }

    // ------------------------------------------------------------------ the change

    /**
     * Applies the capacity change, stopping at the first step SDDC Manager refuses.
     *
     * @return what the estate looks like afterwards, whether the change ran to the end or not
     */
    public OnboardingReport onboard(CapacityRequest request) {
        validate(request);

        List<String> plan = plan(request);
        List<Step> steps = new ArrayList<>();
        Map<String, String> networkIdsByType = new LinkedHashMap<>();
        List<String> appliedRanges = new ArrayList<>();
        String poolId = null;
        String poolName = null;
        String taskId = null;
        String taskStatus = null;
        StepFailure failure = null;

        int index = 0;

        // Step 0: createToken.
        Outcome token = call(CREATE_TOKEN_OPERATION, "POST", TOKENS_PATH, null,
                Json.write(tokenCreationSpec()), 201);
        if (token.failed()) {
            return abandoned(plan, token.toFailure(index, CREATE_TOKEN_OPERATION));
        }
        String accessToken = requiredString(CREATE_TOKEN_OPERATION, token.body(), "accessToken");
        if (accessToken.isBlank() || !accessToken.chars().allMatch(c -> c >= 0x21 && c <= 0x7E)) {
            throw new ProtocolException(CREATE_TOKEN_OPERATION, "TokenPair.accessToken was not header-safe.");
        }
        steps.add(new Step(index, CREATE_TOKEN_OPERATION, StepOutcome.SUCCEEDED, false,
                "authenticated as " + credentials.username()));
        index++;

        // Step 1: createNetworkPool.
        Outcome created = call(CREATE_NETWORK_POOL_OPERATION, "POST", NETWORK_POOLS_PATH, accessToken,
                Json.write(networkPoolBody(request)), 201);
        if (created.failed()) {
            return abandoned(plan, created.toFailure(index, CREATE_NETWORK_POOL_OPERATION));
        }
        poolId = requiredString(CREATE_NETWORK_POOL_OPERATION, created.body(), "id");
        poolName = requiredString(CREATE_NETWORK_POOL_OPERATION, created.body(), "name");
        steps.add(new Step(index, CREATE_NETWORK_POOL_OPERATION, StepOutcome.SUCCEEDED, true,
                "created network pool " + poolName + " as " + poolId));
        index++;

        // Step 2: getNetworksOfNetworkPool.
        String networksPath = NETWORK_POOLS_PATH + "/" + encodeSegment(poolId) + "/networks";
        Outcome networks = call(GET_NETWORKS_OPERATION, "GET", networksPath, accessToken, null, 200);
        if (networks.failed()) {
            return abandoned(plan, networks.toFailure(index, GET_NETWORKS_OPERATION));
        }
        networkIdsByType.putAll(readNetworkIds(networks.body()));
        steps.add(new Step(index, GET_NETWORKS_OPERATION, StepOutcome.SUCCEEDED, false,
                "resolved " + networkIdsByType.size() + " network ids in pool " + poolId));
        index++;

        // Steps 3..n: addIpPoolToNetworkOfNetworkPool, one call per addition, in request order.
        for (IpPoolAddition addition : request.ipPoolAdditions()) {
            String networkId = networkIdsByType.values().iterator().next();
            String path = NETWORK_POOLS_PATH + "/" + encodeSegment(poolId)
                    + "/networks/" + encodeSegment(networkId) + "/ip-pools";
            Outcome added = call(ADD_IP_POOL_OPERATION, "POST", path, accessToken,
                    Json.write(ipPoolBody(addition.range())), 200);
            if (added.failed()) {
                failure = added.toFailure(index, ADD_IP_POOL_OPERATION);
                steps.add(new Step(index, ADD_IP_POOL_OPERATION, StepOutcome.FAILED, false, refusedDetail(added)));
                index++;
                continue;
            }
            String echoedId = requiredString(ADD_IP_POOL_OPERATION, added.body(), "id");
            if (!echoedId.equals(networkId)) {
                throw new ProtocolException(ADD_IP_POOL_OPERATION,
                        "The response described network " + echoedId + " but network " + networkId + " was addressed.");
            }
            appliedRanges.add(addition.networkType() + " " + addition.range());
            steps.add(new Step(index, ADD_IP_POOL_OPERATION, StepOutcome.SUCCEEDED, true,
                    "added " + addition.range() + " to the " + addition.networkType() + " network " + networkId));
            index++;
        }

        // Final step: commissionHosts.
        Outcome commissioned = call(COMMISSION_HOSTS_OPERATION, "POST", HOSTS_PATH, accessToken,
                Json.write(hostCommissionBody(request.hosts(), poolId)), 202);
        if (commissioned.failed()) {
            failure = commissioned.toFailure(index, COMMISSION_HOSTS_OPERATION);
        } else {
            taskId = requiredString(COMMISSION_HOSTS_OPERATION, commissioned.body(), "id");
            taskStatus = requiredString(COMMISSION_HOSTS_OPERATION, commissioned.body(), "status");
            steps.add(new Step(index, COMMISSION_HOSTS_OPERATION, StepOutcome.SUCCEEDED, true,
                    "accepted task " + taskId + " for " + request.hosts().size() + " hosts"));
        }

        if (failure != null) {
            return abandoned(plan, failure);
        }
        return new OnboardingReport(steps, true, false, poolId, poolName, networkIdsByType, appliedRanges,
                taskId, taskStatus, null);
    }

    /** The operationId of every step this change intends to run, in order. */
    private static List<String> plan(CapacityRequest request) {
        List<String> plan = new ArrayList<>();
        plan.add(CREATE_TOKEN_OPERATION);
        plan.add(CREATE_NETWORK_POOL_OPERATION);
        plan.add(GET_NETWORKS_OPERATION);
        for (int i = 0; i < request.ipPoolAdditions().size(); i++) {
            plan.add(ADD_IP_POOL_OPERATION);
        }
        plan.add(COMMISSION_HOSTS_OPERATION);
        return plan;
    }

    /** Describes a change that did not run to the end. */
    private static OnboardingReport abandoned(List<String> plan, StepFailure failure) {
        List<Step> steps = new ArrayList<>();
        for (int i = 0; i < plan.size(); i++) {
            steps.add(i == failure.stepIndex()
                    ? new Step(i, plan.get(i), StepOutcome.FAILED, false,
                            "refused with HTTP " + failure.httpStatus())
                    : new Step(i, plan.get(i), StepOutcome.NOT_ATTEMPTED, false, "not attempted"));
        }
        return new OnboardingReport(steps, false, false, null, null, Map.of(), List.of(), null, null, failure);
    }

    private static String refusedDetail(Outcome outcome) {
        String code = outcome.errorCode();
        return "refused with HTTP " + outcome.status() + (code == null ? "" : " " + code);
    }

    // ------------------------------------------------------------------ validation

    private void validate(CapacityRequest request) {
        Objects.requireNonNull(request, "request");
        requireText(request.poolName(), "poolName");
        if (credentials.username() == null || credentials.username().isBlank()
                || credentials.password() == null || credentials.password().isBlank()) {
            throw new IllegalArgumentException("credentials must carry a username and a password");
        }
        if (request.networks() == null || request.networks().isEmpty()) {
            throw new IllegalArgumentException("the network pool needs at least one network");
        }

        Set<String> types = new LinkedHashSet<>();
        for (NetworkSpec network : request.networks()) {
            Objects.requireNonNull(network, "network");
            requireText(network.type, "network type");
            if (!NETWORK_TYPES.contains(network.type)) {
                throw new IllegalArgumentException("network type " + network.type + " is not an enumerated type");
            }
            if (!types.add(network.type)) {
                throw new IllegalArgumentException("network type " + network.type + " appears twice");
            }
            if (network.vlanId < 0 || network.vlanId > 4094) {
                throw new IllegalArgumentException("vlanId " + network.vlanId + " is out of range");
            }
            if (network.mtu < 1280 || network.mtu > 9190) {
                throw new IllegalArgumentException("mtu " + network.mtu + " is out of range");
            }
            requireText(network.subnet, "subnet");
            requireText(network.mask, "mask");
            requireText(network.gateway, "gateway");
            if (network.ipPools != null) {
                for (IpRange range : network.ipPools) {
                    requireRange(range);
                }
            }
        }

        for (IpPoolAddition addition : request.ipPoolAdditions()) {
            Objects.requireNonNull(addition, "ipPoolAddition");
            requireText(addition.networkType(), "ipPoolAddition network type");
            if (!types.contains(addition.networkType())) {
                throw new IllegalArgumentException(
                        "no network of type " + addition.networkType() + " is being created");
            }
            requireRange(addition.range());
        }

        if (request.hosts() == null || request.hosts().isEmpty()) {
            throw new IllegalArgumentException("at least one host must be commissioned");
        }
        Set<String> seen = new HashSet<>();
        for (HostCommission host : request.hosts()) {
            Objects.requireNonNull(host, "host");
            requireText(host.fqdn, "host fqdn");
            requireText(host.username, "host username");
            requireText(host.password, "host password");
            requireText(host.storageType, "host storageType");
            if (!STORAGE_TYPES.contains(host.storageType)) {
                throw new IllegalArgumentException(
                        "storageType " + host.storageType + " is not an enumerated storage type");
            }
            if ("VVOL".equals(host.storageType)) {
                if (host.vvolStorageProtocolType == null
                        || !VVOL_PROTOCOL_TYPES.contains(host.vvolStorageProtocolType)) {
                    throw new IllegalArgumentException(
                            "a VVOL host needs an enumerated vvolStorageProtocolType");
                }
            } else if (host.vvolStorageProtocolType != null) {
                throw new IllegalArgumentException(
                        "vvolStorageProtocolType only belongs on a VVOL host");
            }
            if (!seen.add(host.fqdn.toLowerCase(Locale.ROOT))) {
                throw new IllegalArgumentException("host " + host.fqdn + " is named twice");
            }
        }
    }

    private static void requireRange(IpRange range) {
        Objects.requireNonNull(range, "ip range");
        requireText(range.start(), "ip range start");
        requireText(range.end(), "ip range end");
    }

    private static void requireText(String value, String what) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(what + " must be a nonblank string");
        }
    }

    // ------------------------------------------------------------------ serialization

    /** {@code TokenCreationSpec}: username, password, apiKey, idToken. */
    private Map<String, Object> tokenCreationSpec() {
        Map<String, Object> spec = new LinkedHashMap<>();
        spec.put("username", orEmpty(credentials.username()));
        spec.put("password", orEmpty(credentials.password()));
        spec.put("apiKey", orEmpty(credentials.apiKey()));
        spec.put("idToken", orEmpty(credentials.idToken()));
        return spec;
    }

    /** {@code NetworkPool}: name, networks; the read-only members are never sent. */
    private static Map<String, Object> networkPoolBody(CapacityRequest request) {
        List<Object> networks = new ArrayList<>();
        for (NetworkSpec network : request.networks()) {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("type", network.type);
            body.put("vlanId", (long) network.vlanId);
            body.put("mtu", (long) network.mtu);
            body.put("subnet", network.subnet);
            body.put("mask", network.mask);
            body.put("gateway", network.gateway);
            List<Object> pools = new ArrayList<>();
            if (network.ipPools != null) {
                for (IpRange range : network.ipPools) {
                    pools.add(ipPoolBody(range));
                }
            }
            body.put("ipPools", pools);
            networks.add(body);
        }
        Map<String, Object> pool = new LinkedHashMap<>();
        pool.put("name", request.poolName());
        pool.put("networks", networks);
        return pool;
    }

    /** {@code IpPool}: start, end. */
    private static Map<String, Object> ipPoolBody(IpRange range) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("start", range.start());
        body.put("end", range.end());
        return body;
    }

    /** An array of {@code HostCommissionSpec} in caller order. */
    private static List<Object> hostCommissionBody(List<HostCommission> hosts, String networkPoolId) {
        List<Object> specs = new ArrayList<>();
        for (HostCommission host : hosts) {
            Map<String, Object> spec = new TreeMap<>();
            spec.put("fqdn", orEmpty(host.fqdn));
            spec.put("username", orEmpty(host.username));
            spec.put("password", orEmpty(host.password));
            spec.put("storageType", orEmpty(host.storageType));
            spec.put("vvolStorageProtocolType", orEmpty(host.vvolStorageProtocolType));
            spec.put("networkPoolId", orEmpty(networkPoolId));
            spec.put("networkPoolName", orEmpty(host.networkPoolName));
            spec.put("sshThumbprint", orEmpty(host.sshThumbprint));
            spec.put("sslThumbprint", orEmpty(host.sslThumbprint));
            specs.add(spec);
        }
        return specs;
    }

    private static String orEmpty(String value) {
        return value == null ? "" : value;
    }

    // ------------------------------------------------------------------ responses

    private static Map<String, String> readNetworkIds(Object page) {
        Map<String, String> ids = new LinkedHashMap<>();
        Object elements = Json.object(page).get("elements");
        if (!(elements instanceof List<?> networks)) {
            throw new ProtocolException(GET_NETWORKS_OPERATION, "PageOfNetwork carried no elements array.");
        }
        for (Object element : networks) {
            Map<String, Object> network = Json.object(element);
            Object id = network.get("id");
            Object type = network.get("type");
            if (!(id instanceof String networkId) || !(type instanceof String networkType)) {
                throw new ProtocolException(GET_NETWORKS_OPERATION, "A Network carried no id or no type.");
            }
            ids.put(networkType, networkId);
        }
        if (ids.isEmpty()) {
            throw new ProtocolException(GET_NETWORKS_OPERATION, "The created pool reported no networks.");
        }
        return ids;
    }

    private static String requiredString(String operationId, Object body, String member) {
        Object value = Json.object(body).get(member);
        if (!(value instanceof String text)) {
            throw new ProtocolException(operationId, "The response carried no string member " + member + ".");
        }
        return text;
    }

    // ------------------------------------------------------------------ transport

    /** One completed exchange: either the expected status with a parsed body, or a refusal. */
    private record Outcome(int status, Object body, String errorCode, String message, String referenceToken) {

        boolean failed() {
            return body == null;
        }

        StepFailure toFailure(int index, String operationId) {
            return new StepFailure(index, operationId, status, errorCode, message, referenceToken);
        }
    }

    private Outcome call(String operationId, String method, String path, String accessToken, String body,
            int expectedStatus) {
        HttpRequest.Builder builder;
        try {
            builder = HttpRequest.newBuilder(new URI(baseUrl + path));
        } catch (URISyntaxException malformed) {
            throw new IllegalArgumentException("baseUrl and path do not form a URI: " + baseUrl + path, malformed);
        }
        builder.header("Accept", JSON_MEDIA_TYPE);
        if (accessToken != null) {
            builder.header("Authorization", "Bearer " + accessToken);
        }
        if (body == null) {
            builder.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            builder.header("Content-Type", JSON_MEDIA_TYPE);
            builder.method(method, HttpRequest.BodyPublishers.ofByteArray(body.getBytes(StandardCharsets.UTF_8)));
        }

        HttpResponse<String> response;
        try {
            response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException failure) {
            throw new TransportException(operationId, operationId + " did not reach SDDC Manager", failure);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new TransportException(operationId, operationId + " was interrupted", interrupted);
        }

        if (response.statusCode() != expectedStatus) {
            Map<String, Object> error = parseErrorBody(response.body());
            return new Outcome(response.statusCode(), null,
                    optionalString(error, "errorCode"),
                    optionalString(error, "message"),
                    optionalString(error, "referenceToken"));
        }
        requireJsonMediaType(operationId, response);
        Object parsed;
        try {
            parsed = Json.parse(response.body());
        } catch (RuntimeException malformed) {
            throw new ProtocolException(operationId, operationId + " returned a body that is not JSON");
        }
        if (!(parsed instanceof Map)) {
            throw new ProtocolException(operationId, operationId + " returned a body that is not a JSON object");
        }
        return new Outcome(response.statusCode(), parsed, null, null, null);
    }

    private static Map<String, Object> parseErrorBody(String body) {
        try {
            Object parsed = Json.parse(body);
            return parsed instanceof Map ? Json.object(parsed) : Map.of();
        } catch (RuntimeException notJson) {
            return Map.of();
        }
    }

    private static String optionalString(Map<String, Object> body, String member) {
        Object value = body.get(member);
        return value instanceof String text ? text : null;
    }

    private static void requireJsonMediaType(String operationId, HttpResponse<String> response) {
        Optional<String> contentType = response.headers().firstValue("Content-Type");
        if (contentType.isEmpty() || !contentType.get().toLowerCase(Locale.ROOT).contains(JSON_MEDIA_TYPE)) {
            throw new ProtocolException(operationId, operationId + " did not answer with the JSON media type");
        }
    }

    private static String normalizeBaseUrl(String baseUrl) {
        requireText(baseUrl, "baseUrl");
        String trimmed = baseUrl.trim();
        while (trimmed.endsWith("/")) {
            trimmed = trimmed.substring(0, trimmed.length() - 1);
        }
        if (!trimmed.startsWith("http://") && !trimmed.startsWith("https://")) {
            throw new IllegalArgumentException("baseUrl must be an http or https URL");
        }
        return trimmed;
    }

    /** Percent-encodes an id so that only RFC 3986 unreserved characters survive into one path segment. */
    static String encodeSegment(String value) {
        StringBuilder out = new StringBuilder();
        for (byte raw : value.getBytes(StandardCharsets.UTF_8)) {
            int b = raw & 0xFF;
            boolean unreserved = (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') || (b >= '0' && b <= '9')
                    || b == '-' || b == '.' || b == '_' || b == '~';
            if (unreserved) {
                out.append((char) b);
            } else {
                out.append('%').append(String.format("%02X", b));
            }
        }
        return out.toString();
    }

    // ------------------------------------------------------------------ minimal JSON

    /** Just enough JSON to write the request bodies and read the responses this contract defines. */
    static final class Json {

        private Json() {}

        @SuppressWarnings("unchecked")
        static Map<String, Object> object(Object value) {
            if (!(value instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object");
            }
            return (Map<String, Object>) value;
        }

        static String write(Object value) {
            StringBuilder out = new StringBuilder();
            writeValue(value, out);
            return out.toString();
        }

        private static void writeValue(Object value, StringBuilder out) {
            switch (value) {
                case null -> out.append("null");
                case String text -> writeString(text, out);
                case Boolean flag -> out.append(flag);
                case Long number -> out.append(number.longValue());
                case Integer number -> out.append(number.intValue());
                case Map<?, ?> map -> {
                    out.append('{');
                    boolean first = true;
                    for (Map.Entry<?, ?> entry : map.entrySet()) {
                        if (!first) {
                            out.append(',');
                        }
                        first = false;
                        writeString(String.valueOf(entry.getKey()), out);
                        out.append(':');
                        writeValue(entry.getValue(), out);
                    }
                    out.append('}');
                }
                case List<?> items -> {
                    out.append('[');
                    for (int i = 0; i < items.size(); i++) {
                        if (i > 0) {
                            out.append(',');
                        }
                        writeValue(items.get(i), out);
                    }
                    out.append(']');
                }
                default -> throw new IllegalArgumentException("cannot serialize " + value.getClass());
            }
        }

        private static void writeString(String text, StringBuilder out) {
            out.append('"');
            for (int i = 0; i < text.length(); i++) {
                char c = text.charAt(i);
                switch (c) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    case '\b' -> out.append("\\b");
                    case '\f' -> out.append("\\f");
                    default -> {
                        if (c < 0x20) {
                            out.append(String.format("\\u%04x", (int) c));
                        } else {
                            out.append(c);
                        }
                    }
                }
            }
            out.append('"');
        }

        static Object parse(String text) {
            Reader reader = new Reader(text);
            reader.skipWhitespace();
            Object value = reader.readValue();
            reader.skipWhitespace();
            if (!reader.atEnd()) {
                throw new IllegalArgumentException("trailing content in JSON input");
            }
            return value;
        }

        private static final class Reader {
            private final String text;
            private int cursor;

            Reader(String text) {
                this.text = text;
            }

            boolean atEnd() {
                return cursor >= text.length();
            }

            void skipWhitespace() {
                while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) {
                    cursor++;
                }
            }

            Object readValue() {
                if (atEnd()) {
                    throw new IllegalArgumentException("unexpected end of JSON input");
                }
                return switch (text.charAt(cursor)) {
                    case '{' -> readObject();
                    case '[' -> readArray();
                    case '"' -> readString();
                    case 't' -> readLiteral("true", Boolean.TRUE);
                    case 'f' -> readLiteral("false", Boolean.FALSE);
                    case 'n' -> readLiteral("null", null);
                    default -> readNumber();
                };
            }

            private Object readLiteral(String literal, Object value) {
                if (!text.startsWith(literal, cursor)) {
                    throw new IllegalArgumentException("bad JSON literal");
                }
                cursor += literal.length();
                return value;
            }

            private Map<String, Object> readObject() {
                Map<String, Object> map = new LinkedHashMap<>();
                cursor++;
                skipWhitespace();
                if (!atEnd() && text.charAt(cursor) == '}') {
                    cursor++;
                    return map;
                }
                while (true) {
                    skipWhitespace();
                    String key = readString();
                    skipWhitespace();
                    expect(':');
                    skipWhitespace();
                    map.put(key, readValue());
                    skipWhitespace();
                    char next = next();
                    if (next == '}') {
                        return map;
                    }
                    if (next != ',') {
                        throw new IllegalArgumentException("expected , or } in JSON object");
                    }
                }
            }

            private List<Object> readArray() {
                List<Object> items = new ArrayList<>();
                cursor++;
                skipWhitespace();
                if (!atEnd() && text.charAt(cursor) == ']') {
                    cursor++;
                    return items;
                }
                while (true) {
                    skipWhitespace();
                    items.add(readValue());
                    skipWhitespace();
                    char next = next();
                    if (next == ']') {
                        return items;
                    }
                    if (next != ',') {
                        throw new IllegalArgumentException("expected , or ] in JSON array");
                    }
                }
            }

            private String readString() {
                expect('"');
                StringBuilder out = new StringBuilder();
                while (true) {
                    char c = next();
                    if (c == '"') {
                        return out.toString();
                    }
                    if (c != '\\') {
                        out.append(c);
                        continue;
                    }
                    char escape = next();
                    switch (escape) {
                        case '"' -> out.append('"');
                        case '\\' -> out.append('\\');
                        case '/' -> out.append('/');
                        case 'b' -> out.append('\b');
                        case 'f' -> out.append('\f');
                        case 'n' -> out.append('\n');
                        case 'r' -> out.append('\r');
                        case 't' -> out.append('\t');
                        case 'u' -> {
                            out.append((char) Integer.parseInt(text.substring(cursor, cursor + 4), 16));
                            cursor += 4;
                        }
                        default -> throw new IllegalArgumentException("bad JSON escape");
                    }
                }
            }

            private Object readNumber() {
                int start = cursor;
                while (cursor < text.length() && "+-0123456789.eE".indexOf(text.charAt(cursor)) >= 0) {
                    cursor++;
                }
                String literal = text.substring(start, cursor);
                if (literal.isEmpty()) {
                    throw new IllegalArgumentException("expected a JSON value");
                }
                if (literal.indexOf('.') < 0 && literal.indexOf('e') < 0 && literal.indexOf('E') < 0) {
                    return Long.parseLong(literal);
                }
                return Double.parseDouble(literal);
            }

            private void expect(char expected) {
                if (next() != expected) {
                    throw new IllegalArgumentException("expected " + expected + " in JSON input");
                }
            }

            private char next() {
                if (atEnd()) {
                    throw new IllegalArgumentException("unexpected end of JSON input");
                }
                return text.charAt(cursor++);
            }
        }
    }
}
