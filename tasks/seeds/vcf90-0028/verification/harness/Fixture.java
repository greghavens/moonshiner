import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The single source of truth for the scenario: the credentials handed to the client, the desired
 * network pool state, and the exact JSON bodies that a conforming client must put on the wire.
 * {@link TestMain} drives the client from these values and {@link VerifyWireShape} asserts against
 * them, so the two can never drift.
 *
 * <p>Harness file. Do not modify.
 */
final class Fixture {

    private Fixture() {
    }

    static final String USERNAME = "administrator@vsphere.local";
    static final String PASSWORD = "VMw@re1!VMw@re1!";
    static final String POOL_NAME = "vcf-np-mgmt-01";

    /** Desired networks, in the order the client must serialize them. */
    static List<VcfNetworkPoolClient.NetworkSpec> desiredNetworks() {
        VcfNetworkPoolClient.NetworkSpec vsan = new VcfNetworkPoolClient.NetworkSpec(
                "VSAN", 3252, 9000, "172.18.12.0", "255.255.255.0", "172.18.12.253",
                List.of(new VcfNetworkPoolClient.IpRange("172.18.12.101", "172.18.12.150")));
        // No IP pool ranges are configured for vMotion: "ipPools" must be absent from the wire.
        VcfNetworkPoolClient.NetworkSpec vmotion = new VcfNetworkPoolClient.NetworkSpec(
                "VMOTION", 3251, 9000, "172.18.11.0", "255.255.255.0", "172.18.11.253",
                List.of());
        return List.of(vsan, vmotion);
    }

    /** The exact JSON body expected for every createToken request. */
    static Map<String, Object> expectedTokenBody() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("username", USERNAME);
        body.put("password", PASSWORD);
        return body;
    }

    /** The exact JSON body expected for the single createNetworkPool request. */
    static Map<String, Object> expectedCreateNetworkPoolBody() {
        Map<String, Object> vsan = new LinkedHashMap<>();
        vsan.put("type", "VSAN");
        vsan.put("vlanId", 3252L);
        vsan.put("mtu", 9000L);
        vsan.put("subnet", "172.18.12.0");
        vsan.put("mask", "255.255.255.0");
        vsan.put("gateway", "172.18.12.253");
        Map<String, Object> range = new LinkedHashMap<>();
        range.put("start", "172.18.12.101");
        range.put("end", "172.18.12.150");
        vsan.put("ipPools", List.of(range));

        Map<String, Object> vmotion = new LinkedHashMap<>();
        vmotion.put("type", "VMOTION");
        vmotion.put("vlanId", 3251L);
        vmotion.put("mtu", 9000L);
        vmotion.put("subnet", "172.18.11.0");
        vmotion.put("mask", "255.255.255.0");
        vmotion.put("gateway", "172.18.11.253");

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", POOL_NAME);
        body.put("networks", List.of(vsan, vmotion));
        return body;
    }

    /**
     * Property names that only exist in the 9.1.0.0 revision of sddc-manager-openapi.json. A client
     * built against the wrong revision leaks these onto the wire.
     */
    static final List<String> FOREIGN_REVISION_PROPERTIES =
            List.of("ipAddressAssignmentMode", "ipAddressVersion", "freeIpCount", "usedIpCount");
}
