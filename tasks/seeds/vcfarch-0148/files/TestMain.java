import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.DateTimeException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Offline, deterministic verifier for the architecture artifact. */
public final class TestMain {
    private static int assertions;

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Object artifactValue = Json.parse(ArchitectureClient.architecture());
        Map<String, Object> artifact = object(artifactValue, "artifact");
        Map<String, Object> greenfield = object(present(artifact, "greenfield", "artifact"), "greenfield");
        Object sddcSpec = present(greenfield, "sddcSpec", "greenfield");

        // The installer schema check is deliberately the first design check.
        Object installerOpenApi = readJson("specifications/vcf-installer/vcf-installer-openapi.json");
        SchemaValidator installerValidator = new SchemaValidator(installerOpenApi);
        installerValidator.validateReference("#/components/schemas/SddcSpec", sddcSpec, "greenfield.sddcSpec");

        Map<String, Object> plan = object(present(artifact, "existingEstatePlan", "artifact"),
                "existingEstatePlan");
        Object migrationSchema = readJson("schemas/migration-plan-schema.json");
        new SchemaValidator(migrationSchema).validateRoot(plan, "existingEstatePlan");

        Map<String, Object> inventory = object(readJson("fixtures/estate-inventory.json"), "estate inventory");
        Map<String, Object> authority = object(readJson("fixtures/compatibility-snapshot.json"),
                "compatibility snapshot");

        checkEquals("1.0", string(present(artifact, "schemaVersion", "artifact"), "schemaVersion"),
                "artifact schemaVersion");
        verifyGreenfield(greenfield, object(sddcSpec, "greenfield.sddcSpec"), authority);
        verifyResearchRecord();
        verifyPlan(plan, inventory, authority);

