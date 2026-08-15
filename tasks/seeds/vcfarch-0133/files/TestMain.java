import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");
    private static final Path PLAN_SCHEMA = Path.of("migration-plan-schema.json");
    private static final Path INSTALLER_SPEC =
            Path.of("specifications/vcf-installer/vcf-installer-openapi.json");
    private static final Path RESEARCH = Path.of("RESEARCH.md");
    private static final Pattern WEB_URL = Pattern.compile("https?://[^\\s<>|)\\]]+");
    private static final Pattern ISO_DATE = Pattern.compile("\\b\\d{4}-\\d{2}-\\d{2}\\b");

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        Object inventory = Json.parse(Files.readString(INVENTORY, StandardCharsets.UTF_8));
        Object snapshot = Json.parse(Files.readString(SNAPSHOT, StandardCharsets.UTF_8));
        Object planSchema = Json.parse(Files.readString(PLAN_SCHEMA, StandardCharsets.UTF_8));
        Object installerDocument = Json.parse(Files.readString(INSTALLER_SPEC, StandardCharsets.UTF_8));

        String artifactText = invokeClient();
        Object artifact = Json.parse(artifactText);

        // This is deliberately the first artifact check. Even a plan that fails every
        // migration rule must first be rejected through the tagged installer's SddcSpec.
        Object targetSddcSpec = rawTargetSddcSpec(artifact);
        Map<String, Object> installerRoot = object(installerDocument, "installer document");
        Object sddcSchema = object(object(object(installerRoot.get("components"),
                "installer components").get("schemas"), "installer schemas").get("SddcSpec"),
                "installer SddcSpec schema");
        List<String> installerErrors = new SchemaValidator(installerRoot)
                .validate(targetSddcSpec, sddcSchema);
        if (!installerErrors.isEmpty()) {
            fail("installer SddcSpec validation failed: " + summarize(installerErrors));
        }

        List<String> planErrors = new SchemaValidator(object(planSchema, "plan schema"))
                .validate(artifact, planSchema);
        if (!planErrors.isEmpty()) {
            fail("migration-plan schema validation failed: " + summarize(planErrors));
        }

        Map<String, Object> plan = object(artifact, "migration plan");
        verifyInstallerIdentity(installerRoot, object(snapshot, "compatibility snapshot"));
        verifyTargetSddcSpec(plan, object(inventory, "estate inventory"));
        verifyMigrationPlan(plan, object(inventory, "estate inventory"),
                object(snapshot, "compatibility snapshot"));
        verifyResearchRecord();

        System.out.println("PASS: installer schema, migration schema, inventory, research record, and pinned compatibility snapshot");
    }

    private static String invokeClient() throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        PrintStream original = System.out;
        try (PrintStream captured = new PrintStream(bytes, true, StandardCharsets.UTF_8)) {
            System.setOut(captured);
            FleetArchitecture.main(new String[]{INVENTORY.toString()});
        } finally {
            System.setOut(original);
        }
        String output = bytes.toString(StandardCharsets.UTF_8).trim();
        if (output.isEmpty()) {
            fail("FleetArchitecture produced no JSON artifact");
        }
        return output;
    }

    private static Object rawTargetSddcSpec(Object artifact) {
        if (!(artifact instanceof Map<?, ?>)) {
            fail("installer SddcSpec validation failed: artifact root is not an object");
        }
        Map<?, ?> raw = (Map<?, ?>) artifact;
        Object target = raw.get("targetSddcSpec");
        if (target == null) {
            fail("installer SddcSpec validation failed: $.targetSddcSpec is missing");
        }
        return target;
    }

    private static void verifyInstallerIdentity(Map<String, Object> installer,
                                                Map<String, Object> snapshot) {
        Map<String, Object> info = object(installer.get("info"), "installer info");
        Map<String, Object> pinned = object(snapshot.get("installerSchema"),
                "snapshot installerSchema");
        equal("9.1.0.0", text(info.get("version"), "installer info.version"),
                "installer spec version");
        equal("9.1.0.0", text(pinned.get("tag"), "snapshot installer tag"),
                "snapshot installer tag");
        equal("specifications/vcf-installer/vcf-installer-openapi.json",
                text(pinned.get("path"), "snapshot installer path"),
                "snapshot installer path");
        equal("29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
                text(pinned.get("sha256"), "snapshot installer sha256"),
                "snapshot upstream installer sha256");
    }

    private static void verifyTargetSddcSpec(Map<String, Object> plan,
                                             Map<String, Object> inventory) {
        Map<String, Object> target = object(inventory.get("targetManagementDomain"),
                "inventory targetManagementDomain");
        Map<String, Object> spec = object(plan.get("targetSddcSpec"), "targetSddcSpec");
        String targetVersion = text(inventory.get("targetVcfVersion"), "inventory target version");

        sameField(target, "sddcId", spec, "sddcId");
        sameField(target, "workflowType", spec, "workflowType");
        equal(targetVersion, text(spec.get("version"), "targetSddcSpec.version"),
                "targetSddcSpec.version");

        Map<String, Object> vcenter = object(spec.get("vcenterSpec"), "target vcenterSpec");
        equal(text(target.get("vcenterHostname"), "target vCenter hostname"),
                text(vcenter.get("vcenterHostname"), "vcenterSpec.vcenterHostname"),
                "vCenter hostname");
        equal(text(target.get("ssoDomain"), "target SSO domain"),
                text(vcenter.get("ssoDomain"), "vcenterSpec.ssoDomain"), "SSO domain");
        equal(targetVersion, text(vcenter.get("version"), "vcenterSpec.version"),
                "vCenter target version");
        equal(Boolean.FALSE, vcenter.get("useExistingDeployment"),
                "vCenter must be a new management-domain deployment");

        Map<String, Object> cluster = object(spec.get("clusterSpec"), "target clusterSpec");
        equal(target.get("datacenterName"), cluster.get("datacenterName"), "datacenter name");
        equal(target.get("clusterName"), cluster.get("clusterName"), "cluster name");

        List<Object> expectedHosts = array(target.get("hostnames"), "target hostnames");
        List<Object> hostSpecs = array(spec.get("hostSpecs"), "target hostSpecs");
        if (hostSpecs.size() != expectedHosts.size()) {
            fail("target hostSpecs count does not match inventory");
        }
        Set<String> actualHosts = new LinkedHashSet<>();
        for (Object item : hostSpecs) {
            actualHosts.add(text(object(item, "hostSpec").get("hostname"), "hostSpec.hostname"));
        }
        equal(stringSet(expectedHosts, "target hostnames"), actualHosts, "target hostnames");

        Map<String, Object> expectedDns = object(target.get("dns"), "target dns");
        Map<String, Object> dns = object(spec.get("dnsSpec"), "target dnsSpec");
        equal(expectedDns.get("subdomain"), dns.get("subdomain"), "DNS subdomain");
        deepEqual(expectedDns.get("nameservers"), dns.get("nameservers"), "DNS nameservers");
        deepEqual(target.get("ntpServers"), spec.get("ntpServers"), "NTP servers");

        List<Object> expectedNetworks = array(target.get("networks"), "target networks");
        List<Object> networks = array(spec.get("networkSpecs"), "target networkSpecs");
        if (networks.size() != expectedNetworks.size()) {
            fail("target networkSpecs count does not match inventory");
        }
        Map<String, Map<String, Object>> actualNetworks = indexBy(networks, "networkType",
                "target networkSpecs");
        for (Object expectedObject : expectedNetworks) {
            Map<String, Object> expected = object(expectedObject, "inventory network");
            String type = text(expected.get("networkType"), "inventory networkType");
            Map<String, Object> actual = actualNetworks.get(type);
            if (actual == null) fail("missing target network " + type);
            for (String key : List.of("networkType", "vlanId", "mtu", "subnet", "gateway")) {
                deepEqual(expected.get(key), actual.get(key), "network " + type + "." + key);
            }
        }

        Map<String, Object> expectedNsx = object(target.get("nsx"), "target nsx");
        Map<String, Object> nsx = object(spec.get("nsxtSpec"), "target nsxtSpec");
        equal(expectedNsx.get("vipFqdn"), nsx.get("vipFqdn"), "NSX VIP");
        deepEqual(expectedNsx.get("transportVlanId"), nsx.get("transportVlanId"),
                "NSX transport VLAN");
        equal(targetVersion, nsx.get("version"), "NSX target version");
        equal(Boolean.FALSE, nsx.get("useExistingDeployment"),
                "NSX must be a new management-domain deployment");
        Set<String> expectedManagers = stringSet(array(expectedNsx.get("managerHostnames"),
                "target NSX managers"), "target NSX managers");
        Set<String> actualManagers = new LinkedHashSet<>();
        for (Object manager : array(nsx.get("nsxtManagers"), "target nsxtManagers")) {
            actualManagers.add(text(object(manager, "NSX manager").get("hostname"),
                    "NSX manager hostname"));
        }
        equal(expectedManagers, actualManagers, "NSX manager hostnames");

        Map<String, Object> manager = object(spec.get("sddcManagerSpec"),
                "target sddcManagerSpec");
        equal(target.get("sddcManagerHostname"), manager.get("hostname"),
                "SDDC Manager hostname");
        equal(targetVersion, manager.get("version"), "SDDC Manager target version");
        equal(target.get("managementPoolName"), spec.get("managementPoolName"),
                "management pool name");
        equal(target.get("ceipEnabled"), spec.get("ceipEnabled"), "CEIP setting");

        Map<String, Object> datastore = object(spec.get("datastoreSpec"),
                "target datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "target vsanSpec");
        equal(target.get("vsanDatastoreName"), vsan.get("datastoreName"),
                "vSAN datastore name");
        deepEqual(target.get("vsanFailuresToTolerate"), vsan.get("failuresToTolerate"),
                "vSAN failuresToTolerate");
    }

    private static void verifyMigrationPlan(Map<String, Object> plan,
                                            Map<String, Object> inventory,
                                            Map<String, Object> snapshot) {
        equal("1.0", plan.get("schemaVersion"), "plan schemaVersion");
        equal(inventory.get("estateId"), plan.get("estateId"), "estateId");
        equal(inventory.get("targetVcfVersion"), plan.get("targetVcfVersion"),
                "targetVcfVersion");
        equal(snapshot.get("targetVcfVersion"), plan.get("targetVcfVersion"),
                "snapshot targetVcfVersion");

        List<Object> inventoryComponents = array(inventory.get("components"),
                "inventory components");
        List<Object> migrations = array(plan.get("componentMigrations"),
                "componentMigrations");
        if (migrations.size() != inventoryComponents.size()) {
            fail("componentMigrations must contain exactly one entry per inventory component");
        }

        Map<String, Object> rules = object(snapshot.get("componentRules"),
                "snapshot componentRules");
        Set<String> gateCatalog = stringSet(array(snapshot.get("gateCatalog"),
                "snapshot gateCatalog"), "snapshot gateCatalog");
        Map<String, Map<String, Object>> byId = indexBy(migrations, "componentId",
                "componentMigrations");
        Map<String, Integer> sequenceByProduct = new LinkedHashMap<>();
        Set<Integer> seenSequences = new HashSet<>();

        for (Object componentObject : inventoryComponents) {
            Map<String, Object> component = object(componentObject, "inventory component");
            String id = text(component.get("id"), "inventory component id");
            String product = text(component.get("product"), "inventory component product");
            Map<String, Object> migration = byId.get(id);
            if (migration == null) fail("missing migration for inventory component " + id);

            equal(product, migration.get("componentName"), id + " componentName");
            equal(component.get("version"), migration.get("currentVersion"),
                    id + " currentVersion");
            Map<String, Object> rule = object(rules.get(product), "snapshot rule for " + product);
            for (String field : List.of("targetProduct", "targetVersion", "disposition")) {
                equal(rule.get(field), migration.get(field), id + " " + field);
            }

            Object actualPath = migration.get("versionPath");
            boolean allowed = false;
            for (Object allowedPath : array(rule.get("allowedPaths"),
                    product + " allowedPaths")) {
                if (deepEquals(allowedPath, actualPath)) allowed = true;
            }
            if (!allowed) fail(id + " versionPath is not allowed by the pinned snapshot");

            Set<String> actualGates = stringSet(array(migration.get("gatedBy"),
                    id + " gatedBy"), id + " gatedBy");
            Set<String> requiredGates = stringSet(array(rule.get("requiredGates"),
                    product + " requiredGates"), product + " requiredGates");
            equal(requiredGates, actualGates, id + " compatibility gates");
            if (!gateCatalog.containsAll(actualGates)) {
                fail(id + " names a gate outside the pinned gate catalog");
            }

            int sequence = integer(migration.get("sequence"), id + " sequence");
            if (!seenSequences.add(sequence)) fail("duplicate migration sequence " + sequence);
            sequenceByProduct.put(product, sequence);
        }

        for (int expected = 1; expected <= migrations.size(); expected++) {
            if (!seenSequences.contains(expected)) {
                fail("migration sequences must be contiguous from 1 through " + migrations.size());
            }
        }
        for (Object orderingObject : array(snapshot.get("orderingRules"),
                "snapshot orderingRules")) {
            Map<String, Object> ordering = object(orderingObject, "ordering rule");
            String before = text(ordering.get("before"), "ordering before");
            String after = text(ordering.get("after"), "ordering after");
            if (sequenceByProduct.get(before) >= sequenceByProduct.get(after)) {
                fail("pinned ordering requires " + before + " before " + after);
            }
        }

        for (Object boundaryObject : array(snapshot.get("supportBoundaries"),
                "snapshot supportBoundaries")) {
            Map<String, Object> boundary = object(boundaryObject, "support boundary");
            String product = text(boundary.get("product"), "boundary product");
            Map<String, Object> migration = null;
            for (Object candidate : migrations) {
                Map<String, Object> item = object(candidate, "component migration");
                if (product.equals(item.get("componentName"))) migration = item;
            }
            if (migration == null) fail("support-boundary product missing from plan: " + product);
            equal(boundary.get("successorProduct"), migration.get("targetProduct"),
                    product + " successor product");
            List<Object> path = array(migration.get("versionPath"), product + " versionPath");
            if (!path.contains(boundary.get("bridgeVersion"))
                    || !path.contains(boundary.get("successorBridgeVersion"))) {
                fail(product + " path does not cross the pinned standalone support boundary");
            }
        }
    }

    private static void verifyResearchRecord() throws IOException {
        if (!Files.isRegularFile(RESEARCH)) {
            fail("RESEARCH.md is required");
        }
        String record = Files.readString(RESEARCH, StandardCharsets.UTF_8);
        if (record.length() < 200) {
            fail("RESEARCH.md must record source titles, URLs, access dates, and decisions");
        }

        Matcher dateMatcher = ISO_DATE.matcher(record);
        boolean validDate = false;
        while (dateMatcher.find()) {
            try {
                LocalDate.parse(dateMatcher.group());
                validDate = true;
            } catch (DateTimeParseException ignored) {
                // Continue looking for a valid recorded access date.
            }
        }
        if (!validDate) fail("RESEARCH.md must contain a valid ISO access date");

        Matcher urlMatcher = WEB_URL.matcher(record);
        Set<String> urls = new LinkedHashSet<>();
        boolean hasBroadcomSource = false;
        while (urlMatcher.find()) {
            String url = urlMatcher.group();
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException exception) {
                fail("RESEARCH.md contains an invalid source URL: " + url);
                return;
            }
            String host = uri.getHost();
            if (!"https".equalsIgnoreCase(uri.getScheme()) || host == null
                    || host.equalsIgnoreCase("localhost")
                    || host.endsWith(".invalid") || host.endsWith(".test")
                    || host.endsWith(".example")) {
                fail("RESEARCH.md source URLs must be real HTTPS locations: " + url);
            }
            if (host.equalsIgnoreCase("broadcom.com")
                    || host.toLowerCase().endsWith(".broadcom.com")) {
                hasBroadcomSource = true;
            }
            urls.add(url);
        }
        if (urls.isEmpty() || !hasBroadcomSource) {
            fail("RESEARCH.md must cite at least one HTTPS Broadcom-published source");
        }

        String lower = record.toLowerCase();
        for (String product : List.of("vcenter", "esxi", "vsan", "nsx",
                "live site recovery", "vsphere replication")) {
            if (!lower.contains(product)) {
                fail("RESEARCH.md does not record a decision for inventoried product " + product);
            }
        }
    }

    private static Map<String, Map<String, Object>> indexBy(List<Object> values, String key,
                                                             String label) {
        Map<String, Map<String, Object>> indexed = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, label + " item");
            String id = text(item.get(key), label + " " + key);
            if (indexed.put(id, item) != null) fail(label + " contains duplicate " + key + " " + id);
        }
        return indexed;
    }

    private static Set<String> stringSet(List<Object> values, String label) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            String item = text(value, label + " item");
            if (!result.add(item)) fail(label + " contains duplicate value " + item);
        }
        return result;
    }

    private static void sameField(Map<String, Object> left, String leftKey,
                                  Map<String, Object> right, String rightKey) {
        deepEqual(left.get(leftKey), right.get(rightKey), rightKey);
    }

    private static void deepEqual(Object expected, Object actual, String label) {
        if (!deepEquals(expected, actual)) {
            fail(label + " mismatch: expected " + expected + " but got " + actual);
        }
    }

    private static boolean deepEquals(Object left, Object right) {
        if (left instanceof BigDecimal a && right instanceof BigDecimal b) {
            return a.compareTo(b) == 0;
        }
        if (left instanceof List<?> a && right instanceof List<?> b) {
            if (a.size() != b.size()) return false;
            for (int i = 0; i < a.size(); i++) {
                if (!deepEquals(a.get(i), b.get(i))) return false;
            }
            return true;
        }
        if (left instanceof Map<?, ?> a && right instanceof Map<?, ?> b) {
            if (!a.keySet().equals(b.keySet())) return false;
            for (Object key : a.keySet()) {
                if (!deepEquals(a.get(key), b.get(key))) return false;
            }
            return true;
        }
        return left == null ? right == null : left.equals(right);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?>)) fail(label + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        if (!(value instanceof List<?>)) fail(label + " must be an array");
        return (List<Object>) value;
    }

    private static String text(Object value, String label) {
        if (!(value instanceof String)) fail(label + " must be a string");
        return (String) value;
    }

    private static int integer(Object value, String label) {
        if (!(value instanceof BigDecimal)) fail(label + " must be an integer");
        BigDecimal number = (BigDecimal) value;
        if (number.stripTrailingZeros().scale() > 0
                || number.compareTo(BigDecimal.valueOf(Integer.MIN_VALUE)) < 0
                || number.compareTo(BigDecimal.valueOf(Integer.MAX_VALUE)) > 0) {
            fail(label + " must be an integer");
        }
        return number.intValueExact();
    }

    private static void equal(Object expected, Object actual, String label) {
        if (!deepEquals(expected, actual)) {
            fail(label + " mismatch: expected " + expected + " but got " + actual);
        }
    }

    private static String summarize(List<String> errors) {
        int limit = Math.min(errors.size(), 8);
        return String.join("; ", errors.subList(0, limit))
                + (errors.size() > limit ? "; ..." : "");
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    private static final class SchemaValidator {
        private final Map<String, Object> root;

        private SchemaValidator(Map<String, Object> root) {
            this.root = root;
        }

        private List<String> validate(Object instance, Object schema) {
            List<String> errors = new ArrayList<>();
            validateAt(instance, schema, "$", errors);
            return errors;
        }

        private void validateAt(Object instance, Object schemaObject, String path,
                                List<String> errors) {
            if (schemaObject instanceof Boolean bool) {
                if (!bool) errors.add(path + " is rejected by a false schema");
                return;
            }
            if (!(schemaObject instanceof Map<?, ?>)) {
                errors.add(path + " has an invalid schema node");
                return;
            }
            Map<String, Object> schema = object(schemaObject, "schema at " + path);

            if (schema.containsKey("$ref")) {
                Object resolved = resolve(text(schema.get("$ref"), "$ref at " + path));
                validateAt(instance, resolved, path, errors);
                return;
            }
            if (instance == null && Boolean.TRUE.equals(schema.get("nullable"))) return;

            validateCombiners(instance, schema, path, errors);
            if (!matchesType(instance, schema.get("type"))) {
                errors.add(path + " must have type " + schema.get("type"));
                return;
            }
            if (schema.containsKey("const") && !deepEquals(schema.get("const"), instance)) {
                errors.add(path + " must equal const " + schema.get("const"));
            }
            if (schema.containsKey("enum")) {
                boolean found = false;
                for (Object choice : array(schema.get("enum"), "enum at " + path)) {
                    if (deepEquals(choice, instance)) found = true;
                }
                if (!found) errors.add(path + " is not one of the allowed enum values");
            }

            if (instance instanceof Map<?, ?>) validateObject(instance, schema, path, errors);
            if (instance instanceof List<?>) validateArray(instance, schema, path, errors);
            if (instance instanceof String) validateString(instance, schema, path, errors);
            if (instance instanceof BigDecimal) validateNumber(instance, schema, path, errors);
        }

        private void validateCombiners(Object instance, Map<String, Object> schema, String path,
                                       List<String> errors) {
            if (schema.containsKey("allOf")) {
                for (Object child : array(schema.get("allOf"), "allOf at " + path)) {
                    validateAt(instance, child, path, errors);
                }
            }
            if (schema.containsKey("anyOf")) {
                int matches = matchingSchemas(instance, schema.get("anyOf"), path);
                if (matches == 0) errors.add(path + " does not match any anyOf branch");
            }
            if (schema.containsKey("oneOf")) {
                int matches = matchingSchemas(instance, schema.get("oneOf"), path);
                if (matches != 1) errors.add(path + " must match exactly one oneOf branch");
            }
            if (schema.containsKey("not")) {
                List<String> nested = new ArrayList<>();
                validateAt(instance, schema.get("not"), path, nested);
                if (nested.isEmpty()) errors.add(path + " matches a prohibited schema");
            }
        }

        private int matchingSchemas(Object instance, Object schemasObject, String path) {
            int matches = 0;
            for (Object child : array(schemasObject, "schema branches at " + path)) {
                List<String> nested = new ArrayList<>();
                validateAt(instance, child, path, nested);
                if (nested.isEmpty()) matches++;
            }
            return matches;
        }

        private boolean matchesType(Object instance, Object typeObject) {
            if (typeObject == null) return true;
            if (typeObject instanceof List<?> types) {
                for (Object type : types) if (matchesType(instance, type)) return true;
                return false;
            }
            String type = text(typeObject, "schema type");
            return switch (type) {
                case "null" -> instance == null;
                case "object" -> instance instanceof Map<?, ?>;
                case "array" -> instance instanceof List<?>;
                case "string" -> instance instanceof String;
                case "boolean" -> instance instanceof Boolean;
                case "number" -> instance instanceof BigDecimal;
                case "integer" -> instance instanceof BigDecimal number
                        && number.stripTrailingZeros().scale() <= 0;
                default -> false;
            };
        }

        private void validateObject(Object instance, Map<String, Object> schema, String path,
                                    List<String> errors) {
            Map<String, Object> value = object(instance, path);
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), "properties at " + path)
                    : Map.of();
            if (schema.containsKey("required")) {
                for (Object nameObject : array(schema.get("required"), "required at " + path)) {
                    String name = text(nameObject, "required property at " + path);
                    if (!value.containsKey(name)) errors.add(path + "." + name + " is required");
                }
            }
            for (Map.Entry<String, Object> property : properties.entrySet()) {
                if (value.containsKey(property.getKey())) {
                    validateAt(value.get(property.getKey()), property.getValue(),
                            childPath(path, property.getKey()), errors);
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : value.keySet()) {
                    if (!properties.containsKey(key)) errors.add(childPath(path, key) + " is not allowed");
                }
            } else if (schema.get("additionalProperties") instanceof Map<?, ?> additional) {
                for (String key : value.keySet()) {
                    if (!properties.containsKey(key)) {
                        validateAt(value.get(key), additional, childPath(path, key), errors);
                    }
                }
            }
            checkCount(value.size(), schema.get("minProperties"), schema.get("maxProperties"),
                    path, "properties", errors);
        }

        private void validateArray(Object instance, Map<String, Object> schema, String path,
                                   List<String> errors) {
            List<Object> value = array(instance, path);
            if (schema.containsKey("items")) {
                for (int i = 0; i < value.size(); i++) {
                    validateAt(value.get(i), schema.get("items"), path + "[" + i + "]", errors);
                }
            }
            checkCount(value.size(), schema.get("minItems"), schema.get("maxItems"),
                    path, "items", errors);
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                for (int i = 0; i < value.size(); i++) {
                    for (int j = i + 1; j < value.size(); j++) {
                        if (deepEquals(value.get(i), value.get(j))) {
                            errors.add(path + " must contain unique items");
                            return;
                        }
                    }
                }
            }
        }

        private void validateString(Object instance, Map<String, Object> schema, String path,
                                    List<String> errors) {
            String value = (String) instance;
            int length = value.codePointCount(0, value.length());
            checkCount(length, schema.get("minLength"), schema.get("maxLength"),
                    path, "characters", errors);
            if (schema.containsKey("pattern")) {
                try {
                    Pattern pattern = Pattern.compile(text(schema.get("pattern"),
                            "pattern at " + path));
                    if (!pattern.matcher(value).find()) errors.add(path + " does not match its pattern");
                } catch (PatternSyntaxException exception) {
                    errors.add(path + " uses an unsupported schema pattern");
                }
            }
        }

        private void validateNumber(Object instance, Map<String, Object> schema, String path,
                                    List<String> errors) {
            BigDecimal value = (BigDecimal) instance;
            compareBound(value, schema.get("minimum"), false, true, path, errors);
            compareBound(value, schema.get("maximum"), false, false, path, errors);
            Object exclusiveMinimum = schema.get("exclusiveMinimum");
            if (exclusiveMinimum instanceof BigDecimal) {
                compareBound(value, exclusiveMinimum, true, true, path, errors);
            }
            Object exclusiveMaximum = schema.get("exclusiveMaximum");
            if (exclusiveMaximum instanceof BigDecimal) {
                compareBound(value, exclusiveMaximum, true, false, path, errors);
            }
        }

        private void compareBound(BigDecimal value, Object boundObject, boolean exclusive,
                                  boolean lower, String path, List<String> errors) {
            if (!(boundObject instanceof BigDecimal bound)) return;
            int comparison = value.compareTo(bound);
            boolean bad = lower ? (exclusive ? comparison <= 0 : comparison < 0)
                    : (exclusive ? comparison >= 0 : comparison > 0);
            if (bad) errors.add(path + " violates numeric "
                    + (lower ? "minimum" : "maximum") + " " + bound);
        }

        private void checkCount(int actual, Object minimumObject, Object maximumObject,
                                String path, String unit, List<String> errors) {
            if (minimumObject instanceof BigDecimal minimum
                    && BigDecimal.valueOf(actual).compareTo(minimum) < 0) {
                errors.add(path + " must contain at least " + minimum + " " + unit);
            }
            if (maximumObject instanceof BigDecimal maximum
                    && BigDecimal.valueOf(actual).compareTo(maximum) > 0) {
                errors.add(path + " must contain at most " + maximum + " " + unit);
            }
        }

        private Object resolve(String reference) {
            if (!reference.startsWith("#/")) fail("only local schema references are supported: " + reference);
            Object current = root;
            for (String encoded : reference.substring(2).split("/")) {
                String token = encoded.replace("~1", "/").replace("~0", "~");
                current = object(current, "schema reference " + reference).get(token);
                if (current == null) fail("unresolved schema reference " + reference);
            }
            return current;
        }

        private String childPath(String parent, String key) {
            return key.matches("[A-Za-z_][A-Za-z0-9_]*") ? parent + "." + key
                    : parent + "['" + key.replace("'", "\\'") + "']";
        }
    }

    private static final class Json {
        private final String source;
        private int position;

        private Json(String source) {
            this.source = source;
        }

        private static Object parse(String source) {
            Json parser = new Json(source);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != source.length()) {
                throw new IllegalArgumentException("unexpected trailing JSON at offset " + parser.position);
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (position >= source.length()) error("unexpected end of JSON");
            char token = source.charAt(position);
            return switch (token) {
                case '{' -> objectValue();
                case '[' -> arrayValue();
                case '"' -> stringValue();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> {
                    if (token == '-' || Character.isDigit(token)) yield numberValue();
                    error("unexpected token");
                    yield null;
                }
            };
        }

        private Map<String, Object> objectValue() {
            position++;
            LinkedHashMap<String, Object> value = new LinkedHashMap<>();
            whitespace();
            if (take('}')) return value;
            while (true) {
                whitespace();
                if (position >= source.length() || source.charAt(position) != '"') {
                    error("object key must be a string");
                }
                String key = stringValue();
                whitespace();
                expect(':');
                Object child = value();
                if (value.containsKey(key)) error("duplicate object key " + key);
                value.put(key, child);
                whitespace();
                if (take('}')) return value;
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            position++;
            List<Object> value = new ArrayList<>();
            whitespace();
            if (take(']')) return value;
            while (true) {
                value.add(value());
                whitespace();
                if (take(']')) return value;
                expect(',');
            }
        }

        private String stringValue() {
            expect('"');
            StringBuilder value = new StringBuilder();
            while (position < source.length()) {
                char character = source.charAt(position++);
                if (character == '"') return value.toString();
                if (character == '\\') {
                    if (position >= source.length()) error("unterminated escape");
                    char escape = source.charAt(position++);
                    switch (escape) {
                        case '"', '\\', '/' -> value.append(escape);
                        case 'b' -> value.append('\b');
                        case 'f' -> value.append('\f');
                        case 'n' -> value.append('\n');
                        case 'r' -> value.append('\r');
                        case 't' -> value.append('\t');
                        case 'u' -> {
                            if (position + 4 > source.length()) error("short unicode escape");
                            String hex = source.substring(position, position + 4);
                            try {
                                value.append((char) Integer.parseInt(hex, 16));
                            } catch (NumberFormatException exception) {
                                error("invalid unicode escape");
                            }
                            position += 4;
                        }
                        default -> error("invalid escape");
                    }
                } else {
                    if (character < 0x20) error("control character in string");
                    value.append(character);
                }
            }
            error("unterminated string");
            return null;
        }

        private BigDecimal numberValue() {
            int start = position;
            if (take('-') && position >= source.length()) error("incomplete number");
            if (take('0')) {
                if (position < source.length() && Character.isDigit(source.charAt(position))) {
                    error("leading zero in number");
                }
            } else {
                digits();
            }
            if (take('.')) digits();
            if (position < source.length()
                    && (source.charAt(position) == 'e' || source.charAt(position) == 'E')) {
                position++;
                if (position < source.length()
                        && (source.charAt(position) == '+' || source.charAt(position) == '-')) {
                    position++;
                }
                digits();
            }
            try {
                return new BigDecimal(source.substring(start, position));
            } catch (NumberFormatException exception) {
                error("invalid number");
                return null;
            }
        }

        private void digits() {
            int start = position;
            while (position < source.length() && Character.isDigit(source.charAt(position))) position++;
            if (start == position) error("expected digit");
        }

        private Object literal(String spelling, Object value) {
            if (!source.startsWith(spelling, position)) error("invalid literal");
            position += spelling.length();
            return value;
        }

        private void whitespace() {
            while (position < source.length()) {
                char character = source.charAt(position);
                if (character == ' ' || character == '\n' || character == '\r' || character == '\t') {
                    position++;
                } else {
                    return;
                }
            }
        }

        private boolean take(char expected) {
            if (position < source.length() && source.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) error("expected '" + expected + "'");
        }

        private void error(String message) {
            throw new IllegalArgumentException(message + " at offset " + position);
        }
    }
}
