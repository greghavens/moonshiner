import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        verifySourceLayout();
        String inventoryText = read("estate-inventory.json");
        String snapshotText = read("compatibility-snapshot.json");
        String artifactText = ArchitectureClient.build(inventoryText, snapshotText);
        check(artifactText != null && !artifactText.isBlank(), "ArchitectureClient returned no artifact");

        Map<String, Object> artifact = object(Json.parse(artifactText), "$artifact");
        Object sddcSpecValue = required(artifact, "sddcSpec", "$artifact");

        // This is intentionally the first substantive verification. The candidate SddcSpec
        // is checked against the schema from the vendored installer OpenAPI document before
        // any fixture, compatibility, migration-schema, or design assertion is evaluated.
        Map<String, Object> openapi = object(
                Json.parse(read("specifications/vcf-installer/vcf-installer-openapi.json")), "$openapi");
        Map<String, Object> components = object(required(openapi, "components", "$openapi"), "$openapi.components");
        Map<String, Object> schemas = object(required(components, "schemas", "$openapi.components"),
                "$openapi.components.schemas");
        Map<String, Object> sddcSchema = object(required(schemas, "SddcSpec", "$openapi.components.schemas"),
                "$openapi.components.schemas.SddcSpec");
        new SchemaValidator(openapi).validate(sddcSpecValue, sddcSchema, "$artifact.sddcSpec");
        System.out.println("PASS installer OpenAPI SddcSpec validation");

        equal(Set.of("sddcSpec", "migrationPlan", "researchConsulted"), artifact.keySet(),
                "top-level deliverables");

        Object migrationPlanValue = required(artifact, "migrationPlan", "$artifact");
        Map<String, Object> migrationSchema = object(Json.parse(read("migration-plan-schema.json")),
                "$migrationSchema");
        new SchemaValidator(migrationSchema).validate(migrationPlanValue, migrationSchema,
                "$artifact.migrationPlan");
        System.out.println("PASS migration-plan schema validation");

        verifyResearch(array(required(artifact, "researchConsulted", "$artifact"),
                "$artifact.researchConsulted"));
        System.out.println("PASS recorded Broadcom research sources");

        Map<String, Object> inventory = object(Json.parse(inventoryText), "$inventory");
        Map<String, Object> snapshot = object(Json.parse(snapshotText), "$snapshot");
        verifySddc(object(sddcSpecValue, "$artifact.sddcSpec"), inventory);
        System.out.println("PASS greenfield capacity, availability, site, and network design");

        verifyMigration(object(migrationPlanValue, "$artifact.migrationPlan"), inventory, snapshot);
        System.out.println("PASS complete snapshot-compatible ordered migration plan");
        System.out.println("ALL TESTS PASSED");
    }

    private static void verifySourceLayout() throws IOException {
        Set<String> javaSources = new HashSet<>();
        try (var entries = Files.walk(Path.of("."))) {
            entries.filter(Files::isRegularFile)
                    .map(Path::normalize)
                    .map(Path::toString)
                    .filter(name -> name.endsWith(".java"))
                    .map(name -> name.startsWith("./") ? name.substring(2) : name)
                    .forEach(javaSources::add);
        }
        equal(Set.of("ArchitectureClient.java", "TestMain.java"), javaSources,
                "Java source-file layout");
    }

    private static void verifyResearch(List<Object> sources) {
        check(sources.size() >= 2,
                "researchConsulted must record multiple sources covering compatibility and upgrade research");
        Set<String> urls = new HashSet<>();
        for (int i = 0; i < sources.size(); i++) {
            String path = "$artifact.researchConsulted[" + i + "]";
            Map<String, Object> source = object(sources.get(i), path);
            check(!string(source, "title", path).isBlank(), path + ".title must not be blank");
            String url = string(source, "url", path);
            check(urls.add(url), path + ".url duplicates an earlier research source");
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException e) {
                throw new AssertionError(path + ".url must be a valid URI");
            }
            String host = uri.getHost();
            check("https".equalsIgnoreCase(uri.getScheme()) && host != null,
                    path + ".url must be an absolute HTTPS URL");
            String lowerHost = host.toLowerCase(java.util.Locale.ROOT);
            check(lowerHost.equals("broadcom.com") || lowerHost.endsWith(".broadcom.com"),
                    path + ".url must identify a Broadcom-published source");
        }
    }

    private static void verifySddc(Map<String, Object> sddc, Map<String, Object> inventory) {
        Map<String, Object> req = object(required(inventory, "targetRequirements", "$inventory"),
                "$inventory.targetRequirements");
        Map<String, Object> site = child(req, "site", "$requirements");
        Map<String, Object> capacity = child(req, "capacity", "$requirements");
        Map<String, Object> availability = child(req, "availability", "$requirements");
        Map<String, Object> appliances = child(req, "appliances", "$requirements");
        Map<String, Object> managementServices = child(req, "managementServices", "$requirements");
        Map<String, Object> cluster = child(req, "cluster", "$requirements");

        equal(string(req, "release", "$requirements"), string(sddc, "version", "$sddc"),
                "target VCF release");
        equal(string(req, "workflowType", "$requirements"), string(sddc, "workflowType", "$sddc"),
                "greenfield workflow type");
        equal(string(site, "sddcId", "$site"), string(sddc, "sddcId", "$sddc"), "site SDDC id");
        equal(string(site, "vcfInstanceName", "$site"), string(sddc, "vcfInstanceName", "$sddc"),
                "VCF instance name");
        equal(1, integer(site, "siteCount", "$site"), "single-site requirement");
        equal("SINGLE_SITE_NON_STRETCHED", string(site, "topology", "$site"), "site topology fixture");

        List<Object> requiredHosts = array(required(req, "hostnames", "$requirements"), "$requirements.hostnames");
        List<Object> hostSpecs = array(required(sddc, "hostSpecs", "$sddc"), "$sddc.hostSpecs");
        equal(integer(capacity, "managementHostCount", "$capacity"), hostSpecs.size(), "management host count");
        equal(stringSet(requiredHosts, "$requirements.hostnames"), propertyStringSet(hostSpecs, "hostname", "$hostSpecs"),
                "management host inventory");
        equal("N_PLUS_1", string(availability, "requiredHostSpareModel", "$availability"),
                "host spare model fixture");

        Map<String, Object> dnsExpected = child(req, "dns", "$requirements");
        Map<String, Object> dnsActual = child(sddc, "dnsSpec", "$sddc");
        equal(string(dnsExpected, "subdomain", "$dnsExpected"), string(dnsActual, "subdomain", "$dnsActual"),
                "DNS subdomain");
        equal(stringList(required(dnsExpected, "nameservers", "$dnsExpected"), "$dnsExpected.nameservers"),
                stringList(required(dnsActual, "nameservers", "$dnsActual"), "$dnsActual.nameservers"),
                "redundant DNS servers");
        equal(stringList(required(req, "ntpServers", "$requirements"), "$requirements.ntpServers"),
                stringList(required(sddc, "ntpServers", "$sddc"), "$sddc.ntpServers"), "redundant NTP servers");

        verifyNetworks(sddc, req);
        verifySwitches(sddc, req);

        Map<String, Object> vc = child(sddc, "vcenterSpec", "$sddc");
        equal(string(appliances, "vcenterHostname", "$appliances"), string(vc, "vcenterHostname", "$vc"),
                "vCenter hostname");
        equal(string(appliances, "vcenterVmSize", "$appliances"), string(vc, "vmSize", "$vc"),
                "vCenter size");
        equal(string(appliances, "vcenterRootPasswordPlaceholder", "$appliances"),
                string(vc, "rootVcenterPassword", "$vc"), "vCenter design placeholder");

        Map<String, Object> sddcManager = child(sddc, "sddcManagerSpec", "$sddc");
        equal(string(appliances, "sddcManagerHostname", "$appliances"),
                string(sddcManager, "hostname", "$sddcManager"), "SDDC Manager hostname");

        Map<String, Object> clusterActual = child(sddc, "clusterSpec", "$sddc");
        equal(string(cluster, "datacenterName", "$cluster"), string(clusterActual, "datacenterName", "$clusterActual"),
                "datacenter name");
        equal(string(cluster, "clusterName", "$cluster"), string(clusterActual, "clusterName", "$clusterActual"),
                "management cluster name");

        Map<String, Object> datastore = child(sddc, "datastoreSpec", "$sddc");
        Map<String, Object> vsan = child(datastore, "vsanSpec", "$datastore");
        equal(string(cluster, "datastoreName", "$cluster"), string(vsan, "datastoreName", "$vsan"),
                "vSAN datastore name");
        equal(integer(availability, "hostFailuresToTolerate", "$availability"),
                integer(vsan, "failuresToTolerate", "$vsan"), "vSAN failures to tolerate");
        Map<String, Object> esa = child(vsan, "esaConfig", "$vsan");
        check(booleanValue(esa, "enabled", "$esa"), "vSAN ESA must be enabled");
        equal("ESA", string(cluster, "vsanArchitecture", "$cluster"), "vSAN architecture fixture");

        Map<String, Object> nsx = child(sddc, "nsxtSpec", "$sddc");
        equal(string(appliances, "nsxVipFqdn", "$appliances"), string(nsx, "vipFqdn", "$nsx"), "NSX VIP");
        Map<String, Object> physical = child(req, "physicalNetwork", "$requirements");
        equal(integer(physical, "transportVlanId", "$physical"), integer(nsx, "transportVlanId", "$nsx"),
                "NSX transport VLAN");
        List<Object> nsxManagers = array(required(nsx, "nsxtManagers", "$nsx"), "$nsx.nsxtManagers");
        equal(integer(availability, "nsxManagerNodeCount", "$availability"), nsxManagers.size(),
                "NSX manager node count");
        equal(stringSet(array(required(appliances, "nsxManagerHostnames", "$appliances"), "$appliances.nsx"),
                        "$appliances.nsx"),
                propertyStringSet(nsxManagers, "hostname", "$nsxManagers"), "NSX manager hostnames");

        Map<String, Object> operations = child(sddc, "vcfOperationsSpec", "$sddc");
        equal(string(capacity, "vcfOperationsApplianceSize", "$capacity"),
                string(operations, "applianceSize", "$operations"), "VCF Operations capacity size");
        equal(string(appliances, "vcfOperationsLoadBalancerFqdn", "$appliances"),
                string(operations, "loadBalancerFqdn", "$operations"), "VCF Operations load balancer");
        List<Object> operationNodes = array(required(operations, "nodes", "$operations"), "$operations.nodes");
        List<String> expectedTypes = stringList(required(availability, "vcfOperationsNodeTypes", "$availability"),
                "$availability.vcfOperationsNodeTypes");
        List<String> expectedOperationHosts = stringList(required(appliances, "vcfOperationsHostnames", "$appliances"),
                "$appliances.vcfOperationsHostnames");
        equal(expectedTypes.size(), operationNodes.size(), "VCF Operations HA node count");
        Set<String> nodePairs = new LinkedHashSet<>();
        for (Object nodeValue : operationNodes) {
            Map<String, Object> node = object(nodeValue, "$operations.nodes[]");
            nodePairs.add(string(node, "hostname", "$node") + "|" + string(node, "type", "$node"));
        }
        Set<String> expectedPairs = new LinkedHashSet<>();
        for (int i = 0; i < expectedTypes.size(); i++) {
            expectedPairs.add(expectedOperationHosts.get(i) + "|" + expectedTypes.get(i));
        }
        equal(expectedPairs, nodePairs, "VCF Operations HA node roles");

        Map<String, Object> vsp = child(sddc, "vspClusterSpec", "$sddc");
        equal(string(appliances, "vspPlatformFqdn", "$appliances"), string(vsp, "platformFqdn", "$vsp"),
                "VSP platform FQDN");
        equal(string(appliances, "vspInstanceFqdn", "$appliances"), string(vsp, "instanceFqdn", "$vsp"),
                "VSP instance FQDN");
        equal(string(appliances, "vspFleetFqdn", "$appliances"), string(vsp, "fleetFqdn", "$vsp"),
                "VSP fleet FQDN");
        equal(string(managementServices, "clusterSize", "$managementServices"), string(vsp, "size", "$vsp"),
                "VSP cluster size");
        equal(string(managementServices, "internalClusterCidrIpv4", "$managementServices"),
                string(vsp, "internalClusterCidrIpv4", "$vsp"), "VSP non-overlapping internal CIDR");
        Map<String, Object> ipv4Pool = child(vsp, "ipv4Pool", "$vsp");
        equal(string(managementServices, "ipv4Cidr", "$managementServices"), string(ipv4Pool, "cidr", "$ipv4Pool"),
                "management-services pool CIDR");
        Map<String, Object> vspRange = child(ipv4Pool, "ipRange", "$ipv4Pool");
        equal(string(managementServices, "startIpAddress", "$managementServices"),
                string(vspRange, "startIpAddress", "$vspRange"), "management-services pool start");
        equal(string(managementServices, "endIpAddress", "$managementServices"),
                string(vspRange, "endIpAddress", "$vspRange"), "management-services pool end");
        equal(integer(managementServices, "reservedAddressCount", "$managementServices"),
                ipv4Count(string(vspRange, "startIpAddress", "$vspRange"),
                        string(vspRange, "endIpAddress", "$vspRange")),
                "management-services reserved address count");

        Map<String, Object> infra = child(sddc, "vcfManagementComponentsInfrastructureSpec", "$sddc");
        check(!infra.containsKey("xRegionNetwork"), "single-site non-stretched design must not define xRegionNetwork");
        Map<String, Object> localNetwork = child(infra, "localRegionNetwork", "$infra");
        equal(string(managementServices, "networkName", "$managementServices"),
                string(localNetwork, "networkName", "$localNetwork"), "management-services network name");
        Map<String, Object> fleetNetwork = networkByType(sddc, "FLEET_MANAGEMENT");
        equal(string(fleetNetwork, "gateway", "$fleetNetwork"), string(localNetwork, "gateway", "$localNetwork"),
                "management-services gateway");
        equal(string(fleetNetwork, "subnetMask", "$fleetNetwork"),
                string(localNetwork, "subnetMask", "$localNetwork"), "management-services subnet mask");

        Map<String, Object> license = child(sddc, "licenseServerSpec", "$sddc");
        equal(string(appliances, "licenseServerHostname", "$appliances"), string(license, "hostname", "$license"),
                "license server hostname");

        check(!booleanValue(sddc, "skipEsxThumbprintValidation", "$sddc"),
                "ESXi thumbprint validation must remain enabled");
        check(!booleanValue(sddc, "skipGatewayPingValidation", "$sddc"),
                "gateway ping validation must remain enabled");
        rejectExistingDeployments(sddc, "$sddc");
    }

    private static void verifyNetworks(Map<String, Object> sddc, Map<String, Object> req) {
        List<Object> expected = array(required(req, "networks", "$requirements"), "$requirements.networks");
        List<Object> actual = array(required(sddc, "networkSpecs", "$sddc"), "$sddc.networkSpecs");
        equal(expected.size(), actual.size(), "network count");
        Map<String, Map<String, Object>> actualByType = new LinkedHashMap<>();
        for (Object value : actual) {
            Map<String, Object> network = object(value, "$sddc.networkSpecs[]");
            String type = string(network, "networkType", "$network");
            check(actualByType.put(type, network) == null, "duplicate network type " + type);
        }
        for (Object value : expected) {
            Map<String, Object> wanted = object(value, "$requirements.networks[]");
            String type = string(wanted, "networkType", "$networkRequirement");
            Map<String, Object> got = actualByType.get(type);
            check(got != null, "missing network " + type);
            for (String field : List.of("subnet", "gateway", "subnetMask", "portGroupKey")) {
                equal(string(wanted, field, "$networkRequirement"), string(got, field, "$network"),
                        type + " " + field);
            }
            equal(integer(wanted, "vlanId", "$networkRequirement"), integer(got, "vlanId", "$network"),
                    type + " VLAN");
            equal(integer(wanted, "mtu", "$networkRequirement"), integer(got, "mtu", "$network"), type + " MTU");
            equal("IPv4", string(got, "ipAddressVersion", "$network"), type + " IP family");
            equal("STATIC", string(got, "ipAddressAssignmentMode", "$network"), type + " address mode");
            List<Object> ranges = array(required(got, "includeIpAddressRanges", "$network"), "$network.ranges");
            equal(1, ranges.size(), type + " IP range count");
            Map<String, Object> range = object(ranges.get(0), "$network.range");
            equal(string(wanted, "startIpAddress", "$networkRequirement"),
                    string(range, "startIpAddress", "$range"), type + " range start");
            equal(string(wanted, "endIpAddress", "$networkRequirement"), string(range, "endIpAddress", "$range"),
                    type + " range end");
        }
    }

    private static void verifySwitches(Map<String, Object> sddc, Map<String, Object> req) {
        Map<String, Object> physical = child(req, "physicalNetwork", "$requirements");
        List<Object> expected = array(required(physical, "distributedSwitches", "$physical"), "$physical.switches");
        List<Object> actual = array(required(sddc, "dvsSpecs", "$sddc"), "$sddc.dvsSpecs");
        equal(expected.size(), actual.size(), "distributed switch count");
        Map<String, Map<String, Object>> actualByName = new HashMap<>();
        for (Object value : actual) {
            Map<String, Object> dvs = object(value, "$sddc.dvsSpecs[]");
            actualByName.put(string(dvs, "dvsName", "$dvs"), dvs);
        }
        for (Object value : expected) {
            Map<String, Object> wanted = object(value, "$physical.switches[]");
            String name = string(wanted, "name", "$switchRequirement");
            Map<String, Object> got = actualByName.get(name);
            check(got != null, "missing distributed switch " + name);
            equal(9000, integer(got, "mtu", "$dvs"), name + " MTU");
            equal(stringSet(array(required(wanted, "traffic", "$switchRequirement"), "$switchRequirement.traffic"),
                            "$switchRequirement.traffic"),
                    stringSet(array(required(got, "networks", "$dvs"), "$dvs.networks"), "$dvs.networks"),
                    name + " traffic placement");
            List<Object> mappings = array(required(got, "vmnicsToUplinks", "$dvs"), "$dvs.vmnicsToUplinks");
            equal(2, mappings.size(), name + " redundant uplink count");
            equal(stringSet(array(required(wanted, "vmnics", "$switchRequirement"), "$switchRequirement.vmnics"),
                            "$switchRequirement.vmnics"),
                    propertyStringSet(mappings, "id", "$mappings"), name + " physical NICs");
            equal(2, propertyStringSet(mappings, "uplink", "$mappings").size(),
                    name + " distinct uplinks");
        }
    }

    private static void verifyMigration(Map<String, Object> plan, Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        equal(string(inventory, "estateId", "$inventory"), string(plan, "estateId", "$plan"), "estate id");
        equal(string(snapshot, "targetRelease", "$snapshot"), string(plan, "targetRelease", "$plan"),
                "migration target release");
        List<Object> inventoryComponents = array(required(inventory, "components", "$inventory"),
                "$inventory.components");
        List<Object> snapshotComponents = array(required(snapshot, "components", "$snapshot"),
                "$snapshot.components");
        List<Object> steps = array(required(plan, "steps", "$plan"), "$plan.steps");
        equal(inventoryComponents.size(), steps.size(), "one migration step per inventoried component");
        equal(snapshotComponents.size(), steps.size(), "snapshot coverage");

        Map<String, Map<String, Object>> inventoryById = index(inventoryComponents, "id", "$inventory.components");
        Map<String, Map<String, Object>> snapshotById = index(snapshotComponents, "componentId", "$snapshot.components");
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < steps.size(); i++) {
            Map<String, Object> step = object(steps.get(i), "$plan.steps[" + i + "]");
            equal(i + 1, integer(step, "order", "$step"), "contiguous migration order");
            String id = string(step, "componentId", "$step");
            check(seen.add(id), "duplicate migration component " + id);
            Map<String, Object> installed = inventoryById.get(id);
            Map<String, Object> authority = snapshotById.get(id);
            check(installed != null, "migration plan names a component absent from inventory: " + id);
            check(authority != null, "migration plan names a component absent from snapshot: " + id);
            equal(string(installed, "name", "$installed"), string(step, "component", "$step"),
                    id + " component name");
            String source = string(installed, "version", "$installed");
            equal(source, string(step, "sourceVersion", "$step"), id + " source version");
            List<String> accepted = stringList(required(authority, "acceptedSourceVersions", "$authority"),
                    "$authority.acceptedSourceVersions");
            check(accepted.contains(source), "pinned snapshot does not accept source version for " + id);
            equal(integer(authority, "order", "$authority"), integer(step, "order", "$step"),
                    id + " compatibility order");
            equal(string(authority, "target", "$authority"), string(step, "target", "$step"), id + " target");
            equal(string(authority, "action", "$authority"), string(step, "action", "$step"), id + " action");
            equal(new LinkedHashSet<>(stringList(required(authority, "requiredGates", "$authority"),
                            "$authority.requiredGates")),
                    new LinkedHashSet<>(stringList(required(step, "gates", "$step"), "$step.gates")), id + " gates");
        }
        equal(inventoryById.keySet(), seen, "all inventory components covered exactly once");

        Map<String, Object> networkAuthority = snapshotById.get("network-observability");
        equal("END_OF_SERVICE", string(networkAuthority, "supportState", "$networkAuthority"),
                "pinned end-of-service boundary");
        check(!booleanValue(networkAuthority, "directInPlaceSupported", "$networkAuthority"),
                "end-of-service network product must not have a direct in-place path");
    }

    private static Map<String, Map<String, Object>> index(List<Object> values, String key, String path) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, path + "[]");
            String id = string(item, key, path + "[]");
            check(result.put(id, item) == null, "duplicate " + key + " " + id + " in " + path);
        }
        return result;
    }

    private static Map<String, Object> networkByType(Map<String, Object> sddc, String type) {
        List<Object> networks = array(required(sddc, "networkSpecs", "$sddc"), "$sddc.networkSpecs");
        for (Object value : networks) {
            Map<String, Object> network = object(value, "$sddc.networkSpecs[]");
            if (type.equals(string(network, "networkType", "$network"))) {
                return network;
            }
        }
        throw new AssertionError("missing network " + type);
    }

    private static void rejectExistingDeployments(Object value, String path) {
        if (value instanceof Map<?, ?> raw) {
            Map<String, Object> map = castMap(raw);
            if (Boolean.TRUE.equals(map.get("useExistingDeployment"))) {
                throw new AssertionError(path + " marks a deployment as existing");
            }
            for (Map.Entry<String, Object> entry : map.entrySet()) {
                rejectExistingDeployments(entry.getValue(), path + "." + entry.getKey());
            }
        } else if (value instanceof List<?> list) {
            for (int i = 0; i < list.size(); i++) {
                rejectExistingDeployments(list.get(i), path + "[" + i + "]");
            }
        }
    }

    private static int ipv4Count(String start, String end) {
        long first = ipv4(start);
        long last = ipv4(end);
        check(last >= first, "invalid IPv4 range " + start + "-" + end);
        long count = last - first + 1;
        check(count <= Integer.MAX_VALUE, "IPv4 range too large");
        return (int) count;
    }

    private static long ipv4(String text) {
        String[] parts = text.split("\\.", -1);
        check(parts.length == 4, "invalid IPv4 address " + text);
        long value = 0;
        for (String part : parts) {
            int octet;
            try {
                octet = Integer.parseInt(part);
            } catch (NumberFormatException e) {
                throw new AssertionError("invalid IPv4 address " + text);
            }
            check(octet >= 0 && octet <= 255, "invalid IPv4 address " + text);
            value = (value << 8) | octet;
        }
        return value;
    }

    private static String read(String relative) throws IOException {
        Path direct = Path.of(relative);
        Path path = Files.exists(direct) ? direct : Path.of("files").resolve(relative);
        return Files.readString(path, StandardCharsets.UTF_8);
    }

    private static Map<String, Object> child(Map<String, Object> parent, String key, String path) {
        return object(required(parent, key, path), path + "." + key);
    }

    private static Object required(Map<String, Object> map, String key, String path) {
        check(map.containsKey(key), "missing " + path + "." + key);
        return map.get(key);
    }

    private static String string(Map<String, Object> map, String key, String path) {
        Object value = required(map, key, path);
        check(value instanceof String, path + "." + key + " must be a string");
        return (String) value;
    }

    private static int integer(Map<String, Object> map, String key, String path) {
        Object value = required(map, key, path);
        check(value instanceof BigDecimal, path + "." + key + " must be an integer");
        try {
            return ((BigDecimal) value).intValueExact();
        } catch (ArithmeticException e) {
            throw new AssertionError(path + "." + key + " must be an integer");
        }
    }

    private static boolean booleanValue(Map<String, Object> map, String key, String path) {
        Object value = required(map, key, path);
        check(value instanceof Boolean, path + "." + key + " must be a boolean");
        return (Boolean) value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String path) {
        check(value instanceof Map<?, ?>, path + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Map<?, ?> value) {
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String path) {
        check(value instanceof List<?>, path + " must be an array");
        return (List<Object>) value;
    }

    private static List<String> stringList(Object value, String path) {
        List<Object> raw = array(value, path);
        List<String> result = new ArrayList<>();
        for (int i = 0; i < raw.size(); i++) {
            check(raw.get(i) instanceof String, path + "[" + i + "] must be a string");
            result.add((String) raw.get(i));
        }
        return result;
    }

    private static Set<String> stringSet(List<Object> value, String path) {
        return new LinkedHashSet<>(stringList(value, path));
    }

    private static Set<String> propertyStringSet(List<Object> values, String property, String path) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            Map<String, Object> map = object(value, path + "[]");
            result.add(string(map, property, path + "[]"));
        }
        return result;
    }

    private static void equal(Object expected, Object actual, String label) {
        check(expected.equals(actual), label + " mismatch: expected " + expected + ", got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class SchemaValidator {
        private final Object root;

        private SchemaValidator(Object root) {
            this.root = root;
        }

        private void validate(Object instance, Object schemaValue, String path) {
            if (schemaValue instanceof Boolean allowed) {
                if (!allowed) {
                    fail(path, "is rejected by a false schema");
                }
                return;
            }
            Map<String, Object> schema = objectSchema(schemaValue, path);
            if (schema.containsKey("$ref")) {
                validate(instance, resolve((String) schema.get("$ref")), path);
            }
            validateCombinators(instance, schema, path);
            validateType(instance, schema, path);
            validateConstAndEnum(instance, schema, path);
            if (instance instanceof Map<?, ?> raw) {
                validateObject(castMap(raw), schema, path);
            } else if (instance instanceof List<?> list) {
                validateArray(list, schema, path);
            } else if (instance instanceof String text) {
                validateString(text, schema, path);
            } else if (instance instanceof BigDecimal number) {
                validateNumber(number, schema, path);
            }
        }

        private void validateCombinators(Object instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("allOf")) {
                for (Object branch : arraySchema(schema.get("allOf"), path + ".allOf")) {
                    validate(instance, branch, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                int matches = branchMatches(instance, arraySchema(schema.get("anyOf"), path + ".anyOf"), path);
                if (matches == 0) {
                    fail(path, "does not match anyOf");
                }
            }
            if (schema.containsKey("oneOf")) {
                int matches = branchMatches(instance, arraySchema(schema.get("oneOf"), path + ".oneOf"), path);
                if (matches != 1) {
                    fail(path, "must match exactly one oneOf branch, matched " + matches);
                }
            }
            if (schema.containsKey("not")) {
                try {
                    validate(instance, schema.get("not"), path);
                } catch (SchemaFailure expected) {
                    return;
                }
                fail(path, "matches a forbidden not schema");
            }
        }

        private int branchMatches(Object instance, List<Object> branches, String path) {
            int matches = 0;
            for (Object branch : branches) {
                try {
                    validate(instance, branch, path);
                    matches++;
                } catch (SchemaFailure ignored) {
                    // A non-matching branch is expected while evaluating a combinator.
                }
            }
            return matches;
        }

        private void validateType(Object instance, Map<String, Object> schema, String path) {
            if (!schema.containsKey("type")) {
                return;
            }
            Object type = schema.get("type");
            boolean matches;
            if (type instanceof String text) {
                matches = isType(instance, text);
            } else {
                matches = false;
                for (Object option : arraySchema(type, path + ".type")) {
                    if (option instanceof String && isType(instance, (String) option)) {
                        matches = true;
                    }
                }
            }
            if (!matches) {
                fail(path, "does not have schema type " + type);
            }
        }

        private boolean isType(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "number" -> value instanceof BigDecimal;
                case "integer" -> value instanceof BigDecimal
                        && ((BigDecimal) value).stripTrailingZeros().scale() <= 0;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> true;
            };
        }

        private void validateConstAndEnum(Object instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("const") && !jsonEqual(instance, schema.get("const"))) {
                fail(path, "does not equal const " + schema.get("const"));
            }
            if (schema.containsKey("enum")) {
                boolean matched = false;
                for (Object candidate : arraySchema(schema.get("enum"), path + ".enum")) {
                    matched |= jsonEqual(instance, candidate);
                }
                if (!matched) {
                    fail(path, "is not in enum");
                }
            }
        }

        private void validateObject(Map<String, Object> instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("required")) {
                for (Object name : arraySchema(schema.get("required"), path + ".required")) {
                    if (name instanceof String && !instance.containsKey(name)) {
                        fail(path, "is missing required property " + name);
                    }
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? objectSchema(schema.get("properties"), path + ".properties")
                    : Map.of();
            for (Map.Entry<String, Object> property : properties.entrySet()) {
                if (instance.containsKey(property.getKey())) {
                    validate(instance.get(property.getKey()), property.getValue(), path + "." + property.getKey());
                }
            }
            Object additional = schema.get("additionalProperties");
            if (Boolean.FALSE.equals(additional)) {
                for (String key : instance.keySet()) {
                    if (!properties.containsKey(key)) {
                        fail(path, "has forbidden additional property " + key);
                    }
                }
            } else if (additional instanceof Map<?, ?> || additional instanceof Boolean) {
                for (Map.Entry<String, Object> entry : instance.entrySet()) {
                    if (!properties.containsKey(entry.getKey())) {
                        validate(entry.getValue(), additional, path + "." + entry.getKey());
                    }
                }
            }
            checkBound(instance.size(), schema, "minProperties", true, path);
            checkBound(instance.size(), schema, "maxProperties", false, path);
        }

        private void validateArray(List<?> instance, Map<String, Object> schema, String path) {
            checkBound(instance.size(), schema, "minItems", true, path);
            checkBound(instance.size(), schema, "maxItems", false, path);
            if (schema.containsKey("items")) {
                for (int i = 0; i < instance.size(); i++) {
                    validate(instance.get(i), schema.get("items"), path + "[" + i + "]");
                }
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                for (int i = 0; i < instance.size(); i++) {
                    for (int j = i + 1; j < instance.size(); j++) {
                        if (jsonEqual(instance.get(i), instance.get(j))) {
                            fail(path, "contains duplicate items");
                        }
                    }
                }
            }
        }

        private void validateString(String instance, Map<String, Object> schema, String path) {
            int length = instance.codePointCount(0, instance.length());
            checkBound(length, schema, "minLength", true, path);
            checkBound(length, schema, "maxLength", false, path);
            if (schema.containsKey("pattern")) {
                String expression = (String) schema.get("pattern");
                if (!Pattern.compile(expression).matcher(instance).find()) {
                    fail(path, "does not match pattern " + expression);
                }
            }
        }

        private void validateNumber(BigDecimal instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("minimum")) {
                BigDecimal minimum = number(schema.get("minimum"), path + ".minimum");
                if (instance.compareTo(minimum) < 0) {
                    fail(path, "is below minimum " + minimum);
                }
            }
            if (schema.containsKey("maximum")) {
                BigDecimal maximum = number(schema.get("maximum"), path + ".maximum");
                if (instance.compareTo(maximum) > 0) {
                    fail(path, "is above maximum " + maximum);
                }
            }
            if (schema.containsKey("exclusiveMinimum")) {
                BigDecimal minimum = number(schema.get("exclusiveMinimum"), path + ".exclusiveMinimum");
                if (instance.compareTo(minimum) <= 0) {
                    fail(path, "is not above exclusiveMinimum " + minimum);
                }
            }
            if (schema.containsKey("exclusiveMaximum")) {
                BigDecimal maximum = number(schema.get("exclusiveMaximum"), path + ".exclusiveMaximum");
                if (instance.compareTo(maximum) >= 0) {
                    fail(path, "is not below exclusiveMaximum " + maximum);
                }
            }
        }

        private void checkBound(int actual, Map<String, Object> schema, String keyword, boolean minimum, String path) {
            if (!schema.containsKey(keyword)) {
                return;
            }
            int limit = number(schema.get(keyword), path + "." + keyword).intValueExact();
            if ((minimum && actual < limit) || (!minimum && actual > limit)) {
                fail(path, "violates " + keyword + " " + limit);
            }
        }

        private Object resolve(String ref) {
            if (!ref.startsWith("#/")) {
                throw new SchemaFailure("Only local schema references are supported: " + ref);
            }
            Object current = root;
            for (String encoded : ref.substring(2).split("/")) {
                String token = encoded.replace("~1", "/").replace("~0", "~");
                Map<String, Object> map = objectSchema(current, ref);
                if (!map.containsKey(token)) {
                    throw new SchemaFailure("Unresolved schema reference " + ref);
                }
                current = map.get(token);
            }
            return current;
        }

        @SuppressWarnings("unchecked")
        private Map<String, Object> objectSchema(Object value, String path) {
            if (!(value instanceof Map<?, ?>)) {
                throw new SchemaFailure(path + " is not a schema object");
            }
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        private List<Object> arraySchema(Object value, String path) {
            if (!(value instanceof List<?>)) {
                throw new SchemaFailure(path + " is not a schema array");
            }
            return (List<Object>) value;
        }

        private BigDecimal number(Object value, String path) {
            if (!(value instanceof BigDecimal)) {
                throw new SchemaFailure(path + " is not a schema number");
            }
            return (BigDecimal) value;
        }

        private boolean jsonEqual(Object left, Object right) {
            if (left instanceof BigDecimal a && right instanceof BigDecimal b) {
                return a.compareTo(b) == 0;
            }
            return left == null ? right == null : left.equals(right);
        }

        private void fail(String path, String message) {
            throw new SchemaFailure("Schema validation failed at " + path + ": " + message);
        }
    }

    private static final class SchemaFailure extends RuntimeException {
        private SchemaFailure(String message) {
            super(message);
        }
    }

    private static final class Json {
        private Json() {
        }

        private static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.value();
            parser.whitespace();
            if (!parser.end()) {
                throw parser.error("unexpected trailing content");
            }
            return value;
        }

        private static final class Parser {
            private final String text;
            private int offset;

            private Parser(String text) {
                this.text = text;
            }

            private Object value() {
                whitespace();
                if (end()) {
                    throw error("expected a JSON value");
                }
                return switch (text.charAt(offset)) {
                    case '{' -> object();
                    case '[' -> array();
                    case '"' -> string();
                    case 't' -> literal("true", Boolean.TRUE);
                    case 'f' -> literal("false", Boolean.FALSE);
                    case 'n' -> literal("null", null);
                    default -> number();
                };
            }

            private Map<String, Object> object() {
                expect('{');
                LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                whitespace();
                if (consume('}')) {
                    return result;
                }
                while (true) {
                    whitespace();
                    if (end() || text.charAt(offset) != '"') {
                        throw error("expected an object key");
                    }
                    String key = string();
                    whitespace();
                    expect(':');
                    if (result.containsKey(key)) {
                        throw error("duplicate object key " + key);
                    }
                    result.put(key, value());
                    whitespace();
                    if (consume('}')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private List<Object> array() {
                expect('[');
                List<Object> result = new ArrayList<>();
                whitespace();
                if (consume(']')) {
                    return result;
                }
                while (true) {
                    result.add(value());
                    whitespace();
                    if (consume(']')) {
                        return result;
                    }
                    expect(',');
                }
            }

            private String string() {
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!end()) {
                    char c = text.charAt(offset++);
                    if (c == '"') {
                        return result.toString();
                    }
                    if (c == '\\') {
                        if (end()) {
                            throw error("unterminated escape");
                        }
                        char escaped = text.charAt(offset++);
                        switch (escaped) {
                            case '"', '\\', '/' -> result.append(escaped);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> result.append(unicode());
                            default -> throw error("invalid escape");
                        }
                    } else {
                        if (c < 0x20) {
                            throw error("control character in string");
                        }
                        result.append(c);
                    }
                }
                throw error("unterminated string");
            }

            private char unicode() {
                if (offset + 4 > text.length()) {
                    throw error("short unicode escape");
                }
                int value = 0;
                for (int i = 0; i < 4; i++) {
                    int digit = Character.digit(text.charAt(offset++), 16);
                    if (digit < 0) {
                        throw error("invalid unicode escape");
                    }
                    value = (value << 4) | digit;
                }
                return (char) value;
            }

            private Object literal(String token, Object value) {
                if (!text.startsWith(token, offset)) {
                    throw error("invalid literal");
                }
                offset += token.length();
                return value;
            }

            private BigDecimal number() {
                int start = offset;
                consume('-');
                if (consume('0')) {
                    // A zero integer part is complete unless a fraction or exponent follows.
                } else {
                    digits();
                }
                if (consume('.')) {
                    digits();
                }
                if (consume('e') || consume('E')) {
                    consume('+');
                    consume('-');
                    digits();
                }
                try {
                    return new BigDecimal(text.substring(start, offset));
                } catch (NumberFormatException e) {
                    throw error("invalid number");
                }
            }

            private void digits() {
                int start = offset;
                while (!end() && Character.isDigit(text.charAt(offset))) {
                    offset++;
                }
                if (offset == start) {
                    throw error("expected digits");
                }
            }

            private void whitespace() {
                while (!end()) {
                    char c = text.charAt(offset);
                    if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
                        offset++;
                    } else {
                        break;
                    }
                }
            }

            private boolean consume(char expected) {
                if (!end() && text.charAt(offset) == expected) {
                    offset++;
                    return true;
                }
                return false;
            }

            private void expect(char expected) {
                if (!consume(expected)) {
                    throw error("expected '" + expected + "'");
                }
            }

            private boolean end() {
                return offset >= text.length();
            }

            private IllegalArgumentException error(String message) {
                return new IllegalArgumentException(message + " at character " + offset);
            }
        }
    }
}
