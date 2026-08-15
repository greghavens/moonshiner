import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

public final class TestMain {
    private static final Path REQUIREMENTS = Path.of("fixtures/design-requirements.json");
    private static final Path INVENTORY = Path.of("fixtures/estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("fixtures/compatibility-snapshot.json");
    private static final Path INSTALLER_SCHEMA =
            Path.of("specifications/vcf-installer/vcf-installer-openapi.json");
    private static final Path MIGRATION_SCHEMA = Path.of("schemas/migration-plan.schema.json");

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Path output = Files.createTempDirectory(Path.of("."), ".vcfarch-output-");
        try {
            VcfArchitectureClient.main(new String[] {
                    REQUIREMENTS.toString(), INVENTORY.toString(), SNAPSHOT.toString(), output.toString()
            });

            // This is deliberately the first verification phase. Do not move fixture,
            // migration, or architecture assertions above the installer-schema check.
            Object installerDocument = Json.parse(Files.readString(INSTALLER_SCHEMA));
            Object sddcDocument = Json.parse(Files.readString(output.resolve("sddc-spec.json")));
            Object sddcSchema = pointer(installerDocument, "#/components/schemas/SddcSpec");
            SchemaValidator.validate(sddcDocument, sddcSchema, installerDocument, "$sddcSpec");
            System.out.println("installer SddcSpec schema: PASS");

            Map<String, Object> requirements = object(Json.parse(Files.readString(REQUIREMENTS)), "requirements");
            Map<String, Object> inventory = object(Json.parse(Files.readString(INVENTORY)), "inventory");
            Map<String, Object> snapshot = object(Json.parse(Files.readString(SNAPSHOT)), "snapshot");
            Map<String, Object> sddc = object(sddcDocument, "sddc-spec.json");

            Object migrationDocument = Json.parse(Files.readString(output.resolve("migration-plan.json")));
            Object migrationSchema = Json.parse(Files.readString(MIGRATION_SCHEMA));
            SchemaValidator.validate(migrationDocument, migrationSchema, migrationSchema, "$migrationPlan");

            verifyGreenfield(sddc, requirements, snapshot);
            verifyMigration(object(migrationDocument, "migration-plan.json"), inventory, snapshot);
            verifyResearchLog(array(Json.parse(Files.readString(output.resolve("research-log.json"))),
                    "research-log.json"));
            System.out.println("architecture, migration, and research-log semantics: PASS");
        } finally {
            deleteTree(output);
        }
    }

