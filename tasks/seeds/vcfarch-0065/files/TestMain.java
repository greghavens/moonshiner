import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Deterministic verifier for the architecture returned by VcfArchitectureClient. */
public final class TestMain {
    private static final Pattern SECRET_PLACEHOLDER =
            Pattern.compile("\\$\\{[A-Z][A-Z0-9_]*}");
    private static final Pattern BROADCOM_HTTPS_URL = Pattern.compile(
            "https://(?:[A-Za-z0-9-]+\\.)*broadcom\\.com(?:[/:?#].*)?");

    private static final Path INVENTORY = Path.of("fixtures", "estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("fixtures", "compatibility-snapshot.json");
    private static final Path INSTALLER_SPEC =
            Path.of("specifications", "vcf-installer", "vcf-installer-openapi.json");

    private static final String INVENTORY_SHA256 =
            "3bb49d637f89d9cc7e4fc5176dd4f201eb3a2da13de195823bf7563b9ce06354";
    private static final String SNAPSHOT_SHA256 =
            "292616551c66d1d210d69bf12571450e8b3ca7001c4103283e211c20de9584bf";
    private static final String INSTALLER_SPEC_SHA256 =
            "a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String artifactText = VcfArchitectureClient.createArchitecture(INVENTORY, SNAPSHOT);
        Object artifactValue = Json.parse(artifactText);
        Object installerValue = Json.parse(Files.readString(INSTALLER_SPEC));

        Object sddcSpec = null;
        if (artifactValue instanceof Map<?, ?>) {
            sddcSpec = ((Map<?, ?>) artifactValue).get("sddcSpec");
        }
        Map<String, Object> installer = uncheckedObject(installerValue);
        Object sddcSchema = path(installer, "components", "schemas", "SddcSpec");
        List<String> schemaErrors = new ArrayList<>();
        new OpenApiSchemaValidator(installer).validate(sddcSchema, sddcSpec, "$", schemaErrors);
        if (!schemaErrors.isEmpty()) {
            throw new AssertionError("SddcSpec schema validation failed first: "
                    + String.join("; ", schemaErrors));
        }

        // The SddcSpec has passed the installer's own schema. Only now check protected inputs.
        requireDigest(INVENTORY, INVENTORY_SHA256);
        requireDigest(SNAPSHOT, SNAPSHOT_SHA256);
        requireDigest(INSTALLER_SPEC, INSTALLER_SPEC_SHA256);

        Map<String, Object> artifact = object(artifactValue, "artifact");
        Map<String, Object> inventory = object(Json.parse(Files.readString(INVENTORY)), "inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(SNAPSHOT)), "snapshot");

        equal(artifact.keySet(), Set.of("sddcSpec", "migrationPlan", "researchConsulted"),
                "artifact top-level fields");
        verifyGreenfield(object(sddcSpec, "sddcSpec"), inventory);
        verifyMigrationPlan(artifact.get("migrationPlan"), inventory, snapshot);
        verifyResearchConsulted(artifact.get("researchConsulted"));

        System.out.println("PASS: installer schema, architecture, migration plan, and research record");
    }

    private static void verifyGreenfield(Map<String, Object> spec, Map<String, Object> inventory) {
        Map<String, Object> req = object(inventory.get("greenfieldRequirements"),
                "greenfieldRequirements");
        Map<String, Object> md = object(req.get("managementDomain"), "managementDomain");

        equal(string(spec.get("sddcId"), "sddcId"), string(req.get("sddcId"), "required sddcId"),
                "sddcId");
        equal(string(spec.get("workflowType"), "workflowType"),
                string(req.get("workflowType"), "required workflowType"), "workflowType");
        equal(string(spec.get("version"), "version"),
                string(req.get("targetFoundationVersion"), "targetFoundationVersion"), "version");

        verifyHosts(array(spec.get("hostSpecs"), "hostSpecs"), req, md);
        verifyNameServices(spec, req);
        verifyNetworks(spec, req);
        verifyDvs(spec, req);
        verifyVcenter(spec, req, md);
        verifySddcManager(spec, req);
        verifyNsx(spec, req, md);
        verifyVsan(spec, md);
        verifyOperations(spec, req, md);
        verifySecretPlaceholders(spec, "sddcSpec");

        isFalse(bool(spec.get("skipEsxThumbprintValidation"),
                "skipEsxThumbprintValidation"), "ESXi thumbprint validation must remain enabled");
        isFalse(bool(spec.get("skipGatewayPingValidation"),
                "skipGatewayPingValidation"), "gateway ping validation must remain enabled");
    }

    private static void verifyHosts(List<Object> hostSpecs, Map<String, Object> req,
            Map<String, Object> md) {
        List<Object> catalogRows = array(req.get("hostCatalog"), "hostCatalog");
        Map<String, Map<String, Object>> catalog = new LinkedHashMap<>();
        for (Object value : catalogRows) {
            Map<String, Object> host = object(value, "host catalog entry");
            catalog.put(string(host.get("hostname"), "catalog hostname"), host);
        }

        int minimumHosts = integer(md.get("minimumHosts"), "minimumHosts");
        check(hostSpecs.size() >= minimumHosts, "management host count is below requirement");
        Set<String> selected = new LinkedHashSet<>();
        Map<String, Integer> racks = new HashMap<>();
        int cores = 0;
        int memory = 0;
        int storage = 0;
        int largestCores = 0;
        int largestMemory = 0;
        int largestStorage = 0;
        String targetSite = string(req.get("targetSite"), "targetSite");

        for (Object value : hostSpecs) {
            Map<String, Object> hostSpec = object(value, "host spec");
            String hostname = string(hostSpec.get("hostname"), "hostSpecs.hostname");
            check(selected.add(hostname), "duplicate selected host " + hostname);
            Map<String, Object> host = catalog.get(hostname);
            check(host != null, "selected host is absent from target catalog: " + hostname);
            equal(string(host.get("site"), "host site"), targetSite,
                    "selected host site for " + hostname);
            String rack = string(host.get("rack"), "host rack");
            racks.put(rack, racks.getOrDefault(rack, 0) + 1);
            int hostCores = integer(host.get("physicalCores"), "physicalCores");
            int hostMemory = integer(host.get("memoryGiB"), "memoryGiB");
            int hostStorage = integer(host.get("usableVsanTiB"), "usableVsanTiB");
            cores += hostCores;
            memory += hostMemory;
            storage += hostStorage;
            largestCores = Math.max(largestCores, hostCores);
            largestMemory = Math.max(largestMemory, hostMemory);
            largestStorage = Math.max(largestStorage, hostStorage);
        }
        check(racks.size() >= integer(md.get("minimumRacks"), "minimumRacks"),
                "selected hosts do not span enough racks");
        int maxPerRack = integer(md.get("maximumHostsPerRack"), "maximumHostsPerRack");
        for (Map.Entry<String, Integer> rack : racks.entrySet()) {
            check(rack.getValue() <= maxPerRack, "too many hosts in rack " + rack.getKey());
        }

        int toleratedFailures = integer(md.get("hostFailuresToTolerate"),
                "hostFailuresToTolerate");
        equal(toleratedFailures, 1, "this verifier's N+1 fixture");
        Map<String, Object> demand = object(md.get("steadyStateDemand"), "steadyStateDemand");
        check(cores - largestCores >= integer(demand.get("physicalCores"), "demand cores"),
                "physical core capacity does not survive one host failure");
        check(memory - largestMemory >= integer(demand.get("memoryGiB"), "demand memory"),
                "memory capacity does not survive one host failure");
        check(storage - largestStorage >= integer(demand.get("usableVsanTiB"), "demand storage"),
                "vSAN capacity does not survive one host failure");
    }

    private static void verifyNameServices(Map<String, Object> spec, Map<String, Object> req) {
        Map<String, Object> dns = object(spec.get("dnsSpec"), "dnsSpec");
        equal(string(dns.get("subdomain"), "dnsSpec.subdomain"),
                string(req.get("dnsSubdomain"), "dnsSubdomain"), "DNS subdomain");
        equalStringSet(array(dns.get("nameservers"), "dnsSpec.nameservers"),
                array(req.get("dnsServers"), "dnsServers"), "DNS servers");
        equalStringSet(array(spec.get("ntpServers"), "ntpServers"),
                array(req.get("ntpServers"), "required NTP servers"), "NTP servers");
    }

    private static void verifyNetworks(Map<String, Object> spec, Map<String, Object> req) {
        List<Object> actualRows = array(spec.get("networkSpecs"), "networkSpecs");
        List<Object> requiredRows = array(req.get("networks"), "required networks");
        equal(actualRows.size(), requiredRows.size(), "network count");
        Map<String, Map<String, Object>> actual = indexBy(actualRows, "networkType", "networkSpecs");
        for (Object value : requiredRows) {
            Map<String, Object> required = object(value, "required network");
            String type = string(required.get("networkType"), "required networkType");
            Map<String, Object> network = actual.get(type);
            check(network != null, "missing network " + type);
            equal(integer(network.get("vlanId"), type + " vlanId"),
                    integer(required.get("vlanId"), type + " required vlanId"), type + " vlanId");
            equal(integer(network.get("mtu"), type + " mtu"),
                    integer(required.get("mtu"), type + " required mtu"), type + " mtu");
            equal(string(network.get("subnet"), type + " subnet"),
                    string(required.get("subnet"), type + " required subnet"), type + " subnet");
            equal(string(network.get("gateway"), type + " gateway"),
                    string(required.get("gateway"), type + " required gateway"), type + " gateway");
            equal(string(network.get("subnetMask"), type + " subnetMask"),
                    string(required.get("subnetMask"), type + " required subnetMask"),
                    type + " subnetMask");
            List<Object> ranges = array(network.get("includeIpAddressRanges"),
                    type + " includeIpAddressRanges");
            equal(ranges.size(), 1, type + " IP range count");
            Map<String, Object> range = object(ranges.get(0), type + " IP range");
            equal(string(range.get("startIpAddress"), type + " startIpAddress"),
                    string(required.get("startIpAddress"), type + " required start"),
                    type + " range start");
            equal(string(range.get("endIpAddress"), type + " endIpAddress"),
                    string(required.get("endIpAddress"), type + " required end"),
                    type + " range end");
        }
    }

    private static void verifyDvs(Map<String, Object> spec, Map<String, Object> req) {
        Map<String, Object> required = object(req.get("distributedSwitch"), "distributedSwitch");
        List<Object> switches = array(spec.get("dvsSpecs"), "dvsSpecs");
        equal(switches.size(), 1, "distributed switch count");
        Map<String, Object> dvs = object(switches.get(0), "dvsSpecs[0]");
        equal(string(dvs.get("dvsName"), "dvsName"), string(required.get("name"), "DVS name"),
                "DVS name");
        equal(integer(dvs.get("mtu"), "DVS mtu"), integer(required.get("mtu"), "required DVS mtu"),
                "DVS mtu");
        List<Object> requiredNetworks = array(req.get("networks"), "required networks");
        Set<String> networkTypes = new LinkedHashSet<>();
        for (Object value : requiredNetworks) {
            networkTypes.add(string(object(value, "required network").get("networkType"),
                    "required networkType"));
        }
        List<Object> dvsNetworks = array(dvs.get("networks"), "DVS networks");
        equal(toStringSet(dvsNetworks), networkTypes, "DVS network assignments");
        equal(dvsNetworks.size(), networkTypes.size(), "DVS network assignment count");

        Map<String, Object> requiredUplinks = object(required.get("vmnicUplinks"), "vmnicUplinks");
        Map<String, String> actualUplinks = new LinkedHashMap<>();
        List<Object> uplinkRows = array(dvs.get("vmnicsToUplinks"), "vmnicsToUplinks");
        equal(uplinkRows.size(), requiredUplinks.size(), "vmnic/uplink mapping count");
        for (Object value : uplinkRows) {
            Map<String, Object> mapping = object(value, "vmnic mapping");
            String id = string(mapping.get("id"), "vmnic id");
            check(actualUplinks.put(id, string(mapping.get("uplink"), "uplink")) == null,
                    "duplicate vmnic/uplink mapping for " + id);
        }
        Map<String, String> expectedUplinks = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : requiredUplinks.entrySet()) {
            expectedUplinks.put(entry.getKey(), string(entry.getValue(), "required uplink"));
        }
        equal(actualUplinks, expectedUplinks, "vmnic/uplink mapping");
    }

