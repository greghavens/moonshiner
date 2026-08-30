import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** Protected acceptance harness for the brownfield VCF architecture artifact. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String inventoryJson = read("estate-inventory.json");
        String compatibilityJson = read("compatibility-snapshot.json");
        String openApiJson = read("specifications/vcf-installer/vcf-installer-openapi.json");

        String result = ArchitectureClient.buildPlan(inventoryJson, compatibilityJson);
        Object artifact = Json.parse(result);
        Map<String, Object> openApi = object(Json.parse(openApiJson), "installer OpenAPI root");
        Object sddcSpec = artifact instanceof Map<?, ?>
                ? ((Map<?, ?>) artifact).get("targetSddcSpec")
                : null;

        // This is intentionally the first validation. All migration-plan assertions follow it.
        Object officialSddcSchema = path(openApi, "components", "schemas", "SddcSpec");
        SchemaValidator.validate(sddcSpec, officialSddcSchema, openApi, "$.targetSddcSpec");

        Map<String, Object> plan = object(artifact, "migration plan");
        Map<String, Object> planSchema = object(
                Json.parse(read("migration-plan-schema.json")), "migration plan schema");
        SchemaValidator.validate(plan, planSchema, planSchema, "$");

        String secondResult = ArchitectureClient.buildPlan(inventoryJson, compatibilityJson);
        check(result.equals(secondResult), "buildPlan must be deterministic");

        Map<String, Object> inventory = object(Json.parse(inventoryJson), "estate inventory");
        Map<String, Object> compatibility = object(
                Json.parse(compatibilityJson), "compatibility snapshot");
        verifyIdentityAndReleasePath(plan, inventory, compatibility);
        Map<String, Object> selectedProfile = verifyStorageDecision(plan, inventory, compatibility);
        verifyTargetArchitecture(plan, inventory, selectedProfile);
        verifyComponentsAndSteps(plan, inventory, compatibility);
        verifyTargetSddcSpec(object(plan.get("targetSddcSpec"), "targetSddcSpec"),
                inventory, compatibility, selectedProfile);
        verifyResearchSources(plan);

        System.out.println("PASS: migration architecture matches the installer schema, estate, and pinned compatibility snapshot");
    }

    private static void verifyIdentityAndReleasePath(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> compatibility) {
        String source = string(inventory.get("vcfVersion"), "inventory.vcfVersion");
        String target = string(compatibility.get("targetVcfVersion"), "snapshot.targetVcfVersion");
        equal(plan.get("estateId"), inventory.get("estateId"), "estateId");
        equal(plan.get("sourceVcfVersion"), source, "sourceVcfVersion");
        equal(plan.get("targetVcfVersion"), target, "targetVcfVersion");

        List<Object> releasePath = array(plan.get("releasePath"), "releasePath");
        check(releasePath.size() >= 2, "releasePath must contain source and target");
        equal(releasePath.get(0), source, "releasePath source");
        equal(releasePath.get(releasePath.size() - 1), target, "releasePath target");

        Set<String> supported = new LinkedHashSet<>();
        for (Object value : array(compatibility.get("supportedReleaseHops"), "supportedReleaseHops")) {
            Map<String, Object> hop = object(value, "supportedReleaseHop");
            supported.add(string(hop.get("from"), "hop.from") + "->"
                    + string(hop.get("to"), "hop.to"));
        }
        for (int index = 1; index < releasePath.size(); index++) {
            String edge = string(releasePath.get(index - 1), "releasePath entry") + "->"
                    + string(releasePath.get(index), "releasePath entry");
            check(supported.contains(edge), "unsupported release hop: " + edge);
        }
    }

    private static Map<String, Object> verifyStorageDecision(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> compatibility) {
        Map<String, Object> decision = object(plan.get("storageDecision"), "storageDecision");
        Map<String, Object> cluster = object(inventory.get("managementCluster"), "managementCluster");
        Map<String, Object> requirements = object(inventory.get("requirements"), "requirements");
        equal(decision.get("sourceArchitecture"), cluster.get("storageArchitecture"),
                "storageDecision.sourceArchitecture");

        List<Object> expectedOptions = array(compatibility.get("storageOptions"), "storageOptions");
        List<Object> actualOptions = array(decision.get("consideredOptions"), "consideredOptions");
        check(actualOptions.size() == expectedOptions.size(),
                "consideredOptions must name every pinned OSA/ESA option");

        long minimumIops = integer(requirements.get("minimumSustainedIops"), "minimumSustainedIops");
        long minimumCapacity = integer(requirements.get("minimumUsableCapacityTb"), "minimumUsableCapacityTb");
        boolean dualFabric = bool(requirements.get("requireDualFabric"), "requireDualFabric");
        Map<String, Object> selected = null;
        for (Object expectedValue : expectedOptions) {
            Map<String, Object> expected = object(expectedValue, "storage option");
            String architecture = string(expected.get("architecture"), "storage option architecture");
            Map<String, Object> actual = findBy(actualOptions, "architecture", architecture,
                    "considered storage option");
            equal(actual.get("hostCount"), expected.get("designHostCount"), architecture + " hostCount");
            equal(actual.get("vsanUplinks"), expected.get("minimumVsanUplinks"),
                    architecture + " vsanUplinks");
            equal(actual.get("uplinkSpeedGbps"), expected.get("uplinkSpeedGbps"),
                    architecture + " uplinkSpeedGbps");
            equal(actual.get("supportedSustainedIops"), expected.get("supportedSustainedIops"),
                    architecture + " supportedSustainedIops");
            boolean viable = integer(expected.get("supportedSustainedIops"), "supportedSustainedIops") >= minimumIops
                    && integer(expected.get("usableCapacityTb"), "usableCapacityTb") >= minimumCapacity
                    && (!dualFabric || integer(expected.get("minimumVsanUplinks"), "minimumVsanUplinks") >= 2);
            check(bool(actual.get("meetsRequirements"), architecture + " meetsRequirements") == viable,
                    architecture + " viability is incorrect");
            if (viable && (selected == null
                    || integer(expected.get("designHostCount"), "designHostCount")
                    < integer(selected.get("designHostCount"), "designHostCount"))) {
                selected = expected;
            }
        }
        check(selected != null, "pinned snapshot must contain a viable storage option");
        equal(decision.get("selectedArchitecture"), selected.get("architecture"),
                "selectedArchitecture");
        equal(decision.get("selectedArchitecture"), "ESA",
                "the required performance must select ESA");
        return selected;
    }

    private static void verifyTargetArchitecture(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> profile) {
        Map<String, Object> target = object(plan.get("targetArchitecture"), "targetArchitecture");
        Map<String, Object> source = object(inventory.get("managementCluster"), "managementCluster");
        equal(target.get("managementCluster"), source.get("name"), "target managementCluster");
        equal(target.get("storageArchitecture"), profile.get("architecture"), "target storageArchitecture");
        equal(target.get("storageMigrationMode"), profile.get("migrationMode"),
                "target storageMigrationMode");
        equal(target.get("hostCount"), profile.get("designHostCount"), "target hostCount");
        equal(target.get("hostModel"), profile.get("hostModel"), "target hostModel");
        equal(target.get("vsanUplinks"), profile.get("minimumVsanUplinks"), "target vsanUplinks");
        equal(target.get("uplinkSpeedGbps"), profile.get("uplinkSpeedGbps"), "target uplinkSpeedGbps");
        check(integer(target.get("vsanMtu"), "target vsanMtu") == 9000, "target vSAN MTU must be 9000");
        check(integer(target.get("hostCount"), "target hostCount")
                        != integer(source.get("hostCount"), "source hostCount"),
                "storage selection must change the host count");
        check(integer(target.get("uplinkSpeedGbps"), "target uplinkSpeedGbps")
                        != integer(source.get("uplinkSpeedGbps"), "source uplinkSpeedGbps"),
                "storage selection must change the network requirement");
    }

    private static void verifyComponentsAndSteps(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> compatibility) {
        List<Object> inventoryComponents = array(inventory.get("components"), "inventory components");
        List<Object> components = array(plan.get("components"), "plan components");
        Map<String, Object> targets = object(compatibility.get("targetComponents"), "targetComponents");
        Map<String, Object> allGates = object(compatibility.get("componentGates"), "componentGates");
        check(components.size() == inventoryComponents.size(),
                "components must name every and only inventoried component");

        for (Object inventoryValue : inventoryComponents) {
            Map<String, Object> source = object(inventoryValue, "inventory component");
            String name = string(source.get("name"), "component name");
            Map<String, Object> transition = findBy(components, "name", name, "component transition");
            equal(transition.get("type"), source.get("type"), name + " type");
            equal(transition.get("currentVersion"), source.get("version"), name + " currentVersion");
            equal(transition.get("targetVersion"), targets.get(name), name + " targetVersion");
            equal(transition.get("gatedBy"), allGates.get(name), name + " gates");
        }

        List<Object> order = array(compatibility.get("upgradeOrder"), "upgradeOrder");
        List<Object> steps = array(plan.get("steps"), "steps");
        check(steps.size() == order.size(), "steps must cover every ordered component exactly once");
        Set<String> seen = new LinkedHashSet<>();
        for (int index = 0; index < order.size(); index++) {
            String expectedName = string(order.get(index), "upgradeOrder entry");
            Map<String, Object> step = object(steps.get(index), "step " + (index + 1));
            check(integer(step.get("sequence"), "step sequence") == index + 1,
                    "step sequence must be contiguous and one-based");
            equal(step.get("component"), expectedName, "step component order");
            check(seen.add(expectedName), "duplicate component step: " + expectedName);
            String expectedAction = "vSAN".equals(expectedName) ? "MIGRATE_STORAGE" : "UPGRADE_COMPONENT";
            equal(step.get("action"), expectedAction, expectedName + " action");
            if ("vSAN".equals(expectedName)) {
                Map<String, Object> selectedProfile = findBy(
                        array(compatibility.get("storageOptions"), "storageOptions"),
                        "architecture", "ESA", "selected storage profile");
                equal(step.get("migrationMode"), selectedProfile.get("migrationMode"),
                        "vSAN migrationMode");
            } else {
                check(!step.containsKey("migrationMode"),
                        expectedName + " must not carry a storage migrationMode");
            }
            Map<String, Object> source = findBy(inventoryComponents, "name", expectedName,
                    "inventory component");
            equal(step.get("fromVersion"), source.get("version"), expectedName + " step fromVersion");
            equal(step.get("targetVersion"), targets.get(expectedName), expectedName + " step targetVersion");
            equal(step.get("gatedBy"), allGates.get(expectedName), expectedName + " step gates");
        }
    }

    private static void verifyTargetSddcSpec(
            Map<String, Object> sddc,
            Map<String, Object> inventory,
            Map<String, Object> compatibility,
            Map<String, Object> profile) {
        Map<String, Object> domain = object(inventory.get("domain"), "domain");
        Map<String, Object> targets = object(compatibility.get("targetComponents"), "targetComponents");
        equal(sddc.get("sddcId"), domain.get("sddcId"), "targetSddcSpec.sddcId");
        equal(sddc.get("version"), compatibility.get("targetVcfVersion"), "targetSddcSpec.version");
        equal(sddc.get("workflowType"), "VCF", "targetSddcSpec.workflowType");
        equal(sddc.get("vcfInstanceName"), domain.get("vcfInstanceName"), "targetSddcSpec.vcfInstanceName");

        Map<String, Object> vcenter = object(sddc.get("vcenterSpec"), "target vcenterSpec");
        equal(vcenter.get("vcenterHostname"), domain.get("vcenterHostname"), "vCenter hostname");
        equal(vcenter.get("version"), targets.get("vCenter Server"), "vCenter target version");
        equal(vcenter.get("rootVcenterPassword"),
                inventoryPath(inventory, "fixtureCredentials", "vcenterRootPassword"),
                "vCenter fixture credential");
        check(bool(vcenter.get("useExistingDeployment"), "useExistingDeployment"),
                "target must identify the existing vCenter deployment");

        Map<String, Object> dns = object(sddc.get("dnsSpec"), "target dnsSpec");
        equal(dns.get("subdomain"), domain.get("dnsSubdomain"), "DNS subdomain");
        equal(dns.get("nameservers"), domain.get("dnsServers"), "DNS nameservers");
        equal(sddc.get("ntpServers"), inventoryPath(inventory, "domain", "ntpServers"), "NTP servers");

        List<Object> hostSpecs = array(sddc.get("hostSpecs"), "target hostSpecs");
        List<Object> expectedHosts = array(inventory.get("targetHostnames"), "targetHostnames");
        check(hostSpecs.size() == integer(profile.get("designHostCount"), "designHostCount"),
                "target SDDC host count mismatch");
        check(hostSpecs.size() == expectedHosts.size(), "target SDDC must name all staged ESA hosts");
        for (int index = 0; index < expectedHosts.size(); index++) {
            equal(object(hostSpecs.get(index), "hostSpec").get("hostname"), expectedHosts.get(index),
                    "target host order");
        }

        Map<String, Object> cluster = object(sddc.get("clusterSpec"), "target clusterSpec");
        equal(cluster.get("clusterName"), inventoryPath(inventory, "managementCluster", "name"),
                "cluster name");
        equal(cluster.get("datacenterName"), inventoryPath(inventory, "managementCluster", "datacenterName"),
                "datacenter name");

        Map<String, Object> datastore = object(sddc.get("datastoreSpec"), "target datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "target vsanSpec");
        Map<String, Object> esa = object(vsan.get("esaConfig"), "target esaConfig");
        check(bool(esa.get("enabled"), "ESA enabled"), "target SDDC must enable vSAN ESA");
        equal(vsan.get("failuresToTolerate"), inventoryPath(inventory, "requirements", "failuresToTolerate"),
                "vSAN failuresToTolerate");

        Map<String, Map<String, Object>> networks = new LinkedHashMap<>();
        for (Object networkValue : array(sddc.get("networkSpecs"), "networkSpecs")) {
            Map<String, Object> network = object(networkValue, "networkSpec");
            networks.put(string(network.get("networkType"), "networkType"), network);
        }
        verifyNetwork(networks, "MANAGEMENT", domain.get("managementVlan"), 1500);
        verifyNetwork(networks, "VMOTION", domain.get("vmotionVlan"), 9000);
        verifyNetwork(networks, "VSAN", domain.get("vsanVlan"), 9000);

        List<Object> dvsSpecs = array(sddc.get("dvsSpecs"), "dvsSpecs");
        check(dvsSpecs.size() == 1, "target design must define one dual-fabric DVS");
        List<Object> mappings = array(object(dvsSpecs.get(0), "dvsSpec").get("vmnicsToUplinks"),
                "vmnicsToUplinks");
        check(mappings.size() == 2, "target DVS must map both required uplinks");
        equal(object(mappings.get(0), "vmnic mapping").get("id"), "vmnic0", "first vmnic");
        equal(object(mappings.get(1), "vmnic mapping").get("id"), "vmnic1", "second vmnic");
    }

    private static void verifyResearchSources(Map<String, Object> plan) {
        List<Object> sources = array(plan.get("researchSources"), "researchSources");
        check(!sources.isEmpty(), "researchSources must record the Broadcom sources consulted");
        Set<String> urls = new LinkedHashSet<>();
        for (Object value : sources) {
            Map<String, Object> source = object(value, "research source");
            String title = string(source.get("title"), "research source title");
            String url = string(source.get("url"), "research source URL");
            check(!title.isBlank(), "research source title must not be blank");
            check(url.equals(url.trim()), "research source URL must be exact and unpadded");
            check(urls.add(url), "research source URLs must be unique: " + url);
            try {
                URI uri = new URI(url);
                String host = uri.getHost();
                check("https".equals(uri.getScheme()), "research source must use HTTPS: " + url);
                check(host != null && (host.equals("broadcom.com") || host.endsWith(".broadcom.com")),
                        "research source must be Broadcom-published: " + url);
            } catch (URISyntaxException exception) {
                throw new AssertionError("research source URL is invalid: " + url, exception);
            }
        }
    }

    private static void verifyNetwork(
            Map<String, Map<String, Object>> networks,
            String type,
            Object expectedVlan,
            long expectedMtu) {
        Map<String, Object> network = networks.get(type);
        check(network != null, "missing " + type + " network");
        equal(network.get("vlanId"), expectedVlan, type + " VLAN");
        check(integer(network.get("mtu"), type + " MTU") == expectedMtu,
                type + " MTU must be " + expectedMtu);
    }

    private static Map<String, Object> findBy(
            List<Object> values, String key, String expected, String label) {
        Map<String, Object> found = null;
        for (Object value : values) {
            Map<String, Object> candidate = object(value, label);
            if (expected.equals(candidate.get(key))) {
                check(found == null, "duplicate " + label + ": " + expected);
                found = candidate;
            }
        }
        check(found != null, "missing " + label + ": " + expected);
        return found;
    }

    private static Object inventoryPath(Map<String, Object> inventory, String first, String second) {
        return object(inventory.get(first), first).get(second);
    }

    private static String read(String name) throws IOException {
        return Files.readString(Path.of(name), StandardCharsets.UTF_8);
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

    private static long integer(Object value, String label) {
        check(value instanceof BigDecimal, label + " must be an integer");
        try {
            return ((BigDecimal) value).longValueExact();
        } catch (ArithmeticException exception) {
            throw new AssertionError(label + " must be an exact integer", exception);
        }
    }

    private static boolean bool(Object value, String label) {
        check(value instanceof Boolean, label + " must be a boolean");
        return (Boolean) value;
    }

    private static void equal(Object actual, Object expected, String label) {
        check(Objects.equals(actual, expected), label + " mismatch: expected " + expected + ", got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static Object path(Map<String, Object> root, String... segments) {
        Object current = root;
        for (String segment : segments) {
            current = object(current, "schema path segment " + segment).get(segment);
            check(current != null, "missing schema path segment: " + segment);
        }
        return current;
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(Object instance, Object schemaValue, Map<String, Object> root, String path) {
            Map<String, Object> schema = object(schemaValue, path + " schema");
            if (schema.containsKey("$ref")) {
                Object resolved = resolve(root, string(schema.get("$ref"), path + " $ref"));
                validate(instance, resolved, root, path);
                return;
            }

            validateCombinators(instance, schema, root, path);
            if (schema.containsKey("enum")) {
                check(array(schema.get("enum"), path + " enum").contains(instance),
                        path + " is not an allowed enum value");
            }

            Object typeValue = schema.get("type");
            if (typeValue instanceof String type) {
                check(matchesType(instance, type), path + " must have JSON type " + type);
            }
            if (instance == null) {
                return;
            }
            if (instance instanceof Map<?, ?> mapValue) {
                validateObject(object(mapValue, path), schema, root, path);
            } else if (instance instanceof List<?> listValue) {
                validateArray(array(listValue, path), schema, root, path);
            } else if (instance instanceof String stringValue) {
                validateString(stringValue, schema, path);
            } else if (instance instanceof BigDecimal numberValue) {
                validateNumber(numberValue, schema, path);
            }
        }

        private static void validateCombinators(
                Object instance, Map<String, Object> schema, Map<String, Object> root, String path) {
            if (schema.containsKey("allOf")) {
                for (Object child : array(schema.get("allOf"), path + " allOf")) {
                    validate(instance, child, root, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                int matches = matchingBranches(instance, array(schema.get("anyOf"), path + " anyOf"), root, path);
                check(matches >= 1, path + " must match at least one anyOf branch");
            }
            if (schema.containsKey("oneOf")) {
                int matches = matchingBranches(instance, array(schema.get("oneOf"), path + " oneOf"), root, path);
                check(matches == 1, path + " must match exactly one oneOf branch, matched " + matches);
            }
        }

        private static int matchingBranches(
                Object instance, List<Object> branches, Map<String, Object> root, String path) {
            int matches = 0;
            for (Object branch : branches) {
                try {
                    validate(instance, branch, root, path);
                    matches++;
                } catch (AssertionError ignored) {
                    // A failed alternative is expected while evaluating a combinator.
                }
            }
            return matches;
        }

        private static void validateObject(
                Map<String, Object> instance,
                Map<String, Object> schema,
                Map<String, Object> root,
                String path) {
            if (schema.containsKey("required")) {
                for (Object required : array(schema.get("required"), path + " required")) {
                    String name = string(required, path + " required entry");
                    check(instance.containsKey(name), path + " is missing required property " + name);
                }
            }
            Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?>
                    ? object(schema.get("properties"), path + " properties")
                    : Map.of();
            for (Map.Entry<String, Object> entry : properties.entrySet()) {
                if (instance.containsKey(entry.getKey())) {
                    validate(instance.get(entry.getKey()), entry.getValue(), root, path + "." + entry.getKey());
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : instance.keySet()) {
                    check(properties.containsKey(key), path + " contains unknown property " + key);
                }
            }
        }

        private static void validateArray(
                List<Object> instance,
                Map<String, Object> schema,
                Map<String, Object> root,
                String path) {
            if (schema.containsKey("minItems")) {
                check(instance.size() >= integer(schema.get("minItems"), path + " minItems"),
                        path + " has too few items");
            }
            if (schema.containsKey("maxItems")) {
                check(instance.size() <= integer(schema.get("maxItems"), path + " maxItems"),
                        path + " has too many items");
            }
            if (schema.containsKey("items")) {
                for (int index = 0; index < instance.size(); index++) {
                    validate(instance.get(index), schema.get("items"), root, path + "[" + index + "]");
                }
            }
        }

        private static void validateString(String instance, Map<String, Object> schema, String path) {
            int length = instance.codePointCount(0, instance.length());
            if (schema.containsKey("minLength")) {
                check(length >= integer(schema.get("minLength"), path + " minLength"),
                        path + " is shorter than minLength");
            }
            if (schema.containsKey("maxLength")) {
                check(length <= integer(schema.get("maxLength"), path + " maxLength"),
                        path + " is longer than maxLength");
            }
            if (schema.containsKey("pattern")) {
                Pattern pattern = Pattern.compile(string(schema.get("pattern"), path + " pattern"));
                check(pattern.matcher(instance).find(), path + " does not match its schema pattern");
            }
        }

        private static void validateNumber(BigDecimal instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("minimum")) {
                check(instance.compareTo(number(schema.get("minimum"), path + " minimum")) >= 0,
                        path + " is below minimum");
            }
            if (schema.containsKey("maximum")) {
                check(instance.compareTo(number(schema.get("maximum"), path + " maximum")) <= 0,
                        path + " is above maximum");
            }
        }

        private static boolean matchesType(Object instance, String type) {
            return switch (type) {
                case "null" -> instance == null;
                case "object" -> instance instanceof Map<?, ?>;
                case "array" -> instance instanceof List<?>;
                case "string" -> instance instanceof String;
                case "boolean" -> instance instanceof Boolean;
                case "number" -> instance instanceof BigDecimal;
                case "integer" -> instance instanceof BigDecimal number
                        && number.stripTrailingZeros().scale() <= 0;
                default -> throw new AssertionError("unsupported schema type in pinned specifications: " + type);
            };
        }

        private static BigDecimal number(Object value, String label) {
            check(value instanceof BigDecimal, label + " must be numeric");
            return (BigDecimal) value;
        }

        private static Object resolve(Map<String, Object> root, String reference) {
            check(reference.startsWith("#/"), "only local schema references are supported: " + reference);
            Object current = root;
            for (String token : reference.substring(2).split("/")) {
                String decoded = token.replace("~1", "/").replace("~0", "~");
                current = object(current, "schema reference " + reference).get(decoded);
                check(current != null, "unresolved schema reference: " + reference);
            }
            return current;
        }
    }

    private static final class Json {
        private final String source;
        private int index;

        private Json(String source) {
            this.source = Objects.requireNonNull(source, "JSON source");
        }

        static Object parse(String source) {
            Json parser = new Json(source);
            Object value = parser.value();
            parser.whitespace();
            check(parser.index == source.length(), "unexpected trailing JSON at offset " + parser.index);
            return value;
        }

        private Object value() {
            whitespace();
            check(index < source.length(), "unexpected end of JSON");
            char current = source.charAt(index);
            return switch (current) {
                case '{' -> objectValue();
                case '[' -> arrayValue();
                case '"' -> stringValue();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> {
                    check(current == '-' || Character.isDigit(current),
                            "unexpected JSON token at offset " + index);
                    yield numberValue();
                }
            };
        }

        private Map<String, Object> objectValue() {
            index++;
            LinkedHashMap<String, Object> values = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return values;
            }
            while (true) {
                whitespace();
                check(index < source.length() && source.charAt(index) == '"',
                        "expected object key at offset " + index);
                String key = stringValue();
                whitespace();
                check(take(':'), "expected ':' at offset " + index);
                check(!values.containsKey(key), "duplicate JSON key: " + key);
                values.put(key, value());
                whitespace();
                if (take('}')) {
                    return values;
                }
                check(take(','), "expected ',' at offset " + index);
            }
        }

        private List<Object> arrayValue() {
            index++;
            ArrayList<Object> values = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return values;
            }
            while (true) {
                values.add(value());
                whitespace();
                if (take(']')) {
                    return values;
                }
                check(take(','), "expected ',' at offset " + index);
            }
        }

        private String stringValue() {
            check(take('"'), "expected string at offset " + index);
            StringBuilder result = new StringBuilder();
            while (index < source.length()) {
                char current = source.charAt(index++);
                if (current == '"') {
                    return result.toString();
                }
                check(current >= 0x20, "unescaped control character in JSON string");
                if (current != '\\') {
                    result.append(current);
                    continue;
                }
                check(index < source.length(), "unterminated JSON escape");
                char escaped = source.charAt(index++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> result.append(unicodeEscape());
                    default -> throw new AssertionError("invalid JSON escape at offset " + (index - 1));
                }
            }
            throw new AssertionError("unterminated JSON string");
        }

        private char unicodeEscape() {
            check(index + 4 <= source.length(), "short unicode escape");
            int value = 0;
            for (int count = 0; count < 4; count++) {
                int digit = Character.digit(source.charAt(index++), 16);
                check(digit >= 0, "invalid unicode escape");
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private BigDecimal numberValue() {
            int start = index;
            if (take('-')) {
                check(index < source.length(), "incomplete JSON number");
            }
            if (take('0')) {
                check(index >= source.length() || !Character.isDigit(source.charAt(index)),
                        "leading zero in JSON number");
            } else {
                check(index < source.length() && source.charAt(index) >= '1' && source.charAt(index) <= '9',
                        "invalid JSON number at offset " + index);
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            }
            if (take('.')) {
                check(index < source.length() && Character.isDigit(source.charAt(index)),
                        "invalid JSON fraction");
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            }
            if (index < source.length() && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                index++;
                if (index < source.length() && (source.charAt(index) == '+' || source.charAt(index) == '-')) {
                    index++;
                }
                check(index < source.length() && Character.isDigit(source.charAt(index)),
                        "invalid JSON exponent");
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            }
            return new BigDecimal(source.substring(start, index));
        }

        private Object literal(String text, Object value) {
            check(source.startsWith(text, index), "invalid JSON literal at offset " + index);
            index += text.length();
            return value;
        }

        private boolean take(char expected) {
            if (index < source.length() && source.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void whitespace() {
            while (index < source.length()) {
                char current = source.charAt(index);
                if (current != ' ' && current != '\n' && current != '\r' && current != '\t') {
                    return;
                }
                index++;
            }
        }
    }
}
