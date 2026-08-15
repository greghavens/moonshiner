import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

public class TestMain {
    private static final Path INVENTORY_PATH = Path.of("fixtures", "estate-inventory.json");
    private static final Path SNAPSHOT_PATH = Path.of("grading", "compatibility-snapshot.json");
    private static final Path INSTALLER_SPEC_PATH = Path.of(
            "specifications", "vcf-installer", "vcf-installer-openapi.json");
    private static final Path MIGRATION_SCHEMA_PATH = Path.of(
            "specifications", "migration-plan-schema.json");

    public static void main(String[] args) throws Exception {
        Map<String, Object> inventory = object(Json.parse(Files.readString(INVENTORY_PATH)), "inventory");
        Map<String, Object> artifact = object(invokeClient(inventory), "architecture artifact");

        // This is deliberately the first artifact assertion. The submitted greenfield
        // object is checked with the schema copied from the pinned upstream OpenAPI file.
        Map<String, Object> installer = object(
                Json.parse(Files.readString(INSTALLER_SPEC_PATH)), "installer OpenAPI document");
        Map<String, Object> sddcSchema = object(path(installer,
                "components", "schemas", "SddcSpec"), "components.schemas.SddcSpec");
        try {
            new SchemaValidator(installer).validate(
                    artifact.get("greenfieldSddcSpec"), sddcSchema, "greenfieldSddcSpec");
        } catch (SchemaFailure failure) {
            throw new AssertionError("installer SddcSpec schema validation failed first: "
                    + failure.getMessage());
        }

        Map<String, Object> snapshot = object(
                Json.parse(Files.readString(SNAPSHOT_PATH)), "compatibility snapshot");
        Map<String, Object> migrationSchema = object(
                Json.parse(Files.readString(MIGRATION_SCHEMA_PATH)), "migration plan schema");
        Object migrationValue = artifact.get("migrationPlan");
        try {
            new SchemaValidator(migrationSchema).validate(
                    migrationValue, migrationSchema, "migrationPlan");
        } catch (SchemaFailure failure) {
            throw new AssertionError("migration plan schema validation failed: "
                    + failure.getMessage());
        }

        checkGreenfield(artifact, inventory, snapshot);
        checkStorageDecision(artifact, snapshot);
        checkMigrationPlan(artifact, inventory, snapshot);
        checkFleetIntegration(artifact, inventory, snapshot);
        checkResearchConsulted(artifact);
        System.out.println("all checks passed");
    }

    private static Object invokeClient(Map<String, Object> inventory) throws Exception {
        JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
        if (compiler == null) {
            throw new AssertionError("a full JDK is required");
        }
        Path output = Files.createTempDirectory(Path.of("."), ".vcfarch-classes-");
        DiagnosticCollector<JavaFileObject> diagnostics = new DiagnosticCollector<>();
        try (StandardJavaFileManager files = compiler.getStandardFileManager(
                diagnostics, null, StandardCharsets.UTF_8)) {
            Iterable<? extends JavaFileObject> units = files.getJavaFileObjects(
                    Path.of("ArchitectureClient.java").toFile());
            boolean compiled = Boolean.TRUE.equals(compiler.getTask(
                    null, files, diagnostics, List.of("-d", output.toString()), null, units).call());
            if (!compiled) {
                StringBuilder message = new StringBuilder("ArchitectureClient.java did not compile:\n");
                for (Diagnostic<? extends JavaFileObject> diagnostic : diagnostics.getDiagnostics()) {
                    message.append(diagnostic).append('\n');
                }
                throw new AssertionError(message.toString());
            }
        }

        try (URLClassLoader loader = new URLClassLoader(
                new URL[] {output.toUri().toURL()}, TestMain.class.getClassLoader())) {
            Class<?> client = Class.forName("ArchitectureClient", true, loader);
            Method design = client.getMethod("design", Map.class);
            try {
                return design.invoke(null, inventory);
            } catch (InvocationTargetException failure) {
                Throwable cause = failure.getCause();
                if (cause instanceof Exception exception) {
                    throw exception;
                }
                if (cause instanceof Error error) {
                    throw error;
                }
                throw failure;
            }
        } finally {
            deleteTree(output);
        }
    }