    private static void verifyVcenter(Map<String, Object> spec, Map<String, Object> req,
            Map<String, Object> md) {
        Map<String, Object> appliances = object(req.get("appliances"), "appliances");
        Map<String, Object> vc = object(spec.get("vcenterSpec"), "vcenterSpec");
        equal(string(vc.get("vcenterHostname"), "vcenterHostname"),
                string(appliances.get("vcenterFqdn"), "required vCenter FQDN"), "vCenter FQDN");
        equal(string(vc.get("vmSize"), "vCenter vmSize"),
                string(md.get("vcenterVmSize"), "required vCenter size"), "vCenter size");
        equal(string(vc.get("storageSize"), "vCenter storageSize"),
                string(md.get("vcenterStorageSize"), "required vCenter storage size"),
                "vCenter storage size");
        equal(string(vc.get("version"), "vCenter version"),
                string(req.get("targetFoundationVersion"), "targetFoundationVersion"),
                "vCenter version");
        isFalse(bool(vc.get("useExistingDeployment"), "vCenter useExistingDeployment"),
                "vCenter must be greenfield");
        secret(vc.get("rootVcenterPassword"), "vCenter root password");
        secret(vc.get("adminUserSsoPassword"), "vCenter SSO password");
    }

    private static void verifySddcManager(Map<String, Object> spec, Map<String, Object> req) {
        Map<String, Object> appliances = object(req.get("appliances"), "appliances");
        Map<String, Object> manager = object(spec.get("sddcManagerSpec"), "sddcManagerSpec");
        equal(string(manager.get("hostname"), "SDDC Manager hostname"),
                string(appliances.get("sddcManagerFqdn"), "required SDDC Manager FQDN"),
                "SDDC Manager FQDN");
        equal(string(manager.get("version"), "SDDC Manager version"),
                string(req.get("targetFoundationVersion"), "targetFoundationVersion"),
                "SDDC Manager version");
        isFalse(bool(manager.get("useExistingDeployment"), "SDDC Manager useExistingDeployment"),
                "SDDC Manager must be greenfield");
        secret(manager.get("rootPassword"), "SDDC Manager root password");
        secret(manager.get("sshPassword"), "SDDC Manager SSH password");
        secret(manager.get("localUserPassword"), "SDDC Manager local-user password");
    }