        System.out.println("PASS: " + assertions + " deterministic architecture assertions");
    }

    private static void verifyGreenfield(Map<String, Object> greenfield, Map<String, Object> spec,
            Map<String, Object> authority) {
        Map<String, Object> expected = object(present(authority, "greenfield", "authority"),
                "authority.greenfield");
        checkEquals(string(present(expected, "sddcSpecVersion", "authority.greenfield"), "version"),
                string(present(spec, "version", "sddcSpec"), "sddcSpec.version"), "greenfield version");
        checkEquals(string(present(expected, "workflowType", "authority.greenfield"), "workflowType"),
                string(present(spec, "workflowType", "sddcSpec"), "sddcSpec.workflowType"),
                "greenfield workflow type");

        Map<String, Object> topology = object(present(greenfield, "topology", "greenfield"),
                "greenfield.topology");
        check(bool(present(topology, "stretchedManagementDomain", "greenfield.topology"),
                "stretchedManagementDomain"), "management domain must be stretched");

        Map<String, Map<String, Object>> topologySites = indexById(
                array(present(topology, "dataSites", "greenfield.topology"), "greenfield.topology.dataSites"),
                "greenfield.topology.dataSites");
        List<Object> expectedSites = array(present(expected, "dataSites", "authority.greenfield"),
                "authority.greenfield.dataSites");
        check(topologySites.size() == expectedSites.size(), "greenfield must contain exactly the two data sites");

        Set<String> topologyHosts = new LinkedHashSet<>();
        Set<String> dataSiteIds = new LinkedHashSet<>();
        Set<String> dataFailureDomains = new LinkedHashSet<>();
        for (Object expectedSiteValue : expectedSites) {
            Map<String, Object> expectedSite = object(expectedSiteValue, "expected data site");
            String siteId = string(present(expectedSite, "id", "expected data site"), "site id");
            Map<String, Object> actualSite = topologySites.get(siteId);
            check(actualSite != null, "missing greenfield data site " + siteId);
            dataSiteIds.add(siteId);
            String expectedFaultDomain = string(present(expectedSite, "failureDomain", "expected data site"),
                    "failureDomain");
            checkEquals(expectedFaultDomain,
                    string(present(actualSite, "failureDomain", "topology site"), "failureDomain"),
                    siteId + " failure domain");
            dataFailureDomains.add(expectedFaultDomain);
            List<Object> hosts = array(present(actualSite, "hosts", "topology site"), "topology site hosts");
            int hostCount = integer(present(expectedSite, "hostCount", "expected data site"), "hostCount");
            check(hosts.size() == hostCount, siteId + " must have exactly " + hostCount + " hosts");
            for (Object host : hosts) {
                check(topologyHosts.add(string(host, "topology host")), "duplicate host across data sites");
            }
        }

        Set<String> specHosts = new LinkedHashSet<>();
        for (Object hostValue : array(present(spec, "hostSpecs", "sddcSpec"), "sddcSpec.hostSpecs")) {
            Map<String, Object> host = object(hostValue, "hostSpec");
            check(specHosts.add(string(present(host, "hostname", "hostSpec"), "hostSpec.hostname")),
                    "duplicate SddcSpec host");
        }
        checkEquals(topologyHosts, specHosts, "SddcSpec hosts must be exactly the stretched-site data hosts");

        Map<String, Object> expectedWitness = object(present(expected, "witness", "authority.greenfield"),
                "authority.greenfield.witness");
        Map<String, Object> witness = object(present(topology, "witness", "greenfield.topology"),
                "greenfield.topology.witness");
        String witnessSite = string(present(witness, "siteId", "witness"), "witness.siteId");
        String witnessFd = string(present(witness, "failureDomain", "witness"), "witness.failureDomain");
        checkEquals(string(present(expectedWitness, "siteId", "expected witness"), "siteId"), witnessSite,
                "witness site");
        checkEquals(string(present(expectedWitness, "failureDomain", "expected witness"), "failureDomain"),
                witnessFd, "witness failure domain");
        checkEquals(string(present(expectedWitness, "mode", "expected witness"), "mode"),
                string(present(witness, "mode", "witness"), "mode"), "witness mode");
        check(!dataSiteIds.contains(witnessSite), "witness must be outside both data sites");
        check(!dataFailureDomains.contains(witnessFd), "witness must be in an independent failure domain");
        check(!bool(present(witness, "memberOfDataCluster", "witness"), "memberOfDataCluster"),
                "witness must not be a management-cluster data host");
        check(!bool(present(witness, "runsManagementWorkloads", "witness"), "runsManagementWorkloads"),
                "witness must not run management workloads");
        String witnessFqdn = string(present(witness, "fqdn", "witness"), "witness.fqdn");
        check(!witnessFqdn.isBlank(), "witness FQDN must be named");
        check(!specHosts.contains(witnessFqdn), "witness must not appear in SddcSpec data hosts");

        Set<String> requiredNetworks = strings(array(present(expected, "requiredNetworkTypes", "authority"),
                "authority required networks"), "required network");
        Set<String> actualNetworks = new LinkedHashSet<>();
        for (Object networkValue : array(present(spec, "networkSpecs", "sddcSpec"), "networkSpecs")) {
            Map<String, Object> network = object(networkValue, "networkSpec");
            actualNetworks.add(string(present(network, "networkType", "networkSpec"), "networkType"));
        }
        check(actualNetworks.containsAll(requiredNetworks), "SddcSpec is missing required management network types");

        List<Object> dvsSpecs = array(present(spec, "dvsSpecs", "sddcSpec"), "dvsSpecs");
        check(!dvsSpecs.isEmpty(), "greenfield design requires a distributed switch");
        Set<String> dvsNetworks = new LinkedHashSet<>();
        for (Object dvsValue : dvsSpecs) {
            Map<String, Object> dvs = object(dvsValue, "dvsSpec");
            if (dvs.containsKey("networks")) {
                dvsNetworks.addAll(strings(array(dvs.get("networks"), "dvs networks"), "dvs network"));
            }
        }
        check(dvsNetworks.containsAll(requiredNetworks), "distributed switch must carry all required networks");

        Map<String, Object> vcenter = object(present(spec, "vcenterSpec", "sddcSpec"), "vcenterSpec");
        check(!bool(present(vcenter, "useExistingDeployment", "vcenterSpec"), "useExistingDeployment"),
                "greenfield vCenter cannot reuse an existing deployment");
        check(!string(present(vcenter, "vcenterHostname", "vcenterSpec"), "vcenterHostname").isBlank(),
                "greenfield vCenter must have a hostname");
        Map<String, Object> sddcManager = object(present(spec, "sddcManagerSpec", "sddcSpec"),
                "sddcManagerSpec");
        check(!bool(present(sddcManager, "useExistingDeployment", "sddcManagerSpec"), "useExistingDeployment"),
                "greenfield SDDC Manager cannot reuse an existing deployment");
        check(!string(present(sddcManager, "hostname", "sddcManagerSpec"), "hostname").isBlank(),
                "greenfield SDDC Manager must have a hostname");
        Map<String, Object> nsx = object(present(spec, "nsxtSpec", "sddcSpec"), "nsxtSpec");
        check(!bool(present(nsx, "useExistingDeployment", "nsxtSpec"), "useExistingDeployment"),
                "greenfield NSX cannot reuse an existing deployment");
        check(!string(present(nsx, "vipFqdn", "nsxtSpec"), "vipFqdn").isBlank(),
                "greenfield NSX VIP must have a hostname");
        int expectedManagerCount = integer(present(expected, "nsxManagerCount", "authority.greenfield"),
                "nsxManagerCount");
        List<Object> nsxManagers = array(present(nsx, "nsxtManagers", "nsxtSpec"), "nsxtManagers");
        check(nsxManagers.size() == expectedManagerCount,
                "NSX Manager node count must be " + expectedManagerCount);
        Set<String> nsxManagerNames = new LinkedHashSet<>();
        for (Object managerValue : nsxManagers) {
            Map<String, Object> manager = object(managerValue, "nsxtManager");
            String hostname = string(present(manager, "hostname", "nsxtManager"), "nsxtManager.hostname");
            check(!hostname.isBlank(), "every NSX Manager node must have a hostname");
            check(nsxManagerNames.add(hostname), "NSX Manager hostnames must be unique");
        }
    }

    private static void verifyResearchRecord() {
        List<Object> records = array(Json.parse(ArchitectureClient.researchRecord()), "researchRecord");
        check(!records.isEmpty(), "researchRecord must contain at least one consulted source");

        Set<String> urls = new LinkedHashSet<>();
        for (int index = 0; index < records.size(); index++) {
            String path = "researchRecord[" + index + "]";
            Map<String, Object> record = object(records.get(index), path);
            String title = string(present(record, "title", path), path + ".title");
            String url = string(present(record, "url", path), path + ".url");
            String accessedAt = string(present(record, "accessedAt", path), path + ".accessedAt");
            String factUsed = string(present(record, "factUsed", path), path + ".factUsed");

            check(!title.isBlank(), path + ".title must not be blank");
            check(urls.add(url), "researchRecord contains duplicate URL " + url);
            verifyBroadcomUrl(url, path + ".url");
            check(Pattern.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}", accessedAt),
                    path + ".accessedAt must use YYYY-MM-DD form");
            try {
                LocalDate.parse(accessedAt);
            } catch (DateTimeException exception) {
                throw new AssertionError(path + ".accessedAt must be a valid calendar date", exception);
            }
            check(!factUsed.isBlank(), path + ".factUsed must not be blank");
        }
    }

    private static void verifyBroadcomUrl(String value, String path) {
        final URI uri;
        try {
            uri = new URI(value);
        } catch (URISyntaxException exception) {
            throw new AssertionError(path + " must be a valid URL", exception);
        }
        check("https".equalsIgnoreCase(uri.getScheme()), path + " must use HTTPS");
        String host = uri.getHost();
        check(host != null && (host.equalsIgnoreCase("broadcom.com")
                || host.toLowerCase().endsWith(".broadcom.com")), path + " must name a Broadcom host");
        check(uri.getRawPath() != null && !uri.getRawPath().isBlank() && !"/".equals(uri.getRawPath()),
                path + " must identify a published source page");
    }

    private static void verifyPlan(Map<String, Object> plan, Map<String, Object> inventory,
            Map<String, Object> authority) {
        checkEquals(string(present(inventory, "estateId", "inventory"), "estateId"),
                string(present(plan, "estateId", "plan"), "estateId"), "plan estateId");

        Map<String, Object> expectedTopology = object(present(authority, "topology", "authority"),
                "authority.topology");
        Map<String, Object> actualTopology = object(present(plan, "topologyDecision", "plan"),
                "plan.topologyDecision");
        checkEquals(expectedTopology, actualTopology, "existing-estate topology decision");
        Set<String> planDataSites = strings(array(present(actualTopology, "dataSiteIds", "topology"), "dataSiteIds"),
                "data site id");
        check(!planDataSites.contains(string(present(actualTopology, "witnessSiteId", "topology"), "witnessSiteId")),
                "existing-estate witness must be outside the data sites");

        Map<String, Object> inventoryManagement = object(present(inventory, "managementDomain", "inventory"),
                "inventory.managementDomain");
        checkEquals(bool(present(inventoryManagement, "stretched", "inventory management domain"), "stretched"),
                bool(present(actualTopology, "stretchedManagementDomain", "topology"), "stretchedManagementDomain"),
                "plan must preserve the inventory's stretched-management decision");
        Set<String> inventorySiteIds = new LinkedHashSet<>();
        for (Object siteValue : array(present(inventoryManagement, "dataSites", "inventory management domain"),
                "inventory management data sites")) {
            Map<String, Object> site = object(siteValue, "inventory management data site");
            inventorySiteIds.add(string(present(site, "id", "inventory management data site"), "site id"));
        }
        checkEquals(inventorySiteIds, planDataSites, "plan data sites must match the inventory");
        Map<String, Object> inventoryWitness = object(present(inventoryManagement, "witness", "inventory management"),
                "inventory management witness");
        checkEquals(string(present(inventoryWitness, "componentId", "inventory witness"), "componentId"),
                string(present(actualTopology, "witnessComponentId", "topology"), "witnessComponentId"),
                "plan witness component");
        checkEquals(string(present(inventoryWitness, "siteId", "inventory witness"), "siteId"),
                string(present(actualTopology, "witnessSiteId", "topology"), "witnessSiteId"),
                "plan witness site");
        checkEquals(string(present(inventoryWitness, "failureDomain", "inventory witness"), "failureDomain"),
                string(present(actualTopology, "witnessFailureDomain", "topology"), "witnessFailureDomain"),
                "plan witness failure domain");
        checkEquals(string(present(inventoryWitness, "mode", "inventory witness"), "mode"),
                string(present(actualTopology, "witnessMode", "topology"), "witnessMode"), "plan witness mode");
        checkEquals(bool(present(inventoryWitness, "memberOfDataCluster", "inventory witness"),
                "memberOfDataCluster"),
                bool(present(actualTopology, "witnessMemberOfDataCluster", "topology"),
                        "witnessMemberOfDataCluster"),
                "plan witness cluster membership");
        checkEquals(bool(present(inventoryWitness, "runsManagementWorkloads", "inventory witness"),
                "runsManagementWorkloads"),
                bool(present(actualTopology, "witnessRunsManagementWorkloads", "topology"),
                        "witnessRunsManagementWorkloads"),
                "plan witness workload placement");

        Map<String, Map<String, Object>> inventoryComponents = indexById(
                array(present(inventory, "components", "inventory"), "inventory.components"), "inventory.components");
        Map<String, Map<String, Object>> planComponents = indexById(
                array(present(plan, "components", "plan"), "plan.components"), "plan.components");
        checkEquals(inventoryComponents.keySet(), planComponents.keySet(),
                "plan must name every inventory component exactly once");

        Map<String, Object> targets = object(present(authority, "targetVersions", "authority"),
                "authority.targetVersions");
        Map<String, Object> gates = object(present(authority, "componentGates", "authority"),
                "authority.componentGates");
        checkEquals(inventoryComponents.keySet(), targets.keySet(), "target authority must cover inventory");
        checkEquals(inventoryComponents.keySet(), gates.keySet(), "gate authority must cover inventory");

        for (String id : inventoryComponents.keySet()) {
            Map<String, Object> source = inventoryComponents.get(id);
            Map<String, Object> component = planComponents.get(id);
            checkEquals(string(present(source, "type", "inventory component"), "type"),
                    string(present(component, "type", "plan component"), "type"), id + " type");
            checkEquals(string(present(source, "version", "inventory component"), "version"),
                    string(present(component, "currentVersion", "plan component"), "currentVersion"),
                    id + " current version");
            checkEquals(string(present(targets, id, "targetVersions"), "target version"),
                    string(present(component, "targetVersion", "plan component"), "targetVersion"),
                    id + " target version");
            checkEquals(strings(array(present(gates, id, "componentGates"), "component gates"), "gate"),
                    strings(array(present(component, "gates", "plan component"), "plan component gates"), "gate"),
                    id + " gates");
        }

        List<Object> expectedSteps = array(present(authority, "requiredTransitions", "authority"),
                "authority.requiredTransitions");
        List<Object> actualSteps = array(present(plan, "steps", "plan"), "plan.steps");
        check(actualSteps.size() == expectedSteps.size(), "plan must contain the exact pinned transition count");

        Map<String, String> versions = new LinkedHashMap<>();
        for (Map.Entry<String, Map<String, Object>> entry : inventoryComponents.entrySet()) {
            versions.put(entry.getKey(), string(present(entry.getValue(), "version", "inventory component"), "version"));
        }
        List<Object> forbidden = array(present(authority, "forbiddenTransitions", "authority"),
                "authority.forbiddenTransitions");

        for (int i = 0; i < expectedSteps.size(); i++) {
            Map<String, Object> expected = object(expectedSteps.get(i), "required transition");
            Map<String, Object> actual = object(actualSteps.get(i), "plan step");
            int order = integer(present(actual, "order", "plan step"), "order");
            check(order == i + 1, "plan step order must be contiguous from one");
            for (String key : List.of("componentId", "fromVersion", "toVersion")) {
                checkEquals(string(present(expected, key, "required transition"), key),
                        string(present(actual, key, "plan step"), key), "step " + order + " " + key);
            }
            checkEquals(strings(array(present(expected, "requiredGates", "required transition"), "requiredGates"),
                    "required gate"),
                    strings(array(present(actual, "requiredGates", "plan step"), "requiredGates"), "required gate"),
                    "step " + order + " required gates");

            String componentId = string(present(actual, "componentId", "plan step"), "componentId");
            String from = string(present(actual, "fromVersion", "plan step"), "fromVersion");
            String to = string(present(actual, "toVersion", "plan step"), "toVersion");
            checkEquals(versions.get(componentId), from, "step " + order + " starts from simulated version");
            verifyNotForbidden(componentId, from, to, versions, forbidden, order);
            versions.put(componentId, to);
        }

        for (String id : inventoryComponents.keySet()) {
            checkEquals(string(present(targets, id, "targetVersions"), "target version"), versions.get(id),
                    id + " must finish at target");
        }
    }

    private static void verifyNotForbidden(String componentId, String from, String to,
            Map<String, String> versions, List<Object> forbidden, int order) {
        for (Object ruleValue : forbidden) {
            Map<String, Object> rule = object(ruleValue, "forbidden transition");
            if (!componentId.equals(string(present(rule, "componentId", "forbidden transition"), "componentId"))
                    || !from.equals(string(present(rule, "fromVersion", "forbidden transition"), "fromVersion"))
                    || !to.equals(string(present(rule, "toVersion", "forbidden transition"), "toVersion"))) {
                continue;
            }
            boolean exceptionSatisfied = false;
            if (rule.containsKey("unlessComponent") && rule.containsKey("unlessVersion")) {
                String unlessComponent = string(rule.get("unlessComponent"), "unlessComponent");
                String unlessVersion = string(rule.get("unlessVersion"), "unlessVersion");
                exceptionSatisfied = unlessVersion.equals(versions.get(unlessComponent));
            }
            check(exceptionSatisfied, "step " + order + " uses forbidden transition " + componentId + " " + from
                    + " -> " + to);
        }
    }

    private static Object readJson(String relativePath) throws IOException {
        return Json.parse(Files.readString(Path.of(relativePath), StandardCharsets.UTF_8));
    }

    private static Map<String, Map<String, Object>> indexById(List<Object> values, String path) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, path + " item");
            String id = string(present(item, "id", path + " item"), path + " id");
            check(result.put(id, item) == null, "duplicate id " + id + " in " + path);
        }
        return result;
    }

    private static Set<String> strings(List<Object> values, String path) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            String item = string(value, path);
            check(result.add(item), "duplicate " + path + " value " + item);
        }
        return result;
    }

    private static Object present(Map<String, Object> map, String key, String path) {
        if (!map.containsKey(key)) {
            throw new AssertionError("missing " + path + "." + key);
        }
        return map.get(key);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String path) {
        if (!(value instanceof Map<?, ?>)) {
            throw new AssertionError(path + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String path) {
        if (!(value instanceof List<?>)) {
            throw new AssertionError(path + " must be an array");
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String path) {
        if (!(value instanceof String text)) {
            throw new AssertionError(path + " must be a string");
        }
        return text;
    }

    private static boolean bool(Object value, String path) {
        if (!(value instanceof Boolean flag)) {
            throw new AssertionError(path + " must be a boolean");
        }
        return flag;
    }

    private static int integer(Object value, String path) {
        if (!(value instanceof Number number)) {
            throw new AssertionError(path + " must be an integer");
        }
        try {
            return new BigDecimal(number.toString()).intValueExact();
        } catch (ArithmeticException exception) {
            throw new AssertionError(path + " must be an integer", exception);
        }
    }

    private static void check(boolean condition, String message) {
        assertions++;
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void checkEquals(Object expected, Object actual, String message) {
        check(expected == null ? actual == null : expected.equals(actual),
                message + ": expected " + expected + " but got " + actual);
    }

    /** JSON Schema/OpenAPI subset covering all constraints used by the pinned schemas. */
    private static final class SchemaValidator {
        private final Object root;

        SchemaValidator(Object root) {
            this.root = root;
        }

        void validateRoot(Object value, String path) {
            validate(root, value, path);
        }

        void validateReference(String reference, Object value, String path) {
            validate(resolve(reference), value, path);
        }

        private void validate(Object schemaValue, Object value, String path) {
            if (schemaValue instanceof Boolean allowed) {
                if (!allowed) {
                    fail(path, "is rejected by a false schema");
                }
                return;
            }
            Map<String, Object> schema = schemaObject(schemaValue, path);
            if (schema.containsKey("$ref")) {
                validate(resolve(schemaString(schema.get("$ref"), path + ".$ref")), value, path);
            }
            if (Boolean.TRUE.equals(schema.get("nullable")) && value == null) {
                return;
            }
            if (schema.containsKey("allOf")) {
                for (Object part : schemaArray(schema.get("allOf"), path + ".allOf")) {
                    validate(part, value, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                int valid = countValid(schemaArray(schema.get("anyOf"), path + ".anyOf"), value, path);
                if (valid == 0) {
                    fail(path, "does not satisfy anyOf");
                }
            }
            if (schema.containsKey("oneOf")) {
                int valid = countValid(schemaArray(schema.get("oneOf"), path + ".oneOf"), value, path);
                if (valid != 1) {
                    fail(path, "must satisfy exactly one oneOf branch, matched " + valid);
                }
            }
            if (schema.containsKey("not")) {
                try {
                    validate(schema.get("not"), value, path);
                } catch (SchemaFailure expected) {
                    expectedFailure();
                    // The value is outside the prohibited schema, as required.
                    validateAfterNot(schema, value, path);
                    return;
                }
                fail(path, "satisfies a prohibited not schema");
            }
            if (schema.containsKey("const") && !jsonEquals(schema.get("const"), value)) {
                fail(path, "must equal const " + schema.get("const"));
            }
            if (schema.containsKey("enum")) {
                boolean found = false;
                for (Object candidate : schemaArray(schema.get("enum"), path + ".enum")) {
                    found |= jsonEquals(candidate, value);
                }
                if (!found) {
                    fail(path, "is not an allowed enum value");
                }
            }

            if (schema.containsKey("type") && !matchesType(schema.get("type"), value, path)) {
                fail(path, "has wrong type; expected " + schema.get("type"));
            }

            if (value instanceof Map<?, ?> rawObject) {
                validateObject(schema, rawObject, path);
            } else if (value instanceof List<?> list) {
                validateArray(schema, list, path);
            } else if (value instanceof String text) {
                validateString(schema, text, path);
            } else if (value instanceof Number number) {
                validateNumber(schema, number, path);
            }
        }

        private void validateAfterNot(Map<String, Object> schema, Object value, String path) {
            Map<String, Object> remainder = new LinkedHashMap<>(schema);
            remainder.remove("not");
            validate(remainder, value, path);
        }

        private void validateObject(Map<String, Object> schema, Map<?, ?> value, String path) {
            if (schema.containsKey("minProperties")
                    && value.size() < schemaInteger(schema.get("minProperties"), path + ".minProperties")) {
                fail(path, "has too few properties");
            }
            if (schema.containsKey("maxProperties")
                    && value.size() > schemaInteger(schema.get("maxProperties"), path + ".maxProperties")) {
                fail(path, "has too many properties");
            }
            if (schema.containsKey("required")) {
                for (Object keyValue : schemaArray(schema.get("required"), path + ".required")) {
                    String key = schemaString(keyValue, path + ".required");
                    if (!value.containsKey(key)) {
                        fail(path, "is missing required property " + key);
                    }
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? schemaObject(schema.get("properties"), path + ".properties")
                    : Map.of();
            for (Map.Entry<?, ?> entry : value.entrySet()) {
                String key = String.valueOf(entry.getKey());
                if (properties.containsKey(key)) {
                    validate(properties.get(key), entry.getValue(), path + "." + key);
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    fail(path, "contains unknown property " + key);
                } else if (schema.get("additionalProperties") instanceof Map<?, ?>
                        || schema.get("additionalProperties") instanceof Boolean) {
                    validate(schema.get("additionalProperties"), entry.getValue(), path + "." + key);
                }
            }
        }

        private void validateArray(Map<String, Object> schema, List<?> value, String path) {
            if (schema.containsKey("minItems")
                    && value.size() < schemaInteger(schema.get("minItems"), path + ".minItems")) {
                fail(path, "has too few items");
            }
            if (schema.containsKey("maxItems")
                    && value.size() > schemaInteger(schema.get("maxItems"), path + ".maxItems")) {
                fail(path, "has too many items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                Set<Object> unique = new HashSet<>();
                for (Object item : value) {
                    if (!unique.add(item)) {
                        fail(path, "contains duplicate items");
                    }
                }
            }
            if (schema.containsKey("items")) {
                for (int index = 0; index < value.size(); index++) {
                    validate(schema.get("items"), value.get(index), path + "[" + index + "]");
                }
            }
        }

        private void validateString(Map<String, Object> schema, String value, String path) {
            int codePoints = value.codePointCount(0, value.length());
            if (schema.containsKey("minLength")
                    && codePoints < schemaInteger(schema.get("minLength"), path + ".minLength")) {
                fail(path, "is shorter than minLength");
            }
            if (schema.containsKey("maxLength")
                    && codePoints > schemaInteger(schema.get("maxLength"), path + ".maxLength")) {
                fail(path, "is longer than maxLength");
            }
            if (schema.containsKey("pattern")) {
                String pattern = schemaString(schema.get("pattern"), path + ".pattern");
                if (!Pattern.compile(pattern).matcher(value).find()) {
                    fail(path, "does not match pattern " + pattern);
                }
            }
        }

        private void validateNumber(Map<String, Object> schema, Number value, String path) {
            BigDecimal number = new BigDecimal(value.toString());
            if (schema.containsKey("minimum")
                    && number.compareTo(new BigDecimal(schema.get("minimum").toString())) < 0) {
                fail(path, "is below minimum");
            }
            if (schema.containsKey("maximum")
                    && number.compareTo(new BigDecimal(schema.get("maximum").toString())) > 0) {
                fail(path, "is above maximum");
            }
            if (schema.containsKey("exclusiveMinimum")) {
                Object exclusive = schema.get("exclusiveMinimum");
                if (exclusive instanceof Number
                        && number.compareTo(new BigDecimal(exclusive.toString())) <= 0) {
                    fail(path, "is not above exclusiveMinimum");
                }
            }
            if (schema.containsKey("exclusiveMaximum")) {
                Object exclusive = schema.get("exclusiveMaximum");
                if (exclusive instanceof Number
                        && number.compareTo(new BigDecimal(exclusive.toString())) >= 0) {
                    fail(path, "is not below exclusiveMaximum");
                }
            }
        }

        private int countValid(List<Object> schemas, Object value, String path) {
            int valid = 0;
            for (Object candidate : schemas) {
                try {
                    validate(candidate, value, path);
                    valid++;
                } catch (SchemaFailure expected) {
                    expectedFailure();
                }
            }
            return valid;
        }

        private boolean matchesType(Object typeValue, Object value, String path) {
            if (typeValue instanceof List<?>) {
                for (Object candidate : schemaArray(typeValue, path + ".type")) {
                    if (matchesType(candidate, value, path)) {
                        return true;
                    }
                }
                return false;
            }
            String type = schemaString(typeValue, path + ".type");
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "boolean" -> value instanceof Boolean;
                case "number" -> value instanceof Number;
                case "integer" -> isInteger(value);
                case "null" -> value == null;
                default -> throw new SchemaFailure(path + ": unsupported schema type " + type);
            };
        }

        private boolean isInteger(Object value) {
            if (!(value instanceof Number number)) {
                return false;
            }
            try {
                new BigDecimal(number.toString()).toBigIntegerExact();
                return true;
            } catch (ArithmeticException exception) {
                return false;
            }
        }

        private Object resolve(String reference) {
            if (!reference.startsWith("#/")) {
                throw new SchemaFailure("only local schema references are permitted: " + reference);
            }
            Object current = root;
            for (String token : reference.substring(2).split("/")) {
                String decoded = token.replace("~1", "/").replace("~0", "~");
                Map<String, Object> map = schemaObject(current, reference);
                if (!map.containsKey(decoded)) {
                    throw new SchemaFailure("unresolved schema reference " + reference);
                }
                current = map.get(decoded);
            }
            return current;
        }

        private boolean jsonEquals(Object left, Object right) {
            if (left instanceof Number && right instanceof Number) {
                return new BigDecimal(left.toString()).compareTo(new BigDecimal(right.toString())) == 0;
            }
            return left == null ? right == null : left.equals(right);
        }

        @SuppressWarnings("unchecked")
        private Map<String, Object> schemaObject(Object value, String path) {
            if (!(value instanceof Map<?, ?>)) {
                throw new SchemaFailure(path + ": schema node is not an object");
            }
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        private List<Object> schemaArray(Object value, String path) {
            if (!(value instanceof List<?>)) {
                throw new SchemaFailure(path + ": schema node is not an array");
            }
            return (List<Object>) value;
        }

        private String schemaString(Object value, String path) {
            if (!(value instanceof String text)) {
                throw new SchemaFailure(path + ": schema value is not a string");
            }
            return text;
        }

        private int schemaInteger(Object value, String path) {
            if (!(value instanceof Number number)) {
                throw new SchemaFailure(path + ": schema value is not an integer");
            }
            return new BigDecimal(number.toString()).intValueExact();
        }

        private void fail(String path, String message) {
            throw new SchemaFailure(path + ": " + message);
        }

        private void expectedFailure() {
            // Documents that a caught SchemaFailure is part of branch evaluation.
        }
    }

    private static final class SchemaFailure extends RuntimeException {
        SchemaFailure(String message) {
            super(message);
        }
    }

    /** Small strict JSON parser so the harness has no network or library dependency. */
    private static final class Json {
        private final String source;
        private int position;

        private Json(String source) {
            this.source = source;
        }

        static Object parse(String source) {
            if (source == null) {
                throw new AssertionError("JSON text must not be null");
            }
            Json parser = new Json(source);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != source.length()) {
                parser.error("trailing content");
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (position >= source.length()) {
                error("expected value");
            }
            return switch (source.charAt(position)) {
                case '{' -> objectValue();
                case '[' -> arrayValue();
                case '"' -> stringValue();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> numberValue();
            };
        }

        private Map<String, Object> objectValue() {
            position++;
            whitespace();
            Map<String, Object> result = new LinkedHashMap<>();
            if (consume('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (position >= source.length() || source.charAt(position) != '"') {
                    error("expected object key");
                }
                String key = stringValue();
                whitespace();
                require(':');
                Object value = value();
                boolean duplicate = result.containsKey(key);
                result.put(key, value);
                if (duplicate) {
                    error("duplicate object key " + key);
                }
                whitespace();
                if (consume('}')) {
                    return result;
                }
                require(',');
            }
        }

        private List<Object> arrayValue() {
            position++;
            whitespace();
            List<Object> result = new ArrayList<>();
            if (consume(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (consume(']')) {
                    return result;
                }
                require(',');
            }
        }

        private String stringValue() {
            require('"');
            StringBuilder result = new StringBuilder();
            while (position < source.length()) {
                char current = source.charAt(position++);
                if (current == '"') {
                    return result.toString();
                }
                if (current == '\\') {
                    if (position >= source.length()) {
                        error("unfinished escape");
                    }
                    char escaped = source.charAt(position++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicodeEscape());
                        default -> error("bad escape");
                    }
                } else {
                    if (current < 0x20) {
                        error("control character in string");
                    }
                    result.append(current);
                }
            }
            error("unterminated string");
            return null;
        }

        private char unicodeEscape() {
            if (position + 4 > source.length()) {
                error("short unicode escape");
            }
            try {
                char value = (char) Integer.parseInt(source.substring(position, position + 4), 16);
                position += 4;
                return value;
            } catch (NumberFormatException exception) {
                error("bad unicode escape");
                return 0;
            }
        }

        private Object numberValue() {
            int start = position;
            if (consume('-')) {
                // sign consumed
            }
            if (consume('0')) {
                if (position < source.length() && Character.isDigit(source.charAt(position))) {
                    error("leading zero in number");
                }
            } else {
                digits();
            }
            boolean decimal = false;
            if (consume('.')) {
                decimal = true;
                digits();
            }
            if (position < source.length() && (source.charAt(position) == 'e' || source.charAt(position) == 'E')) {
                decimal = true;
                position++;
                if (!consume('+')) {
                    consume('-');
                }
                digits();
            }
            if (start == position) {
                error("expected number");
            }
            String token = source.substring(start, position);
            try {
                return decimal ? new BigDecimal(token) : Long.valueOf(token);
            } catch (NumberFormatException exception) {
                error("invalid number");
                return null;
            }
        }

        private void digits() {
            int start = position;
            while (position < source.length() && Character.isDigit(source.charAt(position))) {
                position++;
            }
            if (start == position) {
                error("expected digits");
            }
        }

        private Object literal(String token, Object value) {
            if (!source.startsWith(token, position)) {
                error("bad literal");
            }
            position += token.length();
            return value;
        }

        private void whitespace() {
            while (position < source.length()) {
                char current = source.charAt(position);
                if (current != ' ' && current != '\n' && current != '\r' && current != '\t') {
                    return;
                }
                position++;
            }
        }

        private boolean consume(char expected) {
            if (position < source.length() && source.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void require(char expected) {
            if (!consume(expected)) {
                error("expected '" + expected + "'");
            }
        }

        private void error(String message) {
            throw new AssertionError("invalid JSON at offset " + position + ": " + message);
        }
    }
}
