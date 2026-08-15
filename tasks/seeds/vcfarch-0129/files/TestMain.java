import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

public final class TestMain {
    private static final Path INSTALLER_SPEC = Path.of(
            "specifications", "vcf-installer", "vcf-installer-openapi.json");
    private static final Path PLAN_SCHEMA = Path.of("schema", "migration-plan.schema.json");
    private static final Path INVENTORY = Path.of("fixtures", "estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("fixtures", "compatibility-snapshot.json");
    private static final String INSTALLER_SPEC_SHA256 =
            "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String artifactText = ArchitectureClient.buildMigrationPlan();
        check(artifactText != null && !artifactText.isBlank(), "client returned an empty artifact");
        Map<String, Object> artifact = object(Json.parse(artifactText), "artifact");

        // The installer schema is deliberately the first validation performed on the artifact.
        byte[] installerBytes = Files.readAllBytes(INSTALLER_SPEC);
        check(sha256(installerBytes).equals(INSTALLER_SPEC_SHA256),
                "pinned VCF Installer specification hash mismatch");
        Map<String, Object> installer = object(
                Json.parse(new String(installerBytes, StandardCharsets.UTF_8)), "installer specification");
        Map<String, Object> components = object(installer.get("components"), "installer components");
        Map<String, Object> schemas = object(components.get("schemas"), "installer schemas");
        Map<String, Object> sddcSchema = object(schemas.get("SddcSpec"), "SddcSpec schema");
        SchemaValidator.validate(sddcSchema, artifact, installer, "$artifact");
        System.out.println("[ok] artifact validates against VCF Installer 9.1.0.0 SddcSpec");

        Map<String, Object> planSchema = readObject(PLAN_SCHEMA);
        SchemaValidator.validate(planSchema, artifact, planSchema, "$artifact");
        verifyResearch(artifact);

        Map<String, Object> inventory = readObject(INVENTORY);
        Map<String, Object> snapshot = readObject(SNAPSHOT);
        verifyArchitecture(artifact, inventory, snapshot);
        verifyMigration(artifact, inventory, snapshot);

        System.out.println("[ok] single-site consolidated four-host architecture matches inventory");
        System.out.println("[ok] ordered migration covers every component and pinned compatibility gate");
    }