    private static void verifyNsx(Map<String, Object> spec, Map<String, Object> req,
            Map<String, Object> md) {
        Map<String, Object> required = object(req.get("nsx"), "required NSX");
        Map<String, Object> nsx = object(spec.get("nsxtSpec"), "nsxtSpec");
        List<Object> managers = array(nsx.get("nsxtManagers"), "nsxtManagers");
        equal(managers.size(), integer(md.get("nsxManagerCount"), "required NSX manager count"),
                "NSX manager count");
        Set<String> managerNames = new LinkedHashSet<>();
        for (Object value : managers) {
            managerNames.add(string(object(value, "NSX manager").get("hostname"),
                    "NSX manager hostname"));
        }
        equal(managerNames, toStringSet(array(required.get("managerFqdns"),
                "required NSX manager FQDNs")), "NSX manager FQDNs");
        equal(string(nsx.get("vipFqdn"), "NSX vipFqdn"),
                string(required.get("vipFqdn"), "required NSX vipFqdn"), "NSX VIP");
        equal(integer(nsx.get("transportVlanId"), "transportVlanId"),
                integer(required.get("transportVlanId"), "required transport VLAN"),
                "NSX transport VLAN");
        equal(string(nsx.get("version"), "NSX version"),
                string(req.get("targetFoundationVersion"), "targetFoundationVersion"),
                "NSX version");
        isFalse(bool(nsx.get("useExistingDeployment"), "NSX useExistingDeployment"),
                "NSX must be greenfield");
        secret(nsx.get("rootNsxtManagerPassword"), "NSX root password");
        secret(nsx.get("nsxtAdminPassword"), "NSX admin password");
        secret(nsx.get("nsxtAuditPassword"), "NSX audit password");
        Map<String, Object> pool = object(nsx.get("ipAddressPoolSpec"), "NSX TEP pool");
        equal(string(pool.get("name"), "TEP pool name"),
                string(required.get("tepPoolName"), "required TEP pool name"), "TEP pool name");
        List<Object> subnets = array(pool.get("subnets"), "TEP pool subnets");
        equal(subnets.size(), 1, "TEP subnet count");
        Map<String, Object> subnet = object(subnets.get(0), "TEP subnet");
        equal(string(subnet.get("cidr"), "TEP cidr"),
                string(required.get("tepCidr"), "required TEP cidr"), "TEP CIDR");
        equal(string(subnet.get("gateway"), "TEP gateway"),
                string(required.get("tepGateway"), "required TEP gateway"), "TEP gateway");
        List<Object> ranges = array(subnet.get("ipAddressPoolRanges"), "TEP ranges");
        equal(ranges.size(), 1, "TEP range count");
        Map<String, Object> range = object(ranges.get(0), "TEP range");
        equal(string(range.get("start"), "TEP start"),
                string(required.get("tepStart"), "required TEP start"), "TEP range start");
        equal(string(range.get("end"), "TEP end"),
                string(required.get("tepEnd"), "required TEP end"), "TEP range end");
    }

