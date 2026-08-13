import java.util.List;

/**
 * Client for the VMware Cloud Foundation 9.0 SDDC Manager network pool operations.
 *
 * <p>Implement {@link #ensureNetworkPool(String, List)} so that it drives the appliance towards the
 * requested state exactly once, no matter how often it is called or how the appliance answers. The
 * wire contract for the three operations it may use lives in {@code docs/contract.json}.
 *
 * <p>The public surface below is what the harness compiles against; the value types are already
 * complete. Everything else in this file is yours: the whole client must stay in this single file
 * and may use nothing beyond the Java standard library.
 */
public final class VcfNetworkPoolClient {

    /** An inclusive range of IPv4 addresses handed to a network of the pool. */
    public static final class IpRange {
        public final String start;
        public final String end;

        public IpRange(String start, String end) {
            this.start = start;
            this.end = end;
        }
    }

    /** The desired state of one network inside the pool. */
    public static final class NetworkSpec {
        public final String type;
        public final int vlanId;
        public final int mtu;
        public final String subnet;
        public final String mask;
        public final String gateway;
        /** IP ranges for this network; empty when the network has none configured. */
        public final List<IpRange> ipPools;

        public NetworkSpec(String type, int vlanId, int mtu, String subnet, String mask,
                           String gateway, List<IpRange> ipPools) {
            this.type = type;
            this.vlanId = vlanId;
            this.mtu = mtu;
            this.subnet = subnet;
            this.mask = mask;
            this.gateway = gateway;
            this.ipPools = List.copyOf(ipPools);
        }
    }

    private final String baseUrl;
    private final String username;
    private final String password;

    /**
     * @param baseUrl scheme, host and port of the SDDC Manager appliance, with no trailing slash,
     *                for example {@code http://127.0.0.1:8080}
     */
    public VcfNetworkPoolClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl;
        this.username = username;
        this.password = password;
    }

    /**
     * Makes sure a network pool named {@code poolName} with the given networks exists on the
     * appliance, and returns its id.
     *
     * <p>Calling this twice, or calling it again after a failed attempt, must leave exactly one such
     * pool behind and must return the same id.
     *
     * @return the id of the network pool that exists on the appliance
     */
    public String ensureNetworkPool(String poolName, List<NetworkSpec> networks) throws Exception {
        throw new UnsupportedOperationException("ensureNetworkPool is not implemented yet");
    }
}