    private static void verifyResearch(Map<String, Object> plan) {
        List<Object> entries = array(plan.get("research"), "research");
        Set<String> urls = new LinkedHashSet<>();
        for (Object value : entries) {
            Map<String, Object> entry = object(value, "research entry");
            check(!string(entry.get("title"), "research title").isBlank(),
                    "research title must not be blank");
            check(!string(entry.get("consultedFor"), "research consultedFor").isBlank(),
                    "research consultedFor must not be blank");
            String url = string(entry.get("url"), "research url");
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException error) {
                throw new AssertionError("research URL is not a valid URI: " + url, error);
            }
            String scheme = uri.getScheme();
            check("http".equals(scheme) || "https".equals(scheme),
                    "research URL must use HTTP or HTTPS: " + url);
            String host = uri.getHost();
            check(host != null, "research URL must have a host: " + url);
            String normalizedHost = host.toLowerCase(Locale.ROOT);
            check(normalizedHost.equals("broadcom.com") || normalizedHost.endsWith(".broadcom.com"),
                    "research URL must identify Broadcom-published material: " + url);
            check(uri.getUserInfo() == null, "research URL must not contain user information: " + url);
            check(urls.add(url), "duplicate research URL " + url);
        }
    }

    private static void verifyArchitecture(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> rules = object(snapshot.get("architectureRules"), "architectureRules");
        equal(plan.get("migrationSchemaVersion"), "1.0", "migration schema version");
        equal(plan.get("estateId"), inventory.get("estateId"), "estateId");
        equal(plan.get("siteId"), inventory.get("siteId"), "siteId");
        equal(plan.get("topology"), rules.get("topology"), "topology");
        equal(plan.get("topology"), inventory.get("topology"), "inventory topology");
        equal(plan.get("targetFleetVersion"), snapshot.get("targetFleetVersion"),
                "target fleet version");
        equal(plan.get("targetFleetVersion"), inventory.get("targetFleetVersion"),
                "inventory target fleet version");
        equal(plan.get("sddcId"), inventory.get("targetSddcId"), "SDDC id");
        equal(plan.get("workflowType"), rules.get("workflowType"), "workflow type");
        equal(plan.get("version"), snapshot.get("targetFleetVersion"), "SddcSpec version");

        List<Object> components = array(inventory.get("components"), "inventory components");
        List<String> expectedHosts = new ArrayList<>();
        Map<String, Object> vcenterComponent = null;
        Map<String, Object> nsxComponent = null;
        for (Object value : components) {
            Map<String, Object> component = object(value, "inventory component");
            String type = string(component.get("type"), "component type");
            if (type.equals("ESXI")) {
                expectedHosts.add(string(component.get("installerHostname"), "installer hostname"));
            } else if (type.equals("VCENTER")) {
                vcenterComponent = component;
            } else if (type.equals("NSX")) {
                nsxComponent = component;
            }
        }
        int minimumHosts = integer(rules.get("minimumHostCount"), "minimumHostCount");
        check(expectedHosts.size() == minimumHosts, "fixture does not hold at its pinned minimum host count");
        List<Object> hostSpecs = array(plan.get("hostSpecs"), "hostSpecs");
        check(hostSpecs.size() == minimumHosts,
                "hostSpecs must contain exactly the minimum supported " + minimumHosts + " hosts");
        List<String> actualHosts = new ArrayList<>();
        for (Object value : hostSpecs) {
            actualHosts.add(string(object(value, "hostSpec").get("hostname"), "hostSpec.hostname"));
        }
        equal(new LinkedHashSet<>(actualHosts), new LinkedHashSet<>(expectedHosts), "management hosts");
        check(actualHosts.size() == new LinkedHashSet<>(actualHosts).size(), "hostSpecs contains duplicates");

        check(vcenterComponent != null, "inventory has no vCenter component");
        Map<String, Object> inventoryVcenter = object(inventory.get("vcenter"), "inventory vcenter");
        Map<String, Object> vcenter = object(plan.get("vcenterSpec"), "vcenterSpec");
        equal(vcenter.get("vcenterHostname"), inventoryVcenter.get("hostname"), "vCenter hostname");
        equal(vcenter.get("rootVcenterPassword"), inventoryVcenter.get("fixtureRootPassword"),
                "fixture vCenter credential");
        equal(vcenter.get("ssoDomain"), inventoryVcenter.get("ssoDomain"), "SSO domain");
        equal(vcenter.get("version"), vcenterComponent.get("currentVersion"), "existing vCenter version");
        equal(vcenter.get("useExistingDeployment"), Boolean.TRUE, "vCenter reuse flag");

        Map<String, Object> cluster = object(plan.get("clusterSpec"), "clusterSpec");
        equal(cluster.get("datacenterName"), inventoryVcenter.get("datacenterName"), "datacenter name");
        equal(cluster.get("clusterName"), inventoryVcenter.get("clusterName"), "cluster name");

        check(nsxComponent != null, "inventory has no NSX component");
        Map<String, Object> inventoryNsx = object(inventory.get("nsx"), "inventory nsx");
        Map<String, Object> nsx = object(plan.get("nsxtSpec"), "nsxtSpec");
        equal(nsx.get("vipFqdn"), inventoryNsx.get("vipFqdn"), "NSX VIP");
        equal(nsx.get("version"), nsxComponent.get("currentVersion"), "existing NSX version");
        equal(nsx.get("useExistingDeployment"), Boolean.TRUE, "NSX reuse flag");
        Set<String> expectedManagers = strings(
                array(inventoryNsx.get("managerHostnames"), "managerHostnames"), "manager hostname");
        Set<String> actualManagers = new LinkedHashSet<>();
        for (Object value : array(nsx.get("nsxtManagers"), "nsxtManagers")) {
            actualManagers.add(string(object(value, "NSX manager").get("hostname"), "NSX manager hostname"));
        }
        equal(actualManagers, expectedManagers, "NSX managers");

        Map<String, Object> expectedStorage = object(inventory.get("storage"), "inventory storage");
        equal(expectedStorage.get("type"), rules.get("managementDomainStorage"), "storage type");
        Map<String, Object> datastore = object(plan.get("datastoreSpec"), "datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "datastoreSpec.vsanSpec");
        equal(vsan.get("datastoreName"), expectedStorage.get("datastoreName"), "vSAN datastore name");
        equal(vsan.get("failuresToTolerate"), expectedStorage.get("failuresToTolerate"),
                "vSAN failuresToTolerate");

        Map<String, Object> expectedDns = object(inventory.get("dns"), "inventory dns");
        Map<String, Object> dns = object(plan.get("dnsSpec"), "dnsSpec");
        equal(dns.get("subdomain"), expectedDns.get("subdomain"), "DNS subdomain");
        equal(dns.get("nameservers"), expectedDns.get("nameservers"), "DNS nameservers");
        equal(plan.get("ntpServers"), inventory.get("ntpServers"), "NTP servers");

        verifyNetworks(plan, inventory);
        verifyDistributedSwitch(plan, inventory);
    }

    private static void verifyNetworks(Map<String, Object> plan, Map<String, Object> inventory) {
        Map<String, Map<String, Object>> expected = indexBy(
                array(inventory.get("networks"), "inventory networks"), "networkType", "inventory network");
        Map<String, Map<String, Object>> actual = indexBy(
                array(plan.get("networkSpecs"), "networkSpecs"), "networkType", "networkSpec");
        equal(actual.keySet(), expected.keySet(), "network types");
        for (Map.Entry<String, Map<String, Object>> entry : expected.entrySet()) {
            Map<String, Object> got = actual.get(entry.getKey());
            Map<String, Object> want = entry.getValue();
            for (String field : List.of("networkType", "vlanId", "subnet", "gateway", "mtu")) {
                equal(got.get(field), want.get(field), entry.getKey() + " network " + field);
            }
        }
    }

    private static void verifyDistributedSwitch(
            Map<String, Object> plan, Map<String, Object> inventory) {
        Map<String, Object> expected = object(inventory.get("distributedSwitch"), "distributedSwitch");
        List<Object> switches = array(plan.get("dvsSpecs"), "dvsSpecs");
        check(switches.size() == 1, "consolidated design must define exactly one distributed switch");
        Map<String, Object> dvs = object(switches.get(0), "dvsSpec");
        equal(dvs.get("dvsName"), expected.get("name"), "distributed switch name");
        Set<String> expectedNetworks = indexBy(
                array(inventory.get("networks"), "inventory networks"), "networkType", "network").keySet();
        equal(strings(array(dvs.get("networks"), "dvs networks"), "dvs network"),
                expectedNetworks, "distributed switch networks");

        Set<String> expectedMappings = vmnicMappings(
                array(expected.get("vmnicsToUplinks"), "expected vmnic mappings"));
        Set<String> actualMappings = vmnicMappings(
                array(dvs.get("vmnicsToUplinks"), "vmnicsToUplinks"));
        equal(actualMappings, expectedMappings, "vmnic-to-uplink mappings");
    }

    private static void verifyMigration(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> inventoryById = indexBy(
                array(inventory.get("components"), "inventory components"), "id", "inventory component");
        Map<String, Map<String, Object>> transitions = indexBy(
                array(snapshot.get("componentTransitions"), "componentTransitions"),
                "componentId", "component transition");
        equal(transitions.keySet(), inventoryById.keySet(), "snapshot component coverage");

        List<Object> steps = array(plan.get("migrationSteps"), "migrationSteps");
        check(steps.size() == inventoryById.size(),
                "migrationSteps must name every inventoried component exactly once");
        Set<String> seen = new LinkedHashSet<>();
        for (int i = 0; i < steps.size(); i++) {
            Map<String, Object> step = object(steps.get(i), "migration step");
            equal(step.get("sequence"), Long.valueOf(i + 1L), "contiguous migration sequence");
            String componentId = string(step.get("componentId"), "step componentId");
            check(seen.add(componentId), "duplicate migration component " + componentId);
            Map<String, Object> component = inventoryById.get(componentId);
            Map<String, Object> transition = transitions.get(componentId);
            check(component != null && transition != null, "unknown migration component " + componentId);

            equal(transition.get("componentType"), component.get("type"),
                    componentId + " snapshot component type");
            equal(transition.get("sourceVersion"), component.get("currentVersion"),
                    componentId + " snapshot source version");
            equal(step.get("componentType"), component.get("type"), componentId + " component type");
            equal(step.get("fromVersion"), component.get("currentVersion"), componentId + " source version");
            equal(step.get("targetVersion"), transition.get("targetVersion"), componentId + " target version");
            equal(step.get("action"), transition.get("action"), componentId + " action");
            equal(strings(array(step.get("gates"), componentId + " gates"), "gate"),
                    strings(array(transition.get("requiredGates"), "requiredGates"), "required gate"),
                    componentId + " gates");
            Set<String> dependencies = strings(
                    array(step.get("dependsOn"), componentId + " dependsOn"), "dependency");
            Set<String> requiredDependencies = strings(
                    array(transition.get("dependsOn"), "transition dependsOn"), "dependency");
            equal(dependencies, requiredDependencies, componentId + " dependencies");
            check(seen.containsAll(dependencies),
                    componentId + " appears before one or more dependencies: " + dependencies);
        }
        equal(seen, inventoryById.keySet(), "migration component coverage");

        Map<String, Object> schemaPin = object(snapshot.get("installerSchema"), "installerSchema pin");
        equal(schemaPin.get("tag"), "9.1.0.0", "installer schema tag");
        equal(schemaPin.get("sha256"), INSTALLER_SPEC_SHA256, "installer schema snapshot hash");
    }

    private static Set<String> vmnicMappings(List<Object> values) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            Map<String, Object> mapping = object(value, "vmnic mapping");
            result.add(string(mapping.get("id"), "vmnic id") + "="
                    + string(mapping.get("uplink"), "uplink"));
        }
        check(result.size() == values.size(), "duplicate vmnic mapping");
        return result;
    }

    private static Map<String, Map<String, Object>> indexBy(
            List<Object> values, String key, String label) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, label);
            String id = string(item.get(key), label + "." + key);
            check(result.put(id, item) == null, "duplicate " + label + " key " + id);
        }
        return result;
    }

    private static Map<String, Object> readObject(Path path) {
        try {
            return object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)), path.toString());
        } catch (IOException error) {
            throw new AssertionError("cannot read " + path + ": " + error.getMessage(), error);
        }
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

    private static int integer(Object value, String label) {
        check(value instanceof Long, label + " must be an integer");
        return Math.toIntExact((Long) value);
    }

    private static Set<String> strings(List<Object> values, String label) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            check(result.add(string(value, label)), "duplicate " + label + " " + value);
        }
        return result;
    }

    private static void equal(Object actual, Object expected, String label) {
        check(Objects.equals(actual, expected),
                label + " mismatch: expected " + expected + ", got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static String sha256(byte[] bytes) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder result = new StringBuilder(digest.length * 2);
        for (byte value : digest) {
            result.append(String.format("%02x", value & 0xff));
        }
        return result.toString();
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(
                Map<String, Object> schema,
                Object instance,
                Map<String, Object> rootSchema,
                String path) {
            if (schema.containsKey("$ref")) {
                String reference = string(schema.get("$ref"), path + ".$ref");
                validate(resolve(reference, rootSchema), instance, rootSchema, path);
                return;
            }

            validateComposition(schema, instance, rootSchema, path);
            if (schema.containsKey("const")) {
                equal(instance, schema.get("const"), path + " const");
            }
            if (schema.containsKey("enum")) {
                check(array(schema.get("enum"), path + ".enum").contains(instance),
                        path + " is not one of the schema enum values");
            }

            Object type = schema.get("type");
            if (type != null) {
                check(matchesType(type, instance), path + " does not match schema type " + type);
            }
            if (instance instanceof Map<?, ?> map) {
                validateObject(schema, map, rootSchema, path);
            } else if (instance instanceof List<?> list) {
                validateArray(schema, list, rootSchema, path);
            } else if (instance instanceof String text) {
                validateString(schema, text, path);
            } else if (instance instanceof Number number) {
                validateNumber(schema, number, path);
            }
        }

        private static void validateComposition(
                Map<String, Object> schema, Object instance, Map<String, Object> root, String path) {
            if (schema.containsKey("allOf")) {
                for (Object branch : array(schema.get("allOf"), path + ".allOf")) {
                    validate(object(branch, path + ".allOf branch"), instance, root, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                check(countPassing(array(schema.get("anyOf"), path + ".anyOf"), instance, root, path) >= 1,
                        path + " does not satisfy any anyOf branch");
            }
            if (schema.containsKey("oneOf")) {
                check(countPassing(array(schema.get("oneOf"), path + ".oneOf"), instance, root, path) == 1,
                        path + " does not satisfy exactly one oneOf branch");
            }
        }

        private static int countPassing(
                List<Object> branches, Object instance, Map<String, Object> root, String path) {
            int passed = 0;
            for (Object branch : branches) {
                try {
                    validate(object(branch, path + " branch"), instance, root, path);
                    passed++;
                } catch (AssertionError ignored) {
                    // Branch failures are expected while evaluating anyOf/oneOf.
                }
            }
            return passed;
        }

        private static void validateObject(
                Map<String, Object> schema,
                Map<?, ?> instance,
                Map<String, Object> root,
                String path) {
            Set<String> known = new LinkedHashSet<>();
            if (schema.containsKey("required")) {
                for (Object key : array(schema.get("required"), path + ".required")) {
                    String name = string(key, path + " required property");
                    check(instance.containsKey(name), path + " is missing required property " + name);
                }
            }
            if (schema.containsKey("properties")) {
                Map<String, Object> properties = object(schema.get("properties"), path + ".properties");
                known.addAll(properties.keySet());
                for (Map.Entry<String, Object> property : properties.entrySet()) {
                    if (instance.containsKey(property.getKey())) {
                        validate(object(property.getValue(), path + "." + property.getKey() + " schema"),
                                instance.get(property.getKey()), root, path + "." + property.getKey());
                    }
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (Object key : instance.keySet()) {
                    check(known.contains(key), path + " contains unsupported property " + key);
                }
            }
        }

        private static void validateArray(
                Map<String, Object> schema,
                List<?> instance,
                Map<String, Object> root,
                String path) {
            if (schema.containsKey("minItems")) {
                check(instance.size() >= integer(schema.get("minItems"), path + ".minItems"),
                        path + " has too few items");
            }
            if (schema.containsKey("maxItems")) {
                check(instance.size() <= integer(schema.get("maxItems"), path + ".maxItems"),
                        path + " has too many items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                check(new LinkedHashSet<>(instance).size() == instance.size(), path + " items are not unique");
            }
            if (schema.containsKey("items")) {
                Map<String, Object> itemSchema = object(schema.get("items"), path + ".items");
                for (int i = 0; i < instance.size(); i++) {
                    validate(itemSchema, instance.get(i), root, path + "[" + i + "]");
                }
            }
        }

        private static void validateString(Map<String, Object> schema, String value, String path) {
            int length = value.codePointCount(0, value.length());
            if (schema.containsKey("minLength")) {
                check(length >= integer(schema.get("minLength"), path + ".minLength"),
                        path + " is shorter than minLength");
            }
            if (schema.containsKey("maxLength")) {
                check(length <= integer(schema.get("maxLength"), path + ".maxLength"),
                        path + " is longer than maxLength");
            }
            if (schema.containsKey("pattern")) {
                String regex = string(schema.get("pattern"), path + ".pattern");
                check(Pattern.compile(regex).matcher(value).find(), path + " does not match pattern " + regex);
            }
        }

        private static void validateNumber(Map<String, Object> schema, Number value, String path) {
            BigDecimal number = new BigDecimal(value.toString());
            if (schema.containsKey("minimum")) {
                BigDecimal minimum = new BigDecimal(schema.get("minimum").toString());
                check(number.compareTo(minimum) >= 0, path + " is below minimum " + minimum);
            }
            if (schema.containsKey("maximum")) {
                BigDecimal maximum = new BigDecimal(schema.get("maximum").toString());
                check(number.compareTo(maximum) <= 0, path + " is above maximum " + maximum);
            }
        }

        private static boolean matchesType(Object declared, Object instance) {
            if (declared instanceof List<?> choices) {
                for (Object choice : choices) {
                    if (matchesType(choice, instance)) {
                        return true;
                    }
                }
                return false;
            }
            String type = string(declared, "schema type");
            return switch (type) {
                case "object" -> instance instanceof Map<?, ?>;
                case "array" -> instance instanceof List<?>;
                case "string" -> instance instanceof String;
                case "boolean" -> instance instanceof Boolean;
                case "number" -> instance instanceof Number;
                case "integer" -> instance instanceof Long;
                case "null" -> instance == null;
                default -> throw new AssertionError("unsupported schema type " + type);
            };
        }

        private static Map<String, Object> resolve(String reference, Map<String, Object> root) {
            check(reference.startsWith("#/"), "only local schema references are supported: " + reference);
            Object value = root;
            for (String rawPart : reference.substring(2).split("/")) {
                String part = rawPart.replace("~1", "/").replace("~0", "~");
                value = object(value, "schema reference parent").get(part);
                check(value != null, "unresolved schema reference " + reference);
            }
            return object(value, "schema reference " + reference);
        }
    }

    private static final class Json {
        private final String source;
        private int offset;

        private Json(String source) {
            this.source = source;
        }

        static Object parse(String source) {
            Json parser = new Json(source);
            Object value = parser.value();
            parser.whitespace();
            check(parser.offset == source.length(), "unexpected JSON content at offset " + parser.offset);
            return value;
        }

        private Object value() {
            whitespace();
            check(offset < source.length(), "unexpected end of JSON");
            return switch (source.charAt(offset)) {
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
            Map<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                check(offset < source.length() && source.charAt(offset) == '"',
                        "expected JSON object key at offset " + offset);
                String key = string();
                check(!result.containsKey(key), "duplicate JSON object key " + key);
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> array() {
            expect('[');
            List<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (offset < source.length()) {
                char current = source.charAt(offset++);
                if (current == '"') {
                    return result.toString();
                }
                if (current == '\\') {
                    check(offset < source.length(), "unterminated JSON escape");
                    char escaped = source.charAt(offset++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicode());
                        default -> throw new AssertionError("invalid JSON escape \\" + escaped);
                    }
                } else {
                    check(current >= 0x20, "unescaped control character in JSON string");
                    result.append(current);
                }
            }
            throw new AssertionError("unterminated JSON string");
        }

        private char unicode() {
            check(offset + 4 <= source.length(), "short JSON unicode escape");
            int value = 0;
            for (int i = 0; i < 4; i++) {
                int digit = Character.digit(source.charAt(offset++), 16);
                check(digit >= 0, "invalid JSON unicode escape");
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private Object literal(String token, Object value) {
            check(source.startsWith(token, offset), "invalid JSON token at offset " + offset);
            offset += token.length();
            return value;
        }

        private Number number() {
            int start = offset;
            if (take('-')) {
                check(offset < source.length(), "incomplete JSON number");
            }
            if (take('0')) {
                check(offset >= source.length() || !Character.isDigit(source.charAt(offset)),
                        "leading zero in JSON number");
            } else {
                digits();
            }
            boolean integral = true;
            if (take('.')) {
                integral = false;
                digits();
            }
            if (offset < source.length() && (source.charAt(offset) == 'e' || source.charAt(offset) == 'E')) {
                integral = false;
                offset++;
                if (offset < source.length() && (source.charAt(offset) == '+' || source.charAt(offset) == '-')) {
                    offset++;
                }
                digits();
            }
            String token = source.substring(start, offset);
            try {
                return integral ? Long.valueOf(token) : new BigDecimal(token);
            } catch (NumberFormatException error) {
                throw new AssertionError("invalid JSON number " + token, error);
            }
        }

        private void digits() {
            int start = offset;
            while (offset < source.length() && Character.isDigit(source.charAt(offset))) {
                offset++;
            }
            check(offset > start, "expected digit at offset " + offset);
        }

        private void whitespace() {
            while (offset < source.length()) {
                char current = source.charAt(offset);
                if (current == ' ' || current == '\n' || current == '\r' || current == '\t') {
                    offset++;
                } else {
                    return;
                }
            }
        }

        private boolean take(char expected) {
            if (offset < source.length() && source.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            check(take(expected), "expected '" + expected + "' at JSON offset " + offset);
        }
    }
}