    private static void checkGreenfield(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> spec = object(artifact.get("greenfieldSddcSpec"), "greenfieldSddcSpec");
        Map<String, Object> greenfield = object(inventory.get("greenfield"), "inventory.greenfield");
        Map<String, Object> targetFleet = object(inventory.get("targetFleet"), "inventory.targetFleet");
        Map<String, Object> expectedStorage = object(
                path(snapshot, "storageDecision", "ESA"), "snapshot.storageDecision.ESA");

        equal(spec.get("sddcId"), greenfield.get("sddcId"), "SddcSpec sddcId");
        equal(spec.get("workflowType"), greenfield.get("workflowType"), "SddcSpec workflowType");
        equal(spec.get("version"), snapshot.get("targetVcfVersion"), "SddcSpec version");
        equal(spec.get("vcfInstanceName"), targetFleet.get("vcfInstanceName"),
                "SddcSpec VCF instance name");

        List<Object> hostSpecs = array(spec.get("hostSpecs"), "greenfieldSddcSpec.hostSpecs");
        int selectedHostCount = integer(expectedStorage.get("hostCount"), "ESA host count");
        equal(hostSpecs.size(), selectedHostCount, "selected SddcSpec host count");
        List<Object> inventoryHosts = array(greenfield.get("hosts"), "inventory.greenfield.hosts");
        equal(hostSpecs.size(), inventoryHosts.size(), "all selected hosts represented");
        for (int i = 0; i < inventoryHosts.size(); i++) {
            equal(path(object(hostSpecs.get(i), "host spec"), "hostname"), inventoryHosts.get(i),
                    "hostSpecs[" + i + "].hostname");
        }

        Map<String, Object> fixtureVcenter = object(greenfield.get("vcenter"), "fixture vCenter");
        Map<String, Object> vcenterSpec = object(spec.get("vcenterSpec"), "vcenterSpec");
        equal(vcenterSpec.get("vcenterHostname"), fixtureVcenter.get("hostname"), "vCenter hostname");
        equal(vcenterSpec.get("rootVcenterPassword"), fixtureVcenter.get("rootPassword"),
                "vCenter fixture credential");

        Map<String, Object> fixtureDns = object(greenfield.get("dns"), "fixture DNS");
        Map<String, Object> dnsSpec = object(spec.get("dnsSpec"), "dnsSpec");
        equal(dnsSpec.get("subdomain"), fixtureDns.get("subdomain"), "DNS subdomain");
        equal(dnsSpec.get("nameservers"), fixtureDns.get("nameservers"), "DNS nameservers");

        List<Object> expectedNetworks = array(greenfield.get("networks"), "fixture networks");
        List<Object> actualNetworks = array(spec.get("networkSpecs"), "SddcSpec networks");
        equal(actualNetworks, expectedNetworks, "SddcSpec network definitions");
        for (Object networkValue : actualNetworks) {
            Map<String, Object> network = object(networkValue, "network spec");
            equal(integer(network.get("mtu"), "network MTU"),
                    integer(expectedStorage.get("vsanMtu"), "snapshot MTU"),
                    "greenfield jumbo-frame MTU");
        }

        Map<String, Object> datastore = object(spec.get("datastoreSpec"), "datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "datastoreSpec.vsanSpec");
        Map<String, Object> esa = object(vsan.get("esaConfig"), "vsanSpec.esaConfig");
        equal(esa.get("enabled"), expectedStorage.get("esaEnabled"), "ESA enabled in SddcSpec");

        List<Object> dvsSpecs = array(spec.get("dvsSpecs"), "dvsSpecs");
        check(!dvsSpecs.isEmpty(), "at least one VDS is required for the selected vSAN design");
        boolean foundVsanVds = false;
        for (Object value : dvsSpecs) {
            Map<String, Object> dvs = object(value, "VDS");
            List<Object> networkTypes = array(dvs.get("networks"), "VDS networks");
            if (networkTypes.contains("VSAN")) {
                foundVsanVds = true;
                equal(integer(dvs.get("mtu"), "vSAN VDS MTU"),
                        integer(expectedStorage.get("vsanMtu"), "snapshot MTU"),
                        "vSAN VDS MTU");
                check(!array(dvs.get("vmnicsToUplinks"), "vSAN VDS uplinks").isEmpty(),
                        "vSAN VDS must map at least one physical NIC");
            }
        }
        check(foundVsanVds, "a VDS must carry the VSAN network");

        Map<String, Object> fixtureNsx = object(greenfield.get("nsx"), "fixture NSX");
        Map<String, Object> nsxtSpec = object(spec.get("nsxtSpec"), "nsxtSpec");
        equal(nsxtSpec.get("vipFqdn"), fixtureNsx.get("vipFqdn"), "NSX VIP");
        equal(nsxtSpec.get("transportVlanId"), fixtureNsx.get("transportVlanId"),
                "NSX transport VLAN");
        List<Object> managerNames = array(
                fixtureNsx.get("managerHostnames"), "fixture NSX managers");
        List<Object> managers = array(nsxtSpec.get("nsxtManagers"), "NSX managers");
        equal(managers.size(), managerNames.size(), "NSX manager count");
        for (int i = 0; i < managers.size(); i++) {
            equal(path(object(managers.get(i), "NSX manager"), "hostname"), managerNames.get(i),
                    "NSX manager hostname " + i);
        }
    }

    private static void checkStorageDecision(
            Map<String, Object> artifact, Map<String, Object> snapshot) {
        Map<String, Object> actual = object(artifact.get("storageDecision"), "storageDecision");
        Map<String, Object> expected = object(snapshot.get("storageDecision"),
                "snapshot.storageDecision");
        equal(actual.get("selectedArchitecture"), expected.get("selectedArchitecture"),
                "selected storage architecture");
        for (String architecture : List.of("OSA", "ESA")) {
            Map<String, Object> actualAlternative = object(
                    actual.get(architecture), "storageDecision." + architecture);
            Map<String, Object> expectedAlternative = object(
                    expected.get(architecture), "snapshot.storageDecision." + architecture);
            for (String field : List.of(
                    "hostCount", "hostUplinkGbps", "vsanMtu", "esaEnabled")) {
                equal(actualAlternative.get(field), expectedAlternative.get(field),
                        architecture + " " + field);
            }
        }
        int osaHosts = integer(path(actual, "OSA", "hostCount"), "OSA host count");
        int esaHosts = integer(path(actual, "ESA", "hostCount"), "ESA host count");
        check(osaHosts != esaHosts, "storage alternatives must have different host counts");
        int osaNetwork = integer(path(actual, "OSA", "hostUplinkGbps"), "OSA network");
        int esaNetwork = integer(path(actual, "ESA", "hostUplinkGbps"), "ESA network");
        check(osaNetwork != esaNetwork, "storage alternatives must have different network requirements");
    }

    private static void checkMigrationPlan(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        List<Object> plan = array(artifact.get("migrationPlan"), "migrationPlan");
        List<Object> rules = array(snapshot.get("migrationRules"), "snapshot.migrationRules");
        List<Object> components = array(path(inventory, "existingEstate", "components"),
                "inventory components");
        equal(plan.size(), components.size(), "one migration step per inventory component");
        equal(plan.size(), rules.size(), "one migration step per compatibility rule");

        Map<String, Map<String, Object>> inventoryById = new HashMap<>();
        for (Object value : components) {
            Map<String, Object> component = object(value, "inventory component");
            inventoryById.put(string(component.get("id"), "component id"), component);
        }

        Set<String> seen = new HashSet<>();
        for (int i = 0; i < rules.size(); i++) {
            Map<String, Object> expected = object(rules.get(i), "migration rule");
            Map<String, Object> actual = object(plan.get(i), "migration step");
            int order = i + 1;
            equal(integer(actual.get("order"), "migration order"), order,
                    "contiguous migration order");
            equal(actual.get("order"), expected.get("order"), "snapshot migration order");
            String id = string(expected.get("componentId"), "rule componentId");
            equal(actual.get("componentId"), id, "ordered componentId");
            check(seen.add(id), "component appears more than once: " + id);
            Map<String, Object> source = inventoryById.get(id);
            check(source != null, "snapshot component missing from estate: " + id);
            equal(actual.get("component"), source.get("component"), id + " component name");
            equal(actual.get("currentVersion"), source.get("version"), id + " current version");
            equal(actual.get("targetProduct"), expected.get("targetProduct"), id + " target product");
            equal(actual.get("targetVersion"), expected.get("targetVersion"), id + " target version");
            equal(actual.get("action"), expected.get("action"), id + " action");
            equal(actual.get("gates"), expected.get("gates"), id + " prerequisite gates");
        }
        equal(seen, inventoryById.keySet(), "every inventoried component migrated exactly once");
    }

    private static void checkFleetIntegration(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> actual = object(artifact.get("fleetIntegration"), "fleetIntegration");
        Map<String, Object> expected = object(snapshot.get("fleetIntegration"),
                "snapshot.fleetIntegration");
        Map<String, Object> target = object(inventory.get("targetFleet"), "target fleet");
        Map<String, Object> estate = object(inventory.get("existingEstate"), "existing estate");
        equal(actual.get("fleetId"), target.get("fleetId"), "fleet integration fleetId");
        equal(actual.get("vcfInstanceName"), target.get("vcfInstanceName"),
                "fleet integration VCF instance");
        equal(actual.get("domainName"), estate.get("domainName"), "imported domain name");
        equal(actual.get("mode"), expected.get("mode"), "fleet integration mode");
        equal(actual.get("mode"), estate.get("integrationMode"), "fixture integration mode");
        equal(actual.get("afterOrder"), expected.get("afterOrder"), "fleet integration order");
        equal(actual.get("gates"), expected.get("gates"), "fleet integration gates");
    }

    private static void checkResearchConsulted(Map<String, Object> artifact) {
        List<Object> research = array(artifact.get("researchConsulted"), "researchConsulted");
        check(research.size() >= 3,
                "researchConsulted must cover the compatibility guide, interoperability matrix, "
                        + "and upgrade guidance");
        boolean compatibilityGuide = false;
        boolean interoperabilityMatrix = false;
        boolean upgradeGuidance = false;
        for (int i = 0; i < research.size(); i++) {
            Map<String, Object> entry = object(research.get(i), "researchConsulted[" + i + "]");
            string(entry.get("title"), "research title");
            String url = string(entry.get("url"), "research URL");
            string(entry.get("consultedAt"), "research consultedAt");
            string(entry.get("purpose"), "research purpose");

            java.net.URI uri;
            try {
                uri = java.net.URI.create(url);
            } catch (IllegalArgumentException failure) {
                throw new AssertionError("research URL is invalid: " + url);
            }
            check("https".equalsIgnoreCase(uri.getScheme()),
                    "research URL must use HTTPS: " + url);
            String host = uri.getHost();
            if (host != null) {
                host = host.toLowerCase(java.util.Locale.ROOT);
            }
            check(host != null && !host.equals("localhost") && !host.endsWith(".invalid")
                            && !host.equals("127.0.0.1") && !host.equals("::1"),
                    "research URL must identify a real external source: " + url);
            boolean broadcomHost = host.equals("broadcom.com") || host.endsWith(".broadcom.com");
            compatibilityGuide |= host.equals("compatibilityguide.broadcom.com");
            interoperabilityMatrix |= host.equals("interopmatrix.broadcom.com");
            upgradeGuidance |= broadcomHost
                    && !host.equals("compatibilityguide.broadcom.com")
                    && !host.equals("interopmatrix.broadcom.com")
                    && uri.getPath() != null && !uri.getPath().equals("/");
        }
        check(compatibilityGuide, "researchConsulted is missing the Broadcom Compatibility Guide");
        check(interoperabilityMatrix,
                "researchConsulted is missing the Broadcom Product Interoperability Matrix");
        check(upgradeGuidance, "researchConsulted is missing relevant Broadcom upgrade guidance");
    }

    private static Object path(Map<String, Object> root, String... keys) {
        Object current = root;
        StringBuilder location = new StringBuilder("$");
        for (String key : keys) {
            Map<String, Object> map = object(current, location.toString());
            current = map.get(key);
            location.append('.').append(key);
        }
        return current;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String name) {
        if (!(value instanceof Map<?, ?>)) {
            throw new AssertionError(name + " must be an object, got " + typeName(value));
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String name) {
        if (!(value instanceof List<?>)) {
            throw new AssertionError(name + " must be an array, got " + typeName(value));
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String name) {
        if (!(value instanceof String text) || text.isEmpty()) {
            throw new AssertionError(name + " must be a non-empty string");
        }
        return text;
    }

    private static int integer(Object value, String name) {
        if (!(value instanceof Number number)) {
            throw new AssertionError(name + " must be an integer");
        }
        double asDouble = number.doubleValue();
        int asInt = number.intValue();
        if (asDouble != asInt) {
            throw new AssertionError(name + " must be an integer");
        }
        return asInt;
    }

    private static void equal(Object actual, Object expected, String name) {
        if (!java.util.Objects.equals(actual, expected)) {
            throw new AssertionError(name + " mismatch: expected " + expected + ", got " + actual);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static String typeName(Object value) {
        return value == null ? "null" : value.getClass().getSimpleName();
    }

    private static void deleteTree(Path root) throws IOException {
        if (!Files.exists(root)) {
            return;
        }
        try (var paths = Files.walk(root)) {
            for (Path path : paths.sorted(java.util.Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        }
    }

    private static final class SchemaValidator {
        private final Map<String, Object> document;

        private SchemaValidator(Map<String, Object> document) {
            this.document = document;
        }

        private void validate(Object value, Map<String, Object> rawSchema, String location) {
            Map<String, Object> schema = rawSchema;
            if (schema.containsKey("$ref")) {
                schema = resolve(stringValue(schema.get("$ref"), location + ".$ref"));
            }
            if (value == null && Boolean.TRUE.equals(schema.get("nullable"))) {
                return;
            }
            Object typeValue = schema.get("type");
            if (typeValue instanceof String type) {
                validateType(value, type, location);
            }

            Object enumValue = schema.get("enum");
            if (enumValue instanceof List<?> allowed && !allowed.contains(value)) {
                fail(location, "value " + value + " is not in enum " + allowed);
            }

            if (value instanceof Map<?, ?> valueMap) {
                validateObject(castMap(valueMap), schema, location);
            } else if (value instanceof List<?> valueList) {
                validateArray(valueList, schema, location);
            } else if (value instanceof String text) {
                validateString(text, schema, location);
            } else if (value instanceof Number number) {
                validateNumber(number, schema, location);
            }

            Object allOfValue = schema.get("allOf");
            if (allOfValue instanceof List<?> allOf) {
                for (int i = 0; i < allOf.size(); i++) {
                    validate(value, schemaMap(allOf.get(i), location + ".allOf[" + i + "]"), location);
                }
            }
        }

        private void validateType(Object value, String type, String location) {
            boolean valid = switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "boolean" -> value instanceof Boolean;
                case "number" -> value instanceof Number;
                case "integer" -> isInteger(value);
                default -> true;
            };
            if (!valid) {
                fail(location, "expected " + type + " but got " + typeName(value));
            }
        }

        private void validateObject(
                Map<String, Object> value, Map<String, Object> schema, String location) {
            Object requiredValue = schema.get("required");
            if (requiredValue instanceof List<?> required) {
                for (Object keyValue : required) {
                    String key = stringValue(keyValue, location + ".required");
                    if (!value.containsKey(key) || value.get(key) == null) {
                        fail(location, "missing required property " + key);
                    }
                }
            }
            Map<String, Object> properties = Map.of();
            if (schema.get("properties") instanceof Map<?, ?> propertyMap) {
                properties = castMap(propertyMap);
                for (Map.Entry<String, Object> entry : properties.entrySet()) {
                    if (value.containsKey(entry.getKey())) {
                        validate(value.get(entry.getKey()),
                                schemaMap(entry.getValue(), location + "." + entry.getKey()),
                                location + "." + entry.getKey());
                    }
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : value.keySet()) {
                    if (!properties.containsKey(key)) {
                        fail(location, "additional property is not allowed: " + key);
                    }
                }
            }
        }

        private void validateArray(List<?> value, Map<String, Object> schema, String location) {
            if (schema.get("minItems") instanceof Number min && value.size() < min.intValue()) {
                fail(location, "has fewer than " + min.intValue() + " items");
            }
            if (schema.get("maxItems") instanceof Number max && value.size() > max.intValue()) {
                fail(location, "has more than " + max.intValue() + " items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                Set<Object> unique = new HashSet<>(value);
                if (unique.size() != value.size()) {
                    fail(location, "items must be unique");
                }
            }
            if (schema.get("items") instanceof Map<?, ?> itemSchema) {
                Map<String, Object> items = castMap(itemSchema);
                for (int i = 0; i < value.size(); i++) {
                    validate(value.get(i), items, location + "[" + i + "]");
                }
            }
        }

        private void validateString(String value, Map<String, Object> schema, String location) {
            if (schema.get("minLength") instanceof Number min && value.length() < min.intValue()) {
                fail(location, "is shorter than " + min.intValue());
            }
            if (schema.get("maxLength") instanceof Number max && value.length() > max.intValue()) {
                fail(location, "is longer than " + max.intValue());
            }
            if (schema.get("pattern") instanceof String regex
                    && !Pattern.compile(regex).matcher(value).find()) {
                fail(location, "does not match pattern " + regex);
            }
        }

        private void validateNumber(Number value, Map<String, Object> schema, String location) {
            BigDecimal number = new BigDecimal(value.toString());
            if (schema.get("minimum") instanceof Number min
                    && number.compareTo(new BigDecimal(min.toString())) < 0) {
                fail(location, "is below minimum " + min);
            }
            if (schema.get("maximum") instanceof Number max
                    && number.compareTo(new BigDecimal(max.toString())) > 0) {
                fail(location, "is above maximum " + max);
            }
        }

        private Map<String, Object> resolve(String ref) {
            if (!ref.startsWith("#/")) {
                fail(ref, "only local schema references are supported");
            }
            Object current = document;
            for (String encoded : ref.substring(2).split("/")) {
                String key = encoded.replace("~1", "/").replace("~0", "~");
                if (!(current instanceof Map<?, ?>)) {
                    fail(ref, "unresolvable schema reference");
                }
                Map<?, ?> map = (Map<?, ?>) current;
                if (!map.containsKey(key)) {
                    fail(ref, "unresolvable schema reference");
                }
                current = map.get(key);
            }
            return schemaMap(current, ref);
        }

        private static boolean isInteger(Object value) {
            if (value instanceof Byte || value instanceof Short
                    || value instanceof Integer || value instanceof Long
                    || value instanceof BigInteger) {
                return true;
            }
            if (value instanceof BigDecimal decimal) {
                return decimal.stripTrailingZeros().scale() <= 0;
            }
            return false;
        }

        private static Map<String, Object> schemaMap(Object value, String location) {
            if (!(value instanceof Map<?, ?>)) {
                fail(location, "schema node is not an object");
            }
            return castMap((Map<?, ?>) value);
        }

        private static String stringValue(Object value, String location) {
            if (!(value instanceof String)) {
                fail(location, "schema value is not a string");
            }
            return (String) value;
        }

        @SuppressWarnings("unchecked")
        private static Map<String, Object> castMap(Map<?, ?> map) {
            return (Map<String, Object>) map;
        }

        private static void fail(String location, String message) {
            throw new SchemaFailure(location + ": " + message);
        }
    }

    private static final class SchemaFailure extends RuntimeException {
        private SchemaFailure(String message) {
            super(message);
        }
    }

    private static final class Json {
        private final String source;
        private int index;

        private Json(String source) {
            this.source = source;
        }

        private static Object parse(String source) {
            Json parser = new Json(source);
            Object value = parser.value();
            parser.space();
            if (parser.index != source.length()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        private Object value() {
            space();
            if (index >= source.length()) {
                throw error("unexpected end of input");
            }
            return switch (source.charAt(index)) {
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
            space();
            if (take('}')) {
                return result;
            }
            while (true) {
                space();
                String key = string();
                space();
                expect(':');
                result.put(key, value());
                space();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> array() {
            expect('[');
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
                expect(',');
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < source.length()) {
                char character = source.charAt(index++);
                if (character == '"') {
                    return result.toString();
                }
                if (character == '\\') {
                    if (index >= source.length()) {
                        throw error("unfinished escape");
                    }
                    char escape = source.charAt(index++);
                    switch (escape) {
                        case '"', '\\', '/' -> result.append(escape);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicode());
                        default -> throw error("invalid escape " + escape);
                    }
                } else {
                    if (character < 0x20) {
                        throw error("control character in string");
                    }
                    result.append(character);
                }
            }
            throw error("unterminated string");
        }

        private char unicode() {
            if (index + 4 > source.length()) {
                throw error("unfinished unicode escape");
            }
            try {
                char value = (char) Integer.parseInt(source.substring(index, index + 4), 16);
                index += 4;
                return value;
            } catch (NumberFormatException failure) {
                throw error("invalid unicode escape");
            }
        }

        private Object number() {
            int start = index;
            if (take('-')) {
                // sign consumed
            }
            digits();
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                digits();
            }
            if (index < source.length() && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                decimal = true;
                index++;
                if (index < source.length() && (source.charAt(index) == '+' || source.charAt(index) == '-')) {
                    index++;
                }
                digits();
            }
            String token = source.substring(start, index);
            try {
                return decimal ? new BigDecimal(token) : Long.valueOf(token);
            } catch (NumberFormatException failure) {
                throw error("invalid number " + token);
            }
        }

        private void digits() {
            int start = index;
            while (index < source.length() && Character.isDigit(source.charAt(index))) {
                index++;
            }
            if (start == index) {
                throw error("expected digit");
            }
        }

        private Object literal(String token, Object value) {
            if (!source.startsWith(token, index)) {
                throw error("expected " + token);
            }
            index += token.length();
            return value;
        }

        private void space() {
            while (index < source.length() && Character.isWhitespace(source.charAt(index))) {
                index++;
            }
        }

        private boolean take(char expected) {
            if (index < source.length() && source.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                throw error("expected '" + expected + "'");
            }
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at JSON offset " + index);
        }
    }
}