    private static void verifyVsan(Map<String, Object> spec, Map<String, Object> md) {
        Map<String, Object> datastore = object(spec.get("datastoreSpec"), "datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "vsanSpec");
        Map<String, Object> esa = object(vsan.get("esaConfig"), "vsanSpec.esaConfig");
        check(bool(esa.get("enabled"), "ESA enabled"), "vSAN ESA must be enabled");
        equal(integer(vsan.get("failuresToTolerate"), "vSAN failuresToTolerate"),
                integer(md.get("vsanFailuresToTolerate"), "required vSAN FTT"), "vSAN FTT");
    }

    private static void verifyOperations(Map<String, Object> spec, Map<String, Object> req,
            Map<String, Object> md) {
        Map<String, Object> appliances = object(req.get("appliances"), "appliances");
        Map<String, Object> fleet = object(spec.get("vcfOperationsFleetManagementSpec"),
                "vcfOperationsFleetManagementSpec");
        equal(string(fleet.get("hostname"), "fleet hostname"),
                string(appliances.get("vcfOperationsFleetFqdn"), "required fleet FQDN"),
                "VCF Operations Fleet FQDN");
        equal(string(fleet.get("version"), "fleet version"),
                string(req.get("targetFoundationVersion"), "targetFoundationVersion"),
                "VCF Operations Fleet version");
        isFalse(bool(fleet.get("useExistingDeployment"), "fleet useExistingDeployment"),
                "VCF Operations Fleet must be greenfield");
        secret(fleet.get("rootUserPassword"), "VCF Operations Fleet root password");
        secret(fleet.get("adminUserPassword"), "VCF Operations Fleet admin password");

        Map<String, Object> operations = object(spec.get("vcfOperationsSpec"), "vcfOperationsSpec");
        equal(string(operations.get("loadBalancerFqdn"), "operations loadBalancerFqdn"),
                string(appliances.get("vcfOperationsLoadBalancerFqdn"), "required operations LB"),
                "VCF Operations load balancer FQDN");
        equal(string(operations.get("version"), "operations version"),
                string(req.get("targetFoundationVersion"), "targetFoundationVersion"),
                "VCF Operations version");
        check(Set.of("medium", "large", "xlarge").contains(
                string(operations.get("applianceSize"), "operations applianceSize")),
                "highly available VCF Operations requires medium, large, or xlarge nodes");
        isFalse(bool(operations.get("useExistingDeployment"), "operations useExistingDeployment"),
                "VCF Operations must be greenfield");
        secret(operations.get("adminUserPassword"), "VCF Operations admin password");
        List<Object> nodes = array(operations.get("nodes"), "VCF Operations nodes");
        equal(nodes.size(), integer(md.get("vcfOperationsNodeCount"),
                "required VCF Operations node count"), "VCF Operations node count");
        Set<String> actual = new LinkedHashSet<>();
        Set<String> nodeTypes = new LinkedHashSet<>();
        for (Object value : nodes) {
            Map<String, Object> node = object(value, "VCF Operations node");
            actual.add(string(node.get("hostname"), "VCF Operations node hostname"));
            nodeTypes.add(string(node.get("type"), "VCF Operations node type"));
            secret(node.get("rootUserPassword"), "VCF Operations node root password");
        }
        equal(actual, toStringSet(array(appliances.get("vcfOperationsNodeFqdns"),
                "required VCF Operations nodes")), "VCF Operations node FQDNs");
        equal(nodeTypes, Set.of("master", "replica", "data"),
                "VCF Operations high-availability node roles");
    }

    private static void verifyMigrationPlan(Object planValue, Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        List<Object> plan = array(planValue, "migrationPlan");
        Map<String, Object> existing = object(inventory.get("existingEstate"), "existingEstate");
        List<Object> components = array(existing.get("components"), "existingEstate.components");
        List<Object> facts = array(snapshot.get("compatibilityFacts"), "compatibilityFacts");
        equal(plan.size(), components.size(), "migration plan item count");
        equal(facts.size(), components.size(), "compatibility fact count");

        Map<String, Map<String, Object>> inventoryById = indexBy(components, "componentId",
                "inventory components");
        Map<String, Map<String, Object>> factById = indexBy(facts, "componentId",
                "compatibility facts");
        Set<String> exactKeys = Set.of("sequence", "componentId", "component", "sourceVersion",
                "targetProduct", "targetVersion", "disposition", "gates");
        Set<String> seen = new HashSet<>();
        Map<String, Integer> sequenceById = new HashMap<>();

        for (int planIndex = 0; planIndex < plan.size(); planIndex++) {
            Object value = plan.get(planIndex);
            Map<String, Object> row = object(value, "migration plan item");
            equal(row.keySet(), exactKeys, "migration plan item fields");
            int sequence = integer(row.get("sequence"), "migration sequence");
            equal(sequence, planIndex + 1, "migration array position/sequence");
            check(sequence >= 1 && sequence <= plan.size(), "migration sequence out of range");
            String id = string(row.get("componentId"), "migration componentId");
            check(seen.add(id), "duplicate migration component " + id);
            check(!sequenceById.containsValue(sequence), "duplicate migration sequence " + sequence);
            sequenceById.put(id, sequence);
            Map<String, Object> source = inventoryById.get(id);
            Map<String, Object> fact = factById.get(id);
            check(source != null, "migration component absent from estate: " + id);
            check(fact != null, "migration component absent from compatibility snapshot: " + id);
            equal(string(row.get("component"), id + " component"),
                    string(source.get("component"), id + " inventory component"), id + " component");
            equal(string(row.get("sourceVersion"), id + " sourceVersion"),
                    string(source.get("version"), id + " inventory version"), id + " sourceVersion");
            equal(string(row.get("targetProduct"), id + " targetProduct"),
                    string(fact.get("targetProduct"), id + " snapshot targetProduct"),
                    id + " targetProduct");
            equal(string(row.get("targetVersion"), id + " targetVersion"),
                    string(fact.get("targetVersion"), id + " snapshot targetVersion"),
                    id + " targetVersion");
            equal(string(row.get("disposition"), id + " disposition"),
                    string(fact.get("disposition"), id + " snapshot disposition"),
                    id + " disposition");
            equal(toStringSet(array(row.get("gates"), id + " gates")),
                    toStringSet(array(fact.get("requiredGates"), id + " requiredGates")),
                    id + " gates");
            equal(array(row.get("gates"), id + " gates").size(),
                    array(fact.get("requiredGates"), id + " requiredGates").size(),
                    id + " gate count");
        }
        equal(seen, inventoryById.keySet(), "migration component coverage");
        for (int sequence = 1; sequence <= plan.size(); sequence++) {
            check(sequenceById.containsValue(sequence), "missing migration sequence " + sequence);
        }
        for (Map.Entry<String, Map<String, Object>> entry : factById.entrySet()) {
            int componentSequence = sequenceById.get(entry.getKey());
            for (Object requirement : array(entry.getValue().get("requires"),
                    entry.getKey() + " ordering requirements")) {
                String predecessor = string(requirement, "ordering predecessor");
                Integer predecessorSequence = sequenceById.get(predecessor);
                check(predecessorSequence != null && predecessorSequence < componentSequence,
                        predecessor + " must precede " + entry.getKey());
            }
        }
    }

    private static void verifyResearchConsulted(Object researchValue) {
        List<Object> research = array(researchValue, "researchConsulted");
        check(!research.isEmpty(), "researchConsulted must not be empty");
        Set<String> requiredKeys = Set.of("title", "url", "checkedOn", "finding");
        for (Object value : research) {
            Map<String, Object> row = object(value, "researchConsulted entry");
            check(row.keySet().containsAll(requiredKeys),
                    "researchConsulted entry is missing a required field");
            nonemptyString(row.get("title"), "research title");
            String url = nonemptyString(row.get("url"), "research URL");
            check(BROADCOM_HTTPS_URL.matcher(url).matches(),
                    "research URL must be an absolute Broadcom HTTPS URL");
            String checkedOn = nonemptyString(row.get("checkedOn"), "research checkedOn");
            try {
                LocalDate.parse(checkedOn);
            } catch (DateTimeParseException exception) {
                throw new AssertionError("research checkedOn must be a valid ISO YYYY-MM-DD date");
            }
            nonemptyString(row.get("finding"), "research finding");
        }
    }

    private static void verifySecretPlaceholders(Object value, String path) {
        if (value instanceof Map<?, ?>) {
            Map<String, Object> object = uncheckedObject(value);
            for (Map.Entry<String, Object> entry : object.entrySet()) {
                String childPath = path + "." + entry.getKey();
                if (entry.getKey().toLowerCase(Locale.ROOT).contains("password")) {
                    secret(entry.getValue(), childPath);
                } else {
                    verifySecretPlaceholders(entry.getValue(), childPath);
                }
            }
        } else if (value instanceof List<?>) {
            List<?> array = (List<?>) value;
            for (int i = 0; i < array.size(); i++) {
                verifySecretPlaceholders(array.get(i), path + "[" + i + "]");
            }
        }
    }

    private static Map<String, Map<String, Object>> indexBy(List<Object> rows, String key,
            String label) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : rows) {
            Map<String, Object> row = object(value, label + " entry");
            String id = string(row.get(key), label + "." + key);
            check(result.put(id, row) == null, "duplicate " + label + " key " + id);
        }
        return result;
    }

    private static Object path(Map<String, Object> root, String... parts) {
        Object current = root;
        for (String part : parts) {
            if (!(current instanceof Map<?, ?>)) {
                return null;
            }
            current = ((Map<?, ?>) current).get(part);
        }
        return current;
    }

    private static void requireDigest(Path path, String expected) throws Exception {
        String actual = sha256(Files.readAllBytes(path));
        equal(actual, expected, "protected fixture digest for " + path);
    }

    private static String sha256(byte[] bytes) throws NoSuchAlgorithmException {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder value = new StringBuilder();
        for (byte b : digest) {
            value.append(String.format("%02x", b & 0xff));
        }
        return value.toString();
    }

    private static Set<String> toStringSet(List<Object> values) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            result.add(string(value, "string array item"));
        }
        return result;
    }

    private static void equalStringSet(List<Object> actual, List<Object> expected, String label) {
        equal(toStringSet(actual), toStringSet(expected), label);
        equal(actual.size(), expected.size(), label + " count");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> uncheckedObject(Object value) {
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        check(value instanceof Map<?, ?>, label + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        check(value instanceof List<?>, label + " must be an array");
        return (List<Object>) value;
    }

    private static String string(Object value, String label) {
        check(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static String nonemptyString(Object value, String label) {
        String result = string(value, label);
        check(!result.trim().isEmpty(), label + " must not be empty");
        return result;
    }

    private static void secret(Object value, String label) {
        String placeholder = string(value, label);
        check(SECRET_PLACEHOLDER.matcher(placeholder).matches(),
                label + " must be a literal ${UPPER_SNAKE_CASE} placeholder");
    }

    private static int integer(Object value, String label) {
        check(value instanceof BigDecimal, label + " must be an integer");
        try {
            return ((BigDecimal) value).intValueExact();
        } catch (ArithmeticException exception) {
            throw new AssertionError(label + " must be an exact 32-bit integer");
        }
    }

    private static boolean bool(Object value, String label) {
        check(value instanceof Boolean, label + " must be a boolean");
        return (Boolean) value;
    }

    private static void isFalse(boolean value, String message) {
        check(!value, message);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object actual, Object expected, String label) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(label + " mismatch: expected " + expected + ", got " + actual);
        }
    }

    /** Validates the selected value by recursively interpreting the vendored OpenAPI schema. */
    private static final class OpenApiSchemaValidator {
        private final Map<String, Object> document;

        OpenApiSchemaValidator(Map<String, Object> document) {
            this.document = document;
        }

        void validate(Object schemaValue, Object value, String path, List<String> errors) {
            if (!(schemaValue instanceof Map<?, ?>)) {
                errors.add(path + ": schema is missing or malformed");
                return;
            }
            Map<String, Object> schema = uncheckedObject(schemaValue);
            Object refValue = schema.get("$ref");
            if (refValue instanceof String) {
                Object resolved = resolve((String) refValue);
                if (resolved == null) {
                    errors.add(path + ": unresolved schema reference " + refValue);
                    return;
                }
                validate(resolved, value, path, errors);
                return;
            }
            if (value == null && Boolean.TRUE.equals(schema.get("nullable"))) {
                return;
            }

            validateCompositions(schema, value, path, errors);
            String type = schema.get("type") instanceof String ? (String) schema.get("type") : null;
            if (type == null) {
                validateEnum(schema, value, path, errors);
                return;
            }
            switch (type) {
                case "object":
                    validateObject(schema, value, path, errors);
                    break;
                case "array":
                    validateArray(schema, value, path, errors);
                    break;
                case "string":
                    validateString(schema, value, path, errors);
                    break;
                case "integer":
                    validateNumber(schema, value, path, errors, true);
                    break;
                case "number":
                    validateNumber(schema, value, path, errors, false);
                    break;
                case "boolean":
                    if (!(value instanceof Boolean)) {
                        errors.add(path + ": expected boolean");
                    }
                    break;
                default:
                    errors.add(path + ": unsupported schema type " + type);
            }
            validateEnum(schema, value, path, errors);
        }

        private void validateCompositions(Map<String, Object> schema, Object value, String path,
                List<String> errors) {
            Object allOf = schema.get("allOf");
            if (allOf instanceof List<?>) {
                for (Object branch : (List<?>) allOf) {
                    validate(branch, value, path, errors);
                }
            }
            Object anyOf = schema.get("anyOf");
            if (anyOf instanceof List<?> && !matchesAny((List<?>) anyOf, value, path)) {
                errors.add(path + ": does not match anyOf");
            }
            Object oneOf = schema.get("oneOf");
            if (oneOf instanceof List<?>) {
                int matches = 0;
                for (Object branch : (List<?>) oneOf) {
                    List<String> branchErrors = new ArrayList<>();
                    validate(branch, value, path, branchErrors);
                    if (branchErrors.isEmpty()) {
                        matches++;
                    }
                }
                if (matches != 1) {
                    errors.add(path + ": expected exactly one matching oneOf branch, got " + matches);
                }
            }
        }

        private boolean matchesAny(List<?> schemas, Object value, String path) {
            for (Object branch : schemas) {
                List<String> branchErrors = new ArrayList<>();
                validate(branch, value, path, branchErrors);
                if (branchErrors.isEmpty()) {
                    return true;
                }
            }
            return false;
        }

        private void validateObject(Map<String, Object> schema, Object value, String path,
                List<String> errors) {
            if (!(value instanceof Map<?, ?>)) {
                errors.add(path + ": expected object");
                return;
            }
            Map<String, Object> object = uncheckedObject(value);
            Object requiredValue = schema.get("required");
            if (requiredValue instanceof List<?>) {
                for (Object required : (List<?>) requiredValue) {
                    if (required instanceof String && !object.containsKey(required)) {
                        errors.add(path + ": missing required property " + required);
                    }
                }
            }
            Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?>
                    ? uncheckedObject(schema.get("properties")) : Collections.emptyMap();
            for (Map.Entry<String, Object> entry : object.entrySet()) {
                Object propertySchema = properties.get(entry.getKey());
                if (propertySchema != null) {
                    validate(propertySchema, entry.getValue(), path + "." + entry.getKey(), errors);
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    errors.add(path + ": additional property is not allowed: " + entry.getKey());
                } else if (schema.get("additionalProperties") instanceof Map<?, ?>) {
                    validate(schema.get("additionalProperties"), entry.getValue(),
                            path + "." + entry.getKey(), errors);
                }
            }
        }

        private void validateArray(Map<String, Object> schema, Object value, String path,
                List<String> errors) {
            if (!(value instanceof List<?>)) {
                errors.add(path + ": expected array");
                return;
            }
            List<?> array = (List<?>) value;
            checkLength(schema, "minItems", array.size(), path, "items", true, errors);
            checkLength(schema, "maxItems", array.size(), path, "items", false, errors);
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                Set<Object> unique = new HashSet<>(array);
                if (unique.size() != array.size()) {
                    errors.add(path + ": array items must be unique");
                }
            }
            Object itemSchema = schema.get("items");
            if (itemSchema != null) {
                for (int i = 0; i < array.size(); i++) {
                    validate(itemSchema, array.get(i), path + "[" + i + "]", errors);
                }
            }
        }

        private void validateString(Map<String, Object> schema, Object value, String path,
                List<String> errors) {
            if (!(value instanceof String)) {
                errors.add(path + ": expected string");
                return;
            }
            String string = (String) value;
            int length = string.codePointCount(0, string.length());
            checkLength(schema, "minLength", length, path, "characters", true, errors);
            checkLength(schema, "maxLength", length, path, "characters", false, errors);
            Object pattern = schema.get("pattern");
            if (pattern instanceof String && !Pattern.compile((String) pattern).matcher(string).find()) {
                errors.add(path + ": string does not match pattern " + pattern);
            }
        }

        private void validateNumber(Map<String, Object> schema, Object value, String path,
                List<String> errors, boolean integer) {
            if (!(value instanceof BigDecimal)) {
                errors.add(path + ": expected " + (integer ? "integer" : "number"));
                return;
            }
            BigDecimal number = (BigDecimal) value;
            if (integer && number.stripTrailingZeros().scale() > 0) {
                errors.add(path + ": expected integer");
                return;
            }
            compareBound(schema, "minimum", number, path, true, errors);
            compareBound(schema, "maximum", number, path, false, errors);
        }

        private void validateEnum(Map<String, Object> schema, Object value, String path,
                List<String> errors) {
            Object enumValue = schema.get("enum");
            if (enumValue instanceof List<?> && !((List<?>) enumValue).contains(value)) {
                errors.add(path + ": value is not in enum " + enumValue);
            }
        }

        private void checkLength(Map<String, Object> schema, String keyword, int actual,
                String path, String unit, boolean minimum, List<String> errors) {
            Object boundValue = schema.get(keyword);
            if (!(boundValue instanceof BigDecimal)) {
                return;
            }
            int bound = ((BigDecimal) boundValue).intValue();
            if ((minimum && actual < bound) || (!minimum && actual > bound)) {
                errors.add(path + ": expected " + (minimum ? "at least " : "at most ")
                        + bound + " " + unit + ", got " + actual);
            }
        }

        private void compareBound(Map<String, Object> schema, String keyword, BigDecimal actual,
                String path, boolean minimum, List<String> errors) {
            Object boundValue = schema.get(keyword);
            if (!(boundValue instanceof BigDecimal)) {
                return;
            }
            BigDecimal bound = (BigDecimal) boundValue;
            int comparison = actual.compareTo(bound);
            if ((minimum && comparison < 0) || (!minimum && comparison > 0)) {
                errors.add(path + ": violates " + keyword + " " + bound);
            }
        }

        private Object resolve(String ref) {
            if (!ref.startsWith("#/")) {
                return null;
            }
            Object current = document;
            String[] tokens = ref.substring(2).split("/");
            for (String encoded : tokens) {
                if (!(current instanceof Map<?, ?>)) {
                    return null;
                }
                String token = encoded.replace("~1", "/").replace("~0", "~");
                current = ((Map<?, ?>) current).get(token);
            }
            return current;
        }
    }

    /** Minimal strict JSON parser used so the verifier has no external dependencies. */
    private static final class Json {
        private final String text;
        private int index;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            if (text == null) {
                throw new AssertionError("client returned null rather than JSON");
            }
            Json parser = new Json(text);
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (parser.index != text.length()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        private Object readValue() {
            skipWhitespace();
            if (index >= text.length()) {
                throw error("expected value");
            }
            char c = text.charAt(index);
            if (c == '{') {
                return readObject();
            }
            if (c == '[') {
                return readArray();
            }
            if (c == '"') {
                return readString();
            }
            if (c == 't') {
                readLiteral("true");
                return Boolean.TRUE;
            }
            if (c == 'f') {
                readLiteral("false");
                return Boolean.FALSE;
            }
            if (c == 'n') {
                readLiteral("null");
                return null;
            }
            if (c == '-' || Character.isDigit(c)) {
                return readNumber();
            }
            throw error("unexpected character '" + c + "'");
        }

        private Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                if (index >= text.length() || text.charAt(index) != '"') {
                    throw error("expected object key");
                }
                String key = readString();
                check(!result.containsKey(key), "duplicate JSON object key: " + key);
                skipWhitespace();
                expect(':');
                result.put(key, readValue());
                skipWhitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> readArray() {
            expect('[');
            List<Object> result = new ArrayList<>();
            skipWhitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(readValue());
                skipWhitespace();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String readString() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < text.length()) {
                char c = text.charAt(index++);
                if (c == '"') {
                    return result.toString();
                }
                if (c == '\\') {
                    if (index >= text.length()) {
                        throw error("unterminated escape");
                    }
                    char escape = text.charAt(index++);
                    switch (escape) {
                        case '"': result.append('"'); break;
                        case '\\': result.append('\\'); break;
                        case '/': result.append('/'); break;
                        case 'b': result.append('\b'); break;
                        case 'f': result.append('\f'); break;
                        case 'n': result.append('\n'); break;
                        case 'r': result.append('\r'); break;
                        case 't': result.append('\t'); break;
                        case 'u': result.append(readUnicodeEscape()); break;
                        default: throw error("invalid escape \\" + escape);
                    }
                } else {
                    if (c < 0x20) {
                        throw error("unescaped control character");
                    }
                    result.append(c);
                }
            }
            throw error("unterminated string");
        }

        private char readUnicodeEscape() {
            if (index + 4 > text.length()) {
                throw error("short unicode escape");
            }
            String hex = text.substring(index, index + 4);
            index += 4;
            try {
                return (char) Integer.parseInt(hex, 16);
            } catch (NumberFormatException exception) {
                throw error("invalid unicode escape");
            }
        }

        private BigDecimal readNumber() {
            int start = index;
            if (take('-') && index >= text.length()) {
                throw error("incomplete number");
            }
            if (take('0')) {
                if (index < text.length() && Character.isDigit(text.charAt(index))) {
                    throw error("leading zero in number");
                }
            } else {
                readDigits();
            }
            if (take('.')) {
                readDigits();
            }
            if (index < text.length() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                index++;
                if (index < text.length() && (text.charAt(index) == '+' || text.charAt(index) == '-')) {
                    index++;
                }
                readDigits();
            }
            try {
                return new BigDecimal(text.substring(start, index));
            } catch (NumberFormatException exception) {
                throw error("invalid number");
            }
        }

        private void readDigits() {
            int start = index;
            while (index < text.length() && Character.isDigit(text.charAt(index))) {
                index++;
            }
            if (start == index) {
                throw error("expected digit");
            }
        }

        private void readLiteral(String literal) {
            if (!text.startsWith(literal, index)) {
                throw error("expected " + literal);
            }
            index += literal.length();
        }

        private void skipWhitespace() {
            while (index < text.length()) {
                char c = text.charAt(index);
                if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
                    index++;
                } else {
                    break;
                }
            }
        }

        private boolean take(char c) {
            if (index < text.length() && text.charAt(index) == c) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char c) {
            if (!take(c)) {
                throw error("expected '" + c + "'");
            }
        }

        private AssertionError error(String message) {
            return new AssertionError("invalid JSON at character " + index + ": " + message);
        }
    }
}
