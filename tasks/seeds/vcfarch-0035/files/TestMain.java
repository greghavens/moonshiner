import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** Offline acceptance harness. No research source or network access is used here. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        Path root = Path.of("");

        // This is intentionally the first acceptance operation.  No scenario,
        // snapshot, migration, CLI, or research-ledger assertion precedes
        // validation through the installer's own SddcSpec schema.
        Object greenfield = Json.parse(ArchitectureClient.greenfield());
        Map<String, Object> openApi = object(Json.parse(Files.readString(
                root.resolve("specifications/vcf-installer/vcf-installer-openapi.json"))), "OpenAPI document");
        Object sddcSchema = pointer(openApi, "#/components/schemas/SddcSpec");
        new SchemaValidator(openApi).validate(greenfield, sddcSchema, "$");
        System.out.println("installer SddcSpec schema: ok");

        Map<String, Object> snapshot = object(Json.parse(Files.readString(
                root.resolve("fixtures/compatibility-snapshot.json"))), "compatibility snapshot");
        checkGreenfield(object(greenfield, "greenfield artifact"), snapshot);
        System.out.println("greenfield architecture: ok");

        String estateText = Files.readString(root.resolve("fixtures/estate.json"));
        Object migration = Json.parse(ArchitectureClient.migration(estateText));
        Map<String, Object> migrationSchema = object(Json.parse(Files.readString(
                root.resolve("fixtures/migration-plan-schema.json"))), "migration schema");
        new SchemaValidator(migrationSchema).validate(migration, migrationSchema, "$");
        checkMigration(object(migration, "migration artifact"),
                object(Json.parse(estateText), "estate inventory"), snapshot);

        String changedEstateText = estateText
                .replace("den01-vcf52-prod", "den01-vcf52-validation")
                .replace("VMware Aria Operations\"", "Northstar Operations Validation\"");
        Object changedMigration = Json.parse(ArchitectureClient.migration(changedEstateText));
        new SchemaValidator(migrationSchema).validate(changedMigration, migrationSchema, "$");
        checkMigration(object(changedMigration, "changed-inventory migration artifact"),
                object(Json.parse(changedEstateText), "changed estate inventory"), snapshot);
        System.out.println("migration architecture: ok");

        checkCli(greenfield, migration, estateText);
        System.out.println("client modes: ok");
        System.out.println("all checks passed");
    }

    private static void checkGreenfield(Map<String, Object> spec, Map<String, Object> snapshot) {
        Map<String, Object> expected = atObject(snapshot, "greenfield");
        eq(text(spec, "version"), text(expected, "targetVersion"), "SddcSpec target version");
        eq(text(spec, "workflowType"), text(expected, "workflowType"), "workflow type");
        eq(text(spec, "sddcId"), text(expected, "sddcId"), "SDDC id");
        eq(text(spec, "vcfInstanceName"), text(expected, "vcfInstanceName"), "VCF instance name");
        Map<String, Object> componentVersions = atObject(expected, "componentVersions");
        eq(text(atObject(spec, "sddcManagerSpec"), "version"),
                text(componentVersions, "sddcManager"), "supported SDDC Manager version");
        eq(text(atObject(spec, "vcenterSpec"), "version"),
                text(componentVersions, "vcenter"), "supported vCenter version");
        eq(text(atObject(spec, "nsxtSpec"), "version"),
                text(componentVersions, "nsx"), "supported NSX version");

        List<Object> hostSpecs = atList(spec, "hostSpecs");
        eq(hostSpecs.size(), integer(expected, "managementHostCount"), "management host count");
        Set<String> actualHosts = new LinkedHashSet<>();
        for (Object value : hostSpecs) actualHosts.add(text(object(value, "host spec"), "hostname"));
        eq(actualHosts, strings(atList(atObject(expected, "addressing"), "hostnames")), "management hostnames");

        Map<String, Object> cluster = atObject(spec, "clusterSpec");
        eq(text(cluster, "clusterName"), text(expected, "managementClusterName"), "management cluster name");
        Map<String, Object> vsan = atObject(atObject(spec, "datastoreSpec"), "vsanSpec");
        eq(integer(vsan, "failuresToTolerate"),
                integer(expected, "managementHostFailuresToTolerate"), "vSAN host failures to tolerate");
        check(bool(atObject(vsan, "esaConfig"), "enabled"), "greenfield storage must use vSAN ESA");

        Map<String, Object> addressing = atObject(expected, "addressing");
        Map<String, Object> dns = atObject(spec, "dnsSpec");
        eq(text(dns, "subdomain"), text(addressing, "dnsSubdomain"), "DNS subdomain");
        eq(strings(atList(dns, "nameservers")), strings(atList(addressing, "nameservers")), "DNS servers");
        eq(strings(atList(spec, "ntpServers")), strings(atList(addressing, "ntpServers")), "NTP servers");
        eq(text(atObject(spec, "vcenterSpec"), "vcenterHostname"),
                text(addressing, "vcenterHostname"), "vCenter hostname");
        eq(text(atObject(spec, "sddcManagerSpec"), "hostname"),
                text(addressing, "sddcManagerHostname"), "SDDC Manager hostname");
        eq(text(atObject(spec, "nsxtSpec"), "vipFqdn"),
                text(addressing, "nsxVipFqdn"), "NSX VIP");
        eq(integer(atObject(spec, "nsxtSpec"), "transportVlanId"), 150, "NSX transport VLAN");

        Map<String, Object> networking = atObject(expected, "networking");
        Map<String, Object> expectedVlans = atObject(networking, "vlans");
        Map<String, Map<String, Object>> networkByType = index(atList(spec, "networkSpecs"), "networkType");
        eq(networkByType.keySet(), expectedVlans.keySet(), "installer network types");
        for (Map.Entry<String, Object> entry : expectedVlans.entrySet()) {
            Map<String, Object> network = networkByType.get(entry.getKey());
            eq(integer(network, "vlanId"), number(entry.getValue(), "VLAN " + entry.getKey()).intValueExact(),
                    entry.getKey() + " VLAN");
            eq(integer(network, "mtu"), integer(expected, "requiredMtu"), entry.getKey() + " MTU");
            eq(text(network, "teamingPolicy"), "loadbalance_srcid", entry.getKey() + " teaming policy");
            eq(new LinkedHashSet<>(strings(atList(network, "activeUplinks"))),
                    Set.of("uplink1", "uplink2"), entry.getKey() + " dual active uplinks");
        }

        Map<String, Map<String, Object>> vdsByName = index(atList(spec, "dvsSpecs"), "dvsName");
        Set<String> expectedVdsNames = Set.of(text(networking, "infrastructureVds"), text(networking, "edgeVds"));
        eq(vdsByName.keySet(), expectedVdsNames, "separate infrastructure and Edge VDSes");
        for (Map<String, Object> vds : vdsByName.values()) {
            eq(integer(vds, "mtu"), integer(expected, "requiredMtu"), text(vds, "dvsName") + " MTU");
            List<Object> teamings = atList(vds, "nsxTeamings");
            eq(teamings.size(), 1, text(vds, "dvsName") + " teaming count");
            Map<String, Object> teaming = object(teamings.get(0), "NSX teaming");
            eq(text(teaming, "policy"), "LOADBALANCE_SRCID", "dual-active teaming policy");
            eq(new LinkedHashSet<>(strings(atList(teaming, "activeUplinks"))),
                    Set.of("uplink1", "uplink2"), "dual active uplinks");
            eq(atList(teaming, "standByUplinks").size(), 0, "dual-active has no standby uplink");
        }
        eq(new LinkedHashSet<>(strings(atList(require(vdsByName,
                        text(networking, "infrastructureVds"), "infrastructure VDS"), "networks"))),
                Set.of("MANAGEMENT", "VMOTION", "VSAN"), "infrastructure VDS traffic types");
        eq(new LinkedHashSet<>(strings(atList(require(vdsByName,
                        text(networking, "edgeVds"), "Edge VDS"), "networks"))),
                Set.of("VM_MANAGEMENT", "EDGE_TEP", "EDGE_UPLINK_A", "EDGE_UPLINK_B"),
                "Edge VDS traffic types");

        Map<String, Object> architecture = atObject(spec, "xArchitecture");
        eq(text(architecture, "scenarioId"), text(expected, "scenarioId"), "scenario id");
        Map<String, Object> capacity = atObject(architecture, "capacity");
        eq(integer(capacity, "managementHosts"), integer(expected, "managementHostCount"),
                "management host capacity");
        eq(integer(capacity, "managementVmCapacity"),
                integer(expected, "managementVmCapacity"), "management VM capacity");
        checkResearch(atList(architecture, "researchConsulted"), "greenfield");

        Map<String, Map<String, Object>> sites = index(atList(architecture, "sites"), "siteId");
        List<Object> expectedSites = atList(expected, "sites");
        eq(sites.size(), expectedSites.size(), "site count");
        for (Object value : expectedSites) {
            Map<String, Object> wanted = object(value, "snapshot site");
            Map<String, Object> actual = require(sites, text(wanted, "siteId"), "site");
            for (String field : List.of("role", "replication"))
                eq(text(actual, field), text(wanted, field), text(wanted, "siteId") + " " + field);
            eq(bool(actual, "managementCluster"), bool(wanted, "managementCluster"),
                    text(wanted, "siteId") + " management-cluster placement");
            eq(integer(actual, "recoveryCapacityPercent"), integer(wanted, "recoveryCapacityPercent"),
                    text(wanted, "siteId") + " recovery capacity");
        }

        Map<String, Object> availability = atObject(architecture, "availability");
        eq(integer(availability, "managementHostFailuresToTolerate"),
                integer(expected, "managementHostFailuresToTolerate"), "management host availability");
        eq(integer(availability, "edgeNodeFailuresToTolerate"),
                integer(expected, "edgeNodeFailuresToTolerate"), "Edge-node availability");
        eq(integer(availability, "torFailuresToTolerate"), 1, "ToR availability");

        int requiredGbps = integer(expected, "requiredSurvivingNorthSouthGbps");
        Map<String, Object> minimumProfile = null;
        for (Object value : atList(expected, "edgeProfiles")) {
            Map<String, Object> candidate = object(value, "Edge profile");
            if (integer(candidate, "supportedStatefulThroughputGbps") >= requiredGbps
                    && (minimumProfile == null || integer(candidate, "rank") < integer(minimumProfile, "rank"))) {
                minimumProfile = candidate;
            }
        }
        check(minimumProfile != null, "snapshot has no Edge profile satisfying required throughput");
        Map<String, Object> edge = atObject(architecture, "edgeCluster");
        eq(integer(edge, "requiredSurvivingThroughputGbps"), requiredGbps, "required Edge throughput");
        eq(text(edge, "formFactor"), text(minimumProfile, "formFactor"),
                "smallest supported Edge form factor satisfying surviving throughput");
        eq(integer(edge, "vCpu"), integer(minimumProfile, "vCpu"), "Edge vCPU sizing");
        eq(integer(edge, "memoryGb"), integer(minimumProfile, "memoryGb"), "Edge memory sizing");
        eq(integer(edge, "perNodeSupportedThroughputGbps"),
                integer(minimumProfile, "supportedStatefulThroughputGbps"), "per-node Edge throughput");
        check(integer(edge, "perNodeSupportedThroughputGbps") >= requiredGbps,
                "one surviving Edge node cannot carry the stated throughput");
        eq(integer(edge, "nodeCount"), integer(expected, "edgeNodeCount"), "Edge node count");
        eq(text(edge, "haMode"), text(expected, "edgeHaMode"), "Edge HA mode");
        eq(text(edge, "placement"), text(expected, "edgePlacement"), "Edge placement");

        List<Object> expectedAssignments = atList(networking, "pnicAssignments");
        List<Object> actualAssignments = atList(architecture, "physicalUplinks");
        eq(actualAssignments.size(), expectedAssignments.size(), "physical uplink count");
        Map<String, Map<String, Object>> actualByDevice = index(actualAssignments, "device");
        Set<String> tors = new HashSet<>();
        for (Object value : expectedAssignments) {
            Map<String, Object> wanted = object(value, "pNIC assignment");
            String device = text(wanted, "device");
            Map<String, Object> actual = require(actualByDevice, device, "physical uplink");
            for (String field : List.of("vds", "uplink", "tor"))
                eq(text(actual, field), text(wanted, field), device + " " + field);
            eq(integer(actual, "speedGbps"), integer(expected, "requiredPnicSpeedGbps"), device + " speed");
            tors.add(text(actual, "tor"));

            Map<String, Object> installerVds = require(vdsByName, text(actual, "vds"), "installer VDS");
            Map<String, Map<String, Object>> vmnicMappings = index(atList(installerVds, "vmnicsToUplinks"), "id");
            eq(text(require(vmnicMappings, device, "installer vmnic mapping"), "uplink"),
                    text(actual, "uplink"), device + " installer/architecture uplink agreement");
        }
        eq(tors.size(), integer(expected, "requiredTorCount"), "uplinks span both ToR fault domains");
    }

    private static void checkMigration(Map<String, Object> plan, Map<String, Object> estate,
                                       Map<String, Object> snapshot) {
        Map<String, Object> authority = atObject(snapshot, "migration");
        eq(text(plan, "schemaVersion"), "1.0", "migration schema version");
        eq(text(plan, "estateId"), text(estate, "estateId"), "estate id");
        eq(text(plan, "sourceVcfVersion"), text(estate, "vcfVersion"), "source VCF version");
        eq(text(plan, "sourceVcfVersion"), text(authority, "sourceVcfVersion"), "supported source VCF version");
        eq(text(plan, "targetVcfVersion"), text(authority, "targetVcfVersion"), "target VCF version");
        checkResearch(atList(plan, "researchConsulted"), "migration");

        Map<String, Map<String, Object>> inventory = index(atList(estate, "components"), "id");
        Map<String, Map<String, Object>> rules = index(atList(authority, "components"), "id");
        List<Object> steps = atList(plan, "steps");
        eq(steps.size(), inventory.size(), "one migration step per estate component");
        eq(rules.keySet(), inventory.keySet(), "snapshot covers every inventory component");

        Map<String, Map<String, Object>> stepById = index(steps, "componentId");
        eq(stepById.keySet(), inventory.keySet(), "migration component coverage");
        Map<String, Integer> orderById = new LinkedHashMap<>();
        Set<Integer> orders = new HashSet<>();
        int expectedOrder = 1;
        for (Object value : steps) {
            Map<String, Object> step = object(value, "migration step");
            String id = text(step, "componentId");
            int order = integer(step, "order");
            eq(order, expectedOrder++, "steps array order");
            check(orders.add(order), "duplicate migration order " + order);
            orderById.put(id, order);

            Map<String, Object> installed = require(inventory, id, "inventory component");
            Map<String, Object> rule = require(rules, id, "compatibility rule");
            eq(text(step, "component"), text(installed, "name"), id + " component name");
            eq(text(step, "currentVersion"), text(installed, "version"), id + " installed version");
            check(strings(atList(rule, "supportedSources")).contains(text(step, "currentVersion")),
                    id + " current version is not a supported source");
            eq(text(step, "target"), text(rule, "target"), id + " target");
            eq(text(step, "action"), text(rule, "action"), id + " action");
            eq(new LinkedHashSet<>(strings(atList(step, "gates"))),
                    new LinkedHashSet<>(strings(atList(rule, "gates"))), id + " gates");
            eq(new LinkedHashSet<>(strings(atList(step, "after"))),
                    new LinkedHashSet<>(strings(atList(rule, "mustFollow"))), id + " dependencies");
        }
        for (int i = 1; i <= steps.size(); i++) check(orders.contains(i), "migration orders must be contiguous at " + i);
        for (Map.Entry<String, Map<String, Object>> entry : rules.entrySet()) {
            for (String predecessor : strings(atList(entry.getValue(), "mustFollow"))) {
                check(orderById.get(predecessor) < orderById.get(entry.getKey()),
                        entry.getKey() + " must follow " + predecessor);
            }
        }
    }

    private static void checkResearch(List<Object> entries, String artifact) {
        check(entries.size() >= 2, artifact + " must record multiple research sources");
        Set<String> urls = new HashSet<>();
        for (Object value : entries) {
            Map<String, Object> entry = object(value, artifact + " research entry");
            check(!text(entry, "title").isBlank(), artifact + " research title is blank");
            String url = text(entry, "url");
            check(url.startsWith("https://") && !url.contains(".invalid"),
                    artifact + " research URL must be a reachable HTTPS candidate");
            check(urls.add(url), artifact + " has duplicate research URL " + url);
            check(text(entry, "accessedOn").matches("[0-9]{4}-[0-9]{2}-[0-9]{2}"),
                    artifact + " research access date must be YYYY-MM-DD");
            check(!text(entry, "usedFor").isBlank(), artifact + " research purpose is blank");
        }
    }

    private static void checkCli(Object expectedGreenfield, Object expectedMigration, String estateText) throws Exception {
        PrintStream original = System.out;
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try {
            System.setOut(new PrintStream(bytes, true, StandardCharsets.UTF_8));
            ArchitectureClient.main(new String[]{"greenfield"});
        } finally {
            System.setOut(original);
        }
        eq(Json.parse(bytes.toString(StandardCharsets.UTF_8)), expectedGreenfield, "greenfield CLI artifact");

        Path inventoryCopy = Path.of(".test-estate-copy.json");
        Files.writeString(inventoryCopy, estateText);
        bytes.reset();
        try {
            System.setOut(new PrintStream(bytes, true, StandardCharsets.UTF_8));
            ArchitectureClient.main(new String[]{"migration", inventoryCopy.toString()});
        } finally {
            System.setOut(original);
            Files.deleteIfExists(inventoryCopy);
        }
        eq(Json.parse(bytes.toString(StandardCharsets.UTF_8)), expectedMigration, "migration CLI artifact");
    }

    private static Map<String, Map<String, Object>> index(List<Object> values, String field) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, "array item indexed by " + field);
            String key = text(item, field);
            check(result.put(key, item) == null, "duplicate " + field + " " + key);
        }
        return result;
    }

    private static Set<String> strings(List<Object> values) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            check(value instanceof String, "expected string array item, got " + describe(value));
            check(result.add((String) value), "duplicate string array item " + value);
        }
        return result;
    }

    private static Map<String, Object> atObject(Map<String, Object> parent, String field) {
        return object(parent.get(field), field);
    }

    private static List<Object> atList(Map<String, Object> parent, String field) {
        Object value = parent.get(field);
        check(value instanceof List<?>, "expected array at " + field + ", got " + describe(value));
        @SuppressWarnings("unchecked") List<Object> result = (List<Object>) value;
        return result;
    }

    private static String text(Map<String, Object> parent, String field) {
        Object value = parent.get(field);
        check(value instanceof String, "expected string at " + field + ", got " + describe(value));
        return (String) value;
    }

    private static int integer(Map<String, Object> parent, String field) {
        return number(parent.get(field), field).intValueExact();
    }

    private static boolean bool(Map<String, Object> parent, String field) {
        Object value = parent.get(field);
        check(value instanceof Boolean, "expected boolean at " + field + ", got " + describe(value));
        return (Boolean) value;
    }

    private static BigDecimal number(Object value, String where) {
        check(value instanceof Number, "expected number at " + where + ", got " + describe(value));
        return value instanceof BigDecimal decimal ? decimal : new BigDecimal(value.toString());
    }

    private static Map<String, Object> object(Object value, String where) {
        check(value instanceof Map<?, ?>, "expected object at " + where + ", got " + describe(value));
        @SuppressWarnings("unchecked") Map<String, Object> result = (Map<String, Object>) value;
        return result;
    }

    private static <T> T require(Map<String, T> values, String key, String what) {
        T value = values.get(key);
        check(value != null, "missing " + what + " " + key);
        return value;
    }

    private static Object pointer(Object document, String ref) {
        check(ref.startsWith("#/"), "only local JSON pointers are supported: " + ref);
        Object current = document;
        for (String raw : ref.substring(2).split("/")) {
            String token = raw.replace("~1", "/").replace("~0", "~");
            current = object(current, "JSON pointer " + ref).get(token);
            check(current != null, "unresolved JSON pointer " + ref);
        }
        return current;
    }

    private static void eq(Object actual, Object expected, String what) {
        if (!Objects.equals(actual, expected)) {
            throw new AssertionError(what + ": expected " + expected + ", got " + actual);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static String describe(Object value) {
        return value == null ? "missing/null" : value.getClass().getSimpleName() + " " + value;
    }

    /** The subset is not hand-authored: this walks the actual pinned schemas and refs. */
    private static final class SchemaValidator {
        private final Object document;

        SchemaValidator(Object document) {
            this.document = document;
        }

        void validate(Object instance, Object schemaValue, String path) {
            Map<String, Object> schema = object(schemaValue, "schema for " + path);
            if (schema.containsKey("$ref")) {
                validate(instance, pointer(document, text(schema, "$ref")), path);
            }
            if (schema.containsKey("allOf")) {
                for (Object child : atList(schema, "allOf")) validate(instance, child, path);
            }
            if (schema.containsKey("anyOf")) validateAlternatives(instance, atList(schema, "anyOf"), path, false);
            if (schema.containsKey("oneOf")) validateAlternatives(instance, atList(schema, "oneOf"), path, true);
            if (schema.containsKey("const") && !jsonEqual(instance, schema.get("const")))
                fail(path, "does not equal const " + schema.get("const"));
            if (schema.containsKey("enum")) {
                boolean found = false;
                for (Object allowed : atList(schema, "enum")) found |= jsonEqual(instance, allowed);
                if (!found) fail(path, "is not in enum " + schema.get("enum"));
            }
            if (instance == null && Boolean.TRUE.equals(schema.get("nullable"))) return;

            String type = schema.get("type") instanceof String s ? s : null;
            if (type != null && !hasType(instance, type)) fail(path, "expected " + type + ", got " + describe(instance));
            if ("object".equals(type)) validateObject(object(instance, path), schema, path);
            if ("array".equals(type)) validateArray(instance, schema, path);
            if ("string".equals(type)) validateString((String) instance, schema, path);
            if (("integer".equals(type) || "number".equals(type)) && instance instanceof Number)
                validateNumber(number(instance, path), schema, path);
        }

        private void validateObject(Map<String, Object> instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("required")) {
                for (Object key : atList(schema, "required")) {
                    String name = (String) key;
                    if (!instance.containsKey(name)) fail(path, "missing required property " + name);
                }
            }
            Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?>
                    ? object(schema.get("properties"), path + " properties") : Map.of();
            for (Map.Entry<String, Object> entry : instance.entrySet()) {
                if (properties.containsKey(entry.getKey())) {
                    validate(entry.getValue(), properties.get(entry.getKey()), path + "." + entry.getKey());
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    fail(path, "unexpected property " + entry.getKey());
                } else if (schema.get("additionalProperties") instanceof Map<?, ?> additional) {
                    validate(entry.getValue(), additional, path + "." + entry.getKey());
                }
            }
        }

        private void validateArray(Object value, Map<String, Object> schema, String path) {
            @SuppressWarnings("unchecked") List<Object> instance = (List<Object>) value;
            if (schema.containsKey("minItems") && instance.size() < integer(schema, "minItems"))
                fail(path, "has fewer than minItems");
            if (schema.containsKey("maxItems") && instance.size() > integer(schema, "maxItems"))
                fail(path, "has more than maxItems");
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                for (int i = 0; i < instance.size(); i++)
                    for (int j = i + 1; j < instance.size(); j++)
                        if (jsonEqual(instance.get(i), instance.get(j))) fail(path, "has duplicate items");
            }
            if (schema.containsKey("items")) {
                for (int i = 0; i < instance.size(); i++)
                    validate(instance.get(i), schema.get("items"), path + "[" + i + "]");
            }
        }

        private void validateString(String instance, Map<String, Object> schema, String path) {
            int length = instance.codePointCount(0, instance.length());
            if (schema.containsKey("minLength") && length < integer(schema, "minLength"))
                fail(path, "is shorter than minLength");
            if (schema.containsKey("maxLength") && length > integer(schema, "maxLength"))
                fail(path, "is longer than maxLength");
            if (schema.containsKey("pattern")
                    && !Pattern.compile(text(schema, "pattern")).matcher(instance).find())
                fail(path, "does not match pattern " + text(schema, "pattern"));
        }

        private void validateNumber(BigDecimal instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("minimum") && instance.compareTo(number(schema.get("minimum"), path)) < 0)
                fail(path, "is below minimum");
            if (schema.containsKey("maximum") && instance.compareTo(number(schema.get("maximum"), path)) > 0)
                fail(path, "is above maximum");
        }

        private void validateAlternatives(Object instance, List<Object> alternatives, String path, boolean exactlyOne) {
            int matches = 0;
            for (Object alternative : alternatives) {
                try {
                    validate(instance, alternative, path);
                    matches++;
                } catch (SchemaFailure ignored) {
                    // Count only schemas that validate completely.
                }
            }
            if ((exactlyOne && matches != 1) || (!exactlyOne && matches == 0))
                fail(path, exactlyOne ? "does not match exactly one oneOf branch" : "does not match any anyOf branch");
        }

        private static boolean hasType(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "boolean" -> value instanceof Boolean;
                case "integer" -> value instanceof Byte || value instanceof Short || value instanceof Integer
                        || value instanceof Long || value instanceof java.math.BigInteger
                        || value instanceof BigDecimal d && d.stripTrailingZeros().scale() <= 0;
                case "number" -> value instanceof Number;
                case "null" -> value == null;
                default -> true;
            };
        }

        private static boolean jsonEqual(Object left, Object right) {
            if (left instanceof Number && right instanceof Number)
                return new BigDecimal(left.toString()).compareTo(new BigDecimal(right.toString())) == 0;
            return Objects.equals(left, right);
        }

        private static void fail(String path, String message) {
            throw new SchemaFailure("schema validation failed at " + path + ": " + message);
        }
    }

    private static final class SchemaFailure extends AssertionError {
        SchemaFailure(String message) {
            super(message);
        }
    }

    /** Small strict JSON parser so the acceptance suite has no registry dependencies. */
    private static final class Json {
        static Object parse(String source) {
            Parser parser = new Parser(source);
            Object result = parser.value();
            parser.space();
            if (parser.index != source.length()) parser.error("trailing content");
            return result;
        }

        private static final class Parser {
            private final String source;
            private int index;

            Parser(String source) {
                this.source = Objects.requireNonNull(source, "JSON source");
            }

            Object value() {
                space();
                if (index >= source.length()) error("expected value");
                return switch (source.charAt(index)) {
                    case '{' -> objectValue();
                    case '[' -> arrayValue();
                    case '"' -> string();
                    case 't' -> literal("true", Boolean.TRUE);
                    case 'f' -> literal("false", Boolean.FALSE);
                    case 'n' -> literal("null", null);
                    default -> numberValue();
                };
            }

            Map<String, Object> objectValue() {
                LinkedHashMap<String, Object> result = new LinkedHashMap<>();
                take('{');
                space();
                if (peek('}')) { index++; return result; }
                while (true) {
                    space();
                    if (!peek('"')) error("expected object key");
                    String key = string();
                    if (result.containsKey(key)) error("duplicate object key " + key);
                    space();
                    take(':');
                    result.put(key, value());
                    space();
                    if (peek('}')) { index++; return result; }
                    take(',');
                }
            }

            List<Object> arrayValue() {
                ArrayList<Object> result = new ArrayList<>();
                take('[');
                space();
                if (peek(']')) { index++; return result; }
                while (true) {
                    result.add(value());
                    space();
                    if (peek(']')) { index++; return result; }
                    take(',');
                }
            }

            String string() {
                take('"');
                StringBuilder result = new StringBuilder();
                while (index < source.length()) {
                    char c = source.charAt(index++);
                    if (c == '"') return result.toString();
                    if (c == '\\') {
                        if (index >= source.length()) error("unterminated escape");
                        char escape = source.charAt(index++);
                        switch (escape) {
                            case '"', '\\', '/' -> result.append(escape);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> {
                                if (index + 4 > source.length()) error("short unicode escape");
                                try {
                                    result.append((char) Integer.parseInt(source.substring(index, index + 4), 16));
                                } catch (NumberFormatException e) {
                                    error("bad unicode escape");
                                }
                                index += 4;
                            }
                            default -> error("bad escape " + escape);
                        }
                    } else {
                        if (c < 0x20) error("control character in string");
                        result.append(c);
                    }
                }
                error("unterminated string");
                return null;
            }

            Object numberValue() {
                int start = index;
                if (peek('-')) index++;
                digits();
                boolean decimal = false;
                if (peek('.')) { decimal = true; index++; digits(); }
                if (peek('e') || peek('E')) {
                    decimal = true;
                    index++;
                    if (peek('+') || peek('-')) index++;
                    digits();
                }
                try {
                    String token = source.substring(start, index);
                    return decimal ? new BigDecimal(token) : Long.valueOf(token);
                } catch (NumberFormatException e) {
                    error("bad number");
                    return null;
                }
            }

            Object literal(String token, Object value) {
                if (!source.startsWith(token, index)) error("expected " + token);
                index += token.length();
                return value;
            }

            void digits() {
                int start = index;
                while (index < source.length() && Character.isDigit(source.charAt(index))) index++;
                if (start == index) error("expected digit");
            }

            void space() {
                while (index < source.length() && Character.isWhitespace(source.charAt(index))) index++;
            }

            boolean peek(char c) {
                return index < source.length() && source.charAt(index) == c;
            }

            void take(char c) {
                if (!peek(c)) error("expected '" + c + "'");
                index++;
            }

            void error(String message) {
                throw new AssertionError("invalid JSON at character " + index + ": " + message);
            }
        }
    }
}