    private static void verifyGreenfield(Map<String, Object> sddc,
                                         Map<String, Object> requirements,
                                         Map<String, Object> snapshot) {
        Map<String, Object> deployment = object(requirements.get("deployment"), "deployment");
        Map<String, Object> site = object(requirements.get("site"), "site");
        Map<String, Object> greenfield = object(snapshot.get("greenfield"), "snapshot.greenfield");
        Map<String, Object> credentials = object(requirements.get("credentials"), "credentials");
        Map<String, Object> appliances = object(requirements.get("appliances"), "appliances");

        equal(string(deployment.get("model"), "deployment.model"),
                string(greenfield.get("deploymentModel"), "greenfield.deploymentModel"),
                "fixture and snapshot deployment models");
        equal(string(sddc.get("sddcId"), "sddcId"), string(deployment.get("sddcId"), "deployment.sddcId"),
                "sddcId");
        equal(string(sddc.get("workflowType"), "workflowType"),
                string(greenfield.get("workflowType"), "greenfield.workflowType"), "workflowType");
        equal(string(sddc.get("version"), "version"), string(snapshot.get("targetRelease"), "targetRelease"),
                "VCF release");
        equal(string(sddc.get("vcfInstanceName"), "vcfInstanceName"),
                string(deployment.get("vcfInstanceName"), "deployment.vcfInstanceName"), "VCF instance name");

        List<Object> availableHosts = array(requirements.get("hosts"), "requirements.hosts");
        List<Object> selectedHosts = array(sddc.get("hostSpecs"), "hostSpecs");
        int minimumHosts = integer(greenfield.get("minimumHostCount"), "minimumHostCount");
        check(selectedHosts.size() == minimumHosts, "consolidated design must use exactly " + minimumHosts + " hosts");
        check(availableHosts.size() == minimumHosts, "requirements must describe the minimum supported host set");

        Set<String> expectedHostnames = new LinkedHashSet<>();
        double totalCpu = 0;
        double totalMemory = 0;
        double totalStorage = 0;
        double maxCpu = 0;
        double maxMemory = 0;
        double maxStorage = 0;
        for (Object value : availableHosts) {
            Map<String, Object> host = object(value, "requirements.host");
            expectedHostnames.add(string(host.get("hostname"), "host.hostname"));
            double cpu = number(host.get("cpuCores"), "host.cpuCores");
            double memory = number(host.get("memoryGiB"), "host.memoryGiB");
            double storage = number(host.get("capacityTierTiB"), "host.capacityTierTiB");
            totalCpu += cpu;
            totalMemory += memory;
            totalStorage += storage;
            maxCpu = Math.max(maxCpu, cpu);
            maxMemory = Math.max(maxMemory, memory);
            maxStorage = Math.max(maxStorage, storage);
        }
        Set<String> actualHostnames = new LinkedHashSet<>();
        for (Object value : selectedHosts) {
            Map<String, Object> host = object(value, "hostSpecs[]");
            actualHostnames.add(string(host.get("hostname"), "hostSpecs.hostname"));
            Map<String, Object> hostCredentials = object(host.get("credentials"), "hostSpecs.credentials");
            equal(string(hostCredentials.get("username"), "host username"),
                    string(credentials.get("hostUsername"), "fixture host username"), "host username");
            equal(string(hostCredentials.get("password"), "host password"),
                    string(credentials.get("hostPassword"), "fixture host password"), "host password");
        }
        equal(actualHostnames, expectedHostnames, "selected host set");
        check(actualHostnames.size() == selectedHosts.size(), "hostnames must be unique");

        Map<String, Object> usable = object(requirements.get("minimumUsableAfterHostFailure"),
                "minimumUsableAfterHostFailure");
        check(totalCpu - maxCpu >= number(usable.get("cpuCores"), "usable.cpuCores"),
                "post-failure CPU capacity is insufficient");
        check(totalMemory - maxMemory >= number(usable.get("memoryGiB"), "usable.memoryGiB"),
                "post-failure memory capacity is insufficient");
        check((totalStorage - maxStorage) / 2.0 >=
                        number(usable.get("mirroredStorageTiB"), "usable.mirroredStorageTiB"),
                "post-failure mirrored storage capacity is insufficient");
        equal(integer(deployment.get("hostFailureTolerance"), "deployment.hostFailureTolerance"),
                integer(greenfield.get("hostFailureTolerance"), "greenfield.hostFailureTolerance"),
                "failure tolerance authority");

        Map<String, Object> vcenter = object(sddc.get("vcenterSpec"), "vcenterSpec");
        equal(string(vcenter.get("vcenterHostname"), "vcenter hostname"),
                string(appliances.get("vcenterHostname"), "fixture vcenter hostname"), "vCenter hostname");
        equal(string(vcenter.get("version"), "vcenter version"),
                string(greenfield.get("vcenterVersion"), "snapshot vcenter version"), "vCenter version");
        equal(string(vcenter.get("rootVcenterPassword"), "vcenter password"),
                string(credentials.get("vcenterRootPassword"), "fixture vcenter password"), "vCenter password");
        equal(bool(vcenter.get("useExistingDeployment"), "vcenter useExistingDeployment"), false,
                "greenfield vCenter flag");

        Map<String, Object> manager = object(sddc.get("sddcManagerSpec"), "sddcManagerSpec");
        equal(string(manager.get("hostname"), "manager hostname"),
                string(appliances.get("sddcManagerHostname"), "fixture manager hostname"), "SDDC Manager hostname");
        equal(string(manager.get("version"), "manager version"),
                string(greenfield.get("sddcManagerVersion"), "snapshot manager version"), "SDDC Manager version");
        equal(bool(manager.get("useExistingDeployment"), "manager useExistingDeployment"), false,
                "greenfield SDDC Manager flag");

        Map<String, Object> datastore = object(sddc.get("datastoreSpec"), "datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "vsanSpec");
        equal(integer(vsan.get("failuresToTolerate"), "failuresToTolerate"),
                integer(greenfield.get("hostFailureTolerance"), "greenfield.hostFailureTolerance"),
                "vSAN host failures to tolerate");
        equal(bool(object(vsan.get("esaConfig"), "esaConfig").get("enabled"), "esa enabled"), true,
                "vSAN ESA enabled");

        Map<String, Map<String, Object>> expectedNetworks = byKey(
                array(requirements.get("networks"), "requirements.networks"), "networkType");
        Map<String, Map<String, Object>> actualNetworks = byKey(array(sddc.get("networkSpecs"), "networkSpecs"),
                "networkType");
        Set<String> requiredTypes = stringSet(array(greenfield.get("requiredNetworkTypes"),
                "greenfield.requiredNetworkTypes"));
        equal(actualNetworks.keySet(), requiredTypes, "SDDC network types");
        for (String type : requiredTypes) {
            Map<String, Object> expected = expectedNetworks.get(type);
            Map<String, Object> actual = actualNetworks.get(type);
            check(expected != null && actual != null, "missing network " + type);
            for (String key : List.of("vlanId", "subnet", "gateway", "subnetMask", "mtu")) {
                equal(actual.get(key), expected.get(key), type + " " + key);
            }
            List<Object> ranges = array(actual.get("includeIpAddressRanges"), type + " ranges");
            check(ranges.size() == 1, type + " must have one allocation range");
            Map<String, Object> range = object(ranges.get(0), type + " range");
            equal(range.get("startIpAddress"), expected.get("rangeStart"), type + " range start");
            equal(range.get("endIpAddress"), expected.get("rangeEnd"), type + " range end");
        }

        Map<String, Object> dns = object(sddc.get("dnsSpec"), "dnsSpec");
        equal(string(dns.get("subdomain"), "dns subdomain"), string(site.get("dnsSubdomain"), "site dns"),
                "DNS subdomain");
        equal(dns.get("nameservers"), object(requirements.get("dns"), "requirements.dns").get("nameservers"),
                "DNS servers");
        equal(sddc.get("ntpServers"), requirements.get("ntpServers"), "NTP servers");

        List<Object> dvsSpecs = array(sddc.get("dvsSpecs"), "dvsSpecs");
        check(dvsSpecs.size() == 1, "design must contain one consolidated distributed switch");
        Map<String, Object> dvs = object(dvsSpecs.get(0), "dvsSpecs[0]");
        Map<String, Object> expectedDvs = object(requirements.get("distributedSwitch"), "distributedSwitch");
        equal(dvs.get("dvsName"), expectedDvs.get("name"), "distributed switch name");
        equal(dvs.get("mtu"), expectedDvs.get("mtu"), "distributed switch MTU");
        List<Object> dvsNetworks = array(dvs.get("networks"), "distributed switch networks");
        equal(stringSet(dvsNetworks), requiredTypes, "distributed switch networks");
        check(dvsNetworks.size() == requiredTypes.size(), "distributed switch networks must be unique");
        equal(byKey(array(dvs.get("vmnicsToUplinks"), "distributed switch uplinks"), "id"),
                byKey(array(expectedDvs.get("uplinks"), "fixture distributed switch uplinks"), "id"),
                "dual uplinks");

        Map<String, Object> nsx = object(sddc.get("nsxtSpec"), "nsxtSpec");
        equal(string(nsx.get("version"), "NSX version"), string(greenfield.get("nsxVersion"), "snapshot NSX"),
                "NSX version");
        equal(nsx.get("vipFqdn"), appliances.get("nsxVipFqdn"), "NSX VIP");
        equal(bool(nsx.get("useExistingDeployment"), "NSX useExistingDeployment"), false,
                "greenfield NSX flag");
        List<Object> nsxManagers = array(nsx.get("nsxtManagers"), "nsxtManagers");
        check(nsxManagers.size() == integer(greenfield.get("nsxManagerCount"), "nsxManagerCount"),
                "NSX manager count");
        List<String> actualManagerNames = new ArrayList<>();
        for (Object value : nsxManagers) {
            actualManagerNames.add(string(object(value, "nsxtManager").get("hostname"), "NSX manager hostname"));
        }
        equal(new LinkedHashSet<>(actualManagerNames),
                stringSet(array(appliances.get("nsxManagerHostnames"), "fixture NSX managers")),
                "NSX manager hostnames");
        Map<String, Object> overlay = object(requirements.get("overlay"), "overlay");
        equal(nsx.get("transportVlanId"), overlay.get("vlanId"), "overlay VLAN");
        Map<String, Object> pool = object(nsx.get("ipAddressPoolSpec"), "ipAddressPoolSpec");
        equal(pool.get("name"), overlay.get("poolName"), "overlay pool name");
        List<Object> subnets = array(pool.get("subnets"), "overlay subnets");
        check(subnets.size() == 1, "overlay pool must contain one subnet");
        Map<String, Object> subnet = object(subnets.get(0), "overlay subnet");
        equal(subnet.get("cidr"), overlay.get("cidr"), "overlay CIDR");
        equal(subnet.get("gateway"), overlay.get("gateway"), "overlay gateway");
        List<Object> poolRanges = array(subnet.get("ipAddressPoolRanges"), "overlay ranges");
        check(poolRanges.size() == 1, "overlay subnet must contain one range");
        Map<String, Object> poolRange = object(poolRanges.get(0), "overlay range");
        equal(poolRange.get("start"), overlay.get("rangeStart"), "overlay start");
        equal(poolRange.get("end"), overlay.get("rangeEnd"), "overlay end");

    }

    private static void verifyMigration(Map<String, Object> plan,
                                        Map<String, Object> inventory,
                                        Map<String, Object> snapshot) {
        equal(plan.get("estateId"), inventory.get("estateId"), "estate id");
        equal(plan.get("targetRelease"), snapshot.get("targetRelease"), "migration target release");

        Map<String, Map<String, Object>> inventoryByComponent = byKey(
                array(inventory.get("components"), "inventory.components"), "component");
        Map<String, Map<String, Object>> paths = byKey(array(snapshot.get("upgradePaths"), "upgradePaths"),
                "component");
        List<Object> steps = array(plan.get("steps"), "steps");
        check(steps.size() == inventoryByComponent.size(), "migration must contain every inventory component once");

        Set<String> seen = new LinkedHashSet<>();
        Map<String, Integer> orderByComponent = new LinkedHashMap<>();
        int previousRank = Integer.MIN_VALUE;
        int previousOrder = 0;
        for (int index = 0; index < steps.size(); index++) {
            Map<String, Object> step = object(steps.get(index), "steps[" + index + "]");
            int order = integer(step.get("order"), "step.order");
            check(order > previousOrder, "step order values must increase with array order");
            previousOrder = order;
            String component = string(step.get("component"), "step.component");
            check(seen.add(component), "duplicate migration component " + component);
            Map<String, Object> current = inventoryByComponent.get(component);
            Map<String, Object> path = paths.get(component);
            check(current != null, "migration contains component absent from inventory: " + component);
            check(path != null, "snapshot has no upgrade path for " + component);
            equal(step.get("sourceVersion"), current.get("version"), component + " source version");
            check(array(path.get("allowedSourceVersions"), component + " allowed sources")
                            .contains(step.get("sourceVersion")),
                    component + " source version is unsupported by snapshot");
            equal(step.get("targetComponent"), path.get("targetComponent"), component + " target component");
            equal(step.get("targetVersion"), path.get("targetVersion"), component + " target version");
            equal(step.get("action"), path.get("action"), component + " action");
            equal(stringSet(array(step.get("gates"), component + " gates")),
                    stringSet(array(path.get("gates"), component + " snapshot gates")), component + " gates");
            int rank = integer(path.get("rank"), component + " rank");
            check(rank > previousRank, "migration steps do not follow snapshot ordering ranks");
            previousRank = rank;
            orderByComponent.put(component, order);
        }
        equal(seen, inventoryByComponent.keySet(), "migration component set");

        for (Object value : steps) {
            Map<String, Object> step = object(value, "step");
            String component = string(step.get("component"), "step.component");
            for (String gate : stringSet(array(step.get("gates"), component + " gates"))) {
                Integer gateOrder = orderByComponent.get(gate);
                check(gateOrder != null, component + " names a gate missing from the estate plan: " + gate);
                check(gateOrder < orderByComponent.get(component),
                        gate + " must precede gated component " + component);
            }
        }

        Map<String, Object> combination = object(snapshot.get("supportedTargetCombination"),
                "supportedTargetCombination");
        for (Object value : steps) {
            Map<String, Object> step = object(value, "step");
            String component = string(step.get("component"), "step.component");
            equal(step.get("targetVersion"), combination.get(component), component + " target combination");
        }
    }

    private static void verifyResearchLog(List<Object> log) {
        check(log.size() >= 2, "research log must contain the live compatibility and upgrade-guidance sources");
        for (int index = 0; index < log.size(); index++) {
            Map<String, Object> source = object(log.get(index), "research-log[" + index + "]");
            String title = string(source.get("title"), "research title");
            String url = string(source.get("url"), "research URL");
            String accessedOn = string(source.get("accessedOn"), "research access date");
            String finding = string(source.get("finding"), "research finding");
            check(!title.isBlank(), "research title must not be blank");
            check(!accessedOn.isBlank(), "research access date must not be blank");
            check(!finding.isBlank(), "research finding must not be blank");

            URI parsed;
            try {
                parsed = URI.create(url);
            } catch (IllegalArgumentException error) {
                throw new AssertionError("research URL must be a valid URI: " + url, error);
            }
            check(("https".equalsIgnoreCase(parsed.getScheme()) || "http".equalsIgnoreCase(parsed.getScheme()))
                            && parsed.getHost() != null,
                    "research URL must be an absolute HTTP(S) URL: " + url);
            check(!parsed.getHost().endsWith(".invalid"), "research URL must not use a reserved invalid host");
        }
    }

    private static Map<String, Map<String, Object>> byKey(List<Object> values, String key) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> entry = object(value, key + " entry");
            String id = string(entry.get(key), key);
            check(result.put(id, entry) == null, "duplicate " + key + ": " + id);
        }
        return result;
    }

    private static Object pointer(Object document, String reference) {
        check(reference.startsWith("#/"), "only local JSON pointers are supported: " + reference);
        Object current = document;
        for (String raw : reference.substring(2).split("/")) {
            String key = raw.replace("~1", "/").replace("~0", "~");
            current = object(current, "JSON pointer " + reference).get(key);
            check(current != null, "unresolved JSON pointer " + reference);
        }
        return current;
    }

    private static void deleteTree(Path root) throws IOException {
        if (!Files.exists(root)) {
            return;
        }
        try (var paths = Files.walk(root)) {
            for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String path) {
        check(value instanceof Map<?, ?>, path + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String path) {
        check(value instanceof List<?>, path + " must be an array");
        return (List<Object>) value;
    }

    private static String string(Object value, String path) {
        check(value instanceof String, path + " must be a string");
        return (String) value;
    }

    private static boolean bool(Object value, String path) {
        check(value instanceof Boolean, path + " must be a boolean");
        return (Boolean) value;
    }

    private static double number(Object value, String path) {
        check(value instanceof Number, path + " must be numeric");
        return ((Number) value).doubleValue();
    }

    private static int integer(Object value, String path) {
        check(value instanceof Number && Math.rint(((Number) value).doubleValue()) == ((Number) value).doubleValue(),
                path + " must be an integer");
        return ((Number) value).intValue();
    }

    private static Set<String> stringSet(List<Object> values) {
        return new LinkedHashSet<>(stringList(values));
    }

    private static List<String> stringList(List<Object> values) {
        List<String> result = new ArrayList<>();
        for (Object value : values) {
            result.add(string(value, "array value"));
        }
        return result;
    }

    private static void equal(Object actual, Object expected, String label) {
        check(actual == null ? expected == null : actual.equals(expected),
                label + " mismatch: expected " + expected + ", got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(Object instance, Object rawSchema, Object root, String path) {
            Map<String, Object> schema = object(rawSchema, path + " schema");
            if (schema.containsKey("$ref")) {
                validate(instance, pointer(root, string(schema.get("$ref"), path + " $ref")), root, path);
                return;
            }
            if (Boolean.TRUE.equals(schema.get("nullable")) && instance == null) {
                return;
            }
            validateCompositions(instance, schema, root, path);
            if (schema.containsKey("type")) {
                String type = string(schema.get("type"), path + " schema.type");
                check(typeMatches(instance, type), path + " must have JSON type " + type);
            }
            if (schema.containsKey("enum")) {
                check(array(schema.get("enum"), path + " enum").contains(instance), path + " is not an enum value");
            }
            if (instance instanceof Map<?, ?>) {
                validateObject(object(instance, path), schema, root, path);
            } else if (instance instanceof List<?>) {
                validateArray(array(instance, path), schema, root, path);
            } else if (instance instanceof String text) {
                if (schema.containsKey("minLength")) {
                    check(text.length() >= integer(schema.get("minLength"), path + " minLength"),
                            path + " is shorter than minLength");
                }
                if (schema.containsKey("maxLength")) {
                    check(text.length() <= integer(schema.get("maxLength"), path + " maxLength"),
                            path + " is longer than maxLength");
                }
                if (schema.containsKey("pattern")) {
                    check(Pattern.compile(string(schema.get("pattern"), path + " pattern")).matcher(text).find(),
                            path + " does not match schema pattern");
                }
            } else if (instance instanceof Number number) {
                if (schema.containsKey("minimum")) {
                    check(number.doubleValue() >= number(schema.get("minimum"), path + " minimum"),
                            path + " is below minimum");
                }
                if (schema.containsKey("maximum")) {
                    check(number.doubleValue() <= number(schema.get("maximum"), path + " maximum"),
                            path + " is above maximum");
                }
            }
        }

        private static void validateCompositions(Object instance, Map<String, Object> schema, Object root, String path) {
            if (schema.containsKey("allOf")) {
                for (Object child : array(schema.get("allOf"), path + " allOf")) {
                    validate(instance, child, root, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                int matches = matches(instance, array(schema.get("anyOf"), path + " anyOf"), root, path);
                check(matches >= 1, path + " does not match anyOf");
            }
            if (schema.containsKey("oneOf")) {
                int matches = matches(instance, array(schema.get("oneOf"), path + " oneOf"), root, path);
                check(matches == 1, path + " must match exactly one oneOf branch");
            }
        }

        private static int matches(Object instance, List<Object> schemas, Object root, String path) {
            int count = 0;
            for (Object child : schemas) {
                try {
                    validate(instance, child, root, path);
                    count++;
                } catch (AssertionError ignored) {
                    // A composition branch is allowed to fail.
                }
            }
            return count;
        }

        private static void validateObject(Map<String, Object> instance, Map<String, Object> schema,
                                           Object root, String path) {
            if (schema.containsKey("required")) {
                for (String key : stringList(array(schema.get("required"), path + " required"))) {
                    check(instance.containsKey(key), path + " is missing required property " + key);
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), path + " properties") : Collections.emptyMap();
            for (Map.Entry<String, Object> entry : properties.entrySet()) {
                if (instance.containsKey(entry.getKey())) {
                    validate(instance.get(entry.getKey()), entry.getValue(), root, path + "." + entry.getKey());
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : instance.keySet()) {
                    check(properties.containsKey(key), path + " has forbidden property " + key);
                }
            }
        }

        private static void validateArray(List<Object> instance, Map<String, Object> schema,
                                          Object root, String path) {
            if (schema.containsKey("minItems")) {
                check(instance.size() >= integer(schema.get("minItems"), path + " minItems"),
                        path + " has too few items");
            }
            if (schema.containsKey("maxItems")) {
                check(instance.size() <= integer(schema.get("maxItems"), path + " maxItems"),
                        path + " has too many items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                check(new LinkedHashSet<>(instance).size() == instance.size(), path + " items must be unique");
            }
            if (schema.containsKey("items")) {
                for (int index = 0; index < instance.size(); index++) {
                    validate(instance.get(index), schema.get("items"), root, path + "[" + index + "]");
                }
            }
        }

        private static boolean typeMatches(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "boolean" -> value instanceof Boolean;
                case "number" -> value instanceof Number;
                case "integer" -> value instanceof Number
                        && Math.rint(((Number) value).doubleValue()) == ((Number) value).doubleValue();
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported JSON Schema type " + type);
            };
        }
    }

    private static final class Json {
        private final String input;
        private int offset;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            Json parser = new Json(input);
            Object value = parser.value();
            parser.space();
            check(parser.offset == input.length(), "trailing content in JSON at offset " + parser.offset);
            return value;
        }

        private Object value() {
            space();
            check(offset < input.length(), "unexpected end of JSON");
            return switch (input.charAt(offset)) {
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
            offset++;
            Map<String, Object> result = new LinkedHashMap<>();
            space();
            if (take('}')) {
                return result;
            }
            while (true) {
                space();
                check(offset < input.length() && input.charAt(offset) == '"', "object key must be a string");
                String key = stringValue();
                space();
                check(take(':'), "missing colon after object key");
                check(!result.containsKey(key), "duplicate JSON object key " + key);
                result.put(key, value());
                space();
                if (take('}')) {
                    return result;
                }
                check(take(','), "missing comma in object");
            }
        }

        private List<Object> arrayValue() {
            offset++;
            List<Object> result = new ArrayList<>();
            space();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                space();
                if (take(']')) {
                    return result;
                }
                check(take(','), "missing comma in array");
            }
        }

        private String stringValue() {
            check(take('"'), "expected string");
            StringBuilder result = new StringBuilder();
            while (offset < input.length()) {
                char value = input.charAt(offset++);
                if (value == '"') {
                    return result.toString();
                }
                if (value == '\\') {
                    check(offset < input.length(), "unterminated JSON escape");
                    char escaped = input.charAt(offset++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            check(offset + 4 <= input.length(), "short unicode escape");
                            result.append((char) Integer.parseInt(input.substring(offset, offset + 4), 16));
                            offset += 4;
                        }
                        default -> throw new AssertionError("invalid JSON escape \\" + escaped);
                    }
                } else {
                    check(value >= 0x20, "control character in JSON string");
                    result.append(value);
                }
            }
            throw new AssertionError("unterminated JSON string");
        }

        private Object numberValue() {
            int start = offset;
            if (take('-')) {
                // sign consumed
            }
            check(offset < input.length() && Character.isDigit(input.charAt(offset)), "invalid JSON number");
            if (take('0')) {
                // zero cannot have additional integer digits
            } else {
                while (offset < input.length() && Character.isDigit(input.charAt(offset))) {
                    offset++;
                }
            }
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                check(offset < input.length() && Character.isDigit(input.charAt(offset)), "invalid JSON fraction");
                while (offset < input.length() && Character.isDigit(input.charAt(offset))) {
                    offset++;
                }
            }
            if (offset < input.length() && (input.charAt(offset) == 'e' || input.charAt(offset) == 'E')) {
                decimal = true;
                offset++;
                if (offset < input.length() && (input.charAt(offset) == '+' || input.charAt(offset) == '-')) {
                    offset++;
                }
                check(offset < input.length() && Character.isDigit(input.charAt(offset)), "invalid JSON exponent");
                while (offset < input.length() && Character.isDigit(input.charAt(offset))) {
                    offset++;
                }
            }
            String token = input.substring(start, offset);
            try {
                return decimal ? Double.valueOf(token) : Long.valueOf(token);
            } catch (NumberFormatException error) {
                throw new AssertionError("invalid JSON number " + token, error);
            }
        }

        private Object literal(String token, Object value) {
            check(input.startsWith(token, offset), "invalid JSON literal at offset " + offset);
            offset += token.length();
            return value;
        }

        private void space() {
            while (offset < input.length() && Character.isWhitespace(input.charAt(offset))) {
                offset++;
            }
        }

        private boolean take(char value) {
            if (offset < input.length() && input.charAt(offset) == value) {
                offset++;
                return true;
            }
            return false;
        }
    }
}
