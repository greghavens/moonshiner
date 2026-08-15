import java.math.BigDecimal;
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
import java.util.regex.PatternSyntaxException;

/** Protected acceptance harness. It performs no network I/O. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) {
        try {
            if (args.length != 1) {
                fail("expected repository root argument");
            }
            run(Path.of(args[0]));
            System.out.println("all checks passed");
        } catch (Throwable error) {
            System.err.println("verification failed: " + error.getMessage());
            System.exit(1);
        }
    }

    private static void run(Path root) throws Exception {
        String inventoryText = Files.readString(root.resolve("fixtures/estate-inventory.json"));
        String artifactText = VcfMigrationPlanner.generate(inventoryText);
        if (artifactText == null || artifactText.isBlank()) {
            fail("generate returned no artifact");
        }
        Object parsedArtifact = Json.parse(artifactText);
        Map<String, Object> artifact = object(parsedArtifact, "artifact");

        // This must remain the first semantic verification. The complete artifact is
        // validated as an SddcSpec; OpenAPI permits the brownfield extension fields.
        Map<String, Object> openapi = object(Json.parse(Files.readString(root.resolve(
                "specifications/vcf-installer/vcf-installer-openapi.json"))), "OpenAPI document");
        Map<String, Object> components = object(openapi.get("components"), "OpenAPI components");
        Map<String, Object> schemas = object(components.get("schemas"), "OpenAPI schemas");
        Object sddcSchema = schemas.get("SddcSpec");
        if (sddcSchema == null) {
            fail("OpenAPI document has no components.schemas.SddcSpec");
        }
        assertSchemaValid(artifact, sddcSchema, openapi, "installer SddcSpec");

        // Only after the installer-schema check may the brownfield contract and its
        // deterministic fixture authorities be loaded and evaluated.
        Map<String, Object> migrationSchema = object(Json.parse(Files.readString(root.resolve(
                "specifications/vcf-migration-plan.schema.json"))), "migration schema");
        assertSchemaValid(artifact, migrationSchema, migrationSchema, "migration-plan schema");

        Map<String, Object> inventory = object(Json.parse(inventoryText), "inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(root.resolve(
                "fixtures/compatibility-snapshot.json"))), "compatibility snapshot");
        verifySddcValues(artifact, inventory);
        verifyMigration(artifact, inventory, snapshot);
        verifyEdgeArchitecture(artifact, inventory, snapshot);
    }

    private static void assertSchemaValid(Object instance, Object schema, Object root,
                                          String label) {
        List<String> errors = new ArrayList<>();
        SchemaValidator.validate(instance, schema, root, "$", errors);
        if (!errors.isEmpty()) {
            fail(label + " validation failed: " + String.join("; ", errors));
        }
    }

    private static void verifySddcValues(Map<String, Object> artifact,
                                         Map<String, Object> inventory) {
        equal("1.0", artifact.get("artifactSchemaVersion"), "artifactSchemaVersion");
        equal(text(inventory, "estateId"), artifact.get("estateId"), "estateId");
        equal(text(inventory, "targetVcfVersion"), artifact.get("targetVersion"),
                "targetVersion");
        equal(text(inventory, "targetVcfVersion"), artifact.get("version"), "SddcSpec version");
        equal(text(inventory, "targetSddcId"), artifact.get("sddcId"), "sddcId");
        equal(text(inventory, "workflowType"), artifact.get("workflowType"), "workflowType");

        Map<String, Object> expectedDns = object(inventory.get("dns"), "inventory dns");
        Map<String, Object> actualDns = object(artifact.get("dnsSpec"), "dnsSpec");
        equal(expectedDns.get("subdomain"), actualDns.get("subdomain"), "dnsSpec.subdomain");
        equal(expectedDns.get("nameservers"), actualDns.get("nameservers"),
                "dnsSpec.nameservers");

        Map<String, Object> expectedVcenter = object(inventory.get("vcenter"), "inventory vcenter");
        Map<String, Object> actualVcenter = object(artifact.get("vcenterSpec"), "vcenterSpec");
        equal(expectedVcenter.get("hostname"), actualVcenter.get("vcenterHostname"),
                "vcenterSpec.vcenterHostname");
        equal(expectedVcenter.get("rootPassword"), actualVcenter.get("rootVcenterPassword"),
                "vcenterSpec.rootVcenterPassword");
        equal(expectedVcenter.get("sslThumbprint"), actualVcenter.get("sslThumbprint"),
                "vcenterSpec.sslThumbprint");
        equal(Boolean.TRUE, actualVcenter.get("useExistingDeployment"),
                "vcenterSpec.useExistingDeployment");

        List<Object> expectedNetworks = array(inventory.get("networks"), "inventory networks");
        List<Object> actualNetworks = array(artifact.get("networkSpecs"), "networkSpecs");
        equal(expectedNetworks.size(), actualNetworks.size(), "networkSpecs count");
        Map<String, Map<String, Object>> actualByType = new HashMap<>();
        for (Object value : actualNetworks) {
            Map<String, Object> network = object(value, "networkSpec");
            String type = text(network, "networkType");
            if (actualByType.put(type, network) != null) {
                fail("duplicate networkSpec for " + type);
            }
        }
        for (Object value : expectedNetworks) {
            Map<String, Object> expected = object(value, "inventory network");
            String type = text(expected, "networkType");
            Map<String, Object> actual = actualByType.get(type);
            if (actual == null) {
                fail("missing networkSpec " + type);
            }
            for (String field : List.of("networkType", "vlanId", "subnet", "gateway", "mtu")) {
                equal(expected.get(field), actual.get(field), "networkSpec " + type + "." + field);
            }
        }
    }

    private static void verifyMigration(Map<String, Object> artifact,
                                        Map<String, Object> inventory,
                                        Map<String, Object> snapshot) {
        String source = text(inventory, "currentVcfVersion");
        String target = text(inventory, "targetVcfVersion");
        equal(source, snapshot.get("sourceVcfVersion"), "snapshot source VCF version");
        equal(target, snapshot.get("targetVcfVersion"), "snapshot target VCF version");

        List<Object> supportedHops = array(snapshot.get("supportedVcfHops"), "supportedVcfHops");
        boolean directSupported = false;
        for (Object value : supportedHops) {
            Map<String, Object> hop = object(value, "supported VCF hop");
            if (source.equals(hop.get("from")) && target.equals(hop.get("to"))) {
                directSupported = true;
            }
        }
        if (!directSupported) {
            fail("snapshot does not authorize requested direct VCF hop");
        }
        equal(List.of(source, target), artifact.get("versionPath"), "versionPath");

        List<Object> inventoryComponents = array(inventory.get("components"), "inventory components");
        Map<String, Map<String, Object>> inventoryById = new HashMap<>();
        for (Object value : inventoryComponents) {
            Map<String, Object> component = object(value, "inventory component");
            String id = text(component, "componentId");
            if (inventoryById.put(id, component) != null) {
                fail("inventory contains duplicate componentId " + id);
            }
        }

        Map<String, Object> targets = object(snapshot.get("targetComponents"), "targetComponents");
        List<Object> sequence = array(snapshot.get("migrationSequence"), "migrationSequence");
        List<Object> plan = array(artifact.get("migrationPlan"), "migrationPlan");
        equal(inventoryComponents.size(), plan.size(), "migrationPlan component count");
        equal(sequence.size(), plan.size(), "migrationPlan sequence count");

        Set<String> seen = new HashSet<>();
        for (int index = 0; index < sequence.size(); index++) {
            Map<String, Object> authority = object(sequence.get(index), "snapshot sequence step");
            Map<String, Object> actual = object(plan.get(index), "migrationPlan step");
            long expectedOrder = index + 1L;
            equal(expectedOrder, integer(actual, "order"), "migration step order");
            equal(expectedOrder, integer(authority, "order"), "snapshot step order");
            String id = text(authority, "componentId");
            equal(id, actual.get("componentId"), "migration componentId at order " + expectedOrder);
            if (!seen.add(id)) {
                fail("migrationPlan repeats componentId " + id);
            }
            Map<String, Object> current = inventoryById.get(id);
            if (current == null) {
                fail("snapshot sequence names unknown componentId " + id);
            }
            equal(current.get("component"), actual.get("component"), id + " component name");
            equal(current.get("currentVersion"), actual.get("currentVersion"),
                    id + " currentVersion");
            equal(targets.get(id), actual.get("targetVersion"), id + " targetVersion");
            equal(authority.get("action"), actual.get("action"), id + " action");
            equal(authority.get("gates"), actual.get("gates"), id + " gates");
        }
        equal(inventoryById.keySet(), seen, "migrationPlan component coverage");
    }

    private static void verifyEdgeArchitecture(Map<String, Object> artifact,
                                               Map<String, Object> inventory,
                                               Map<String, Object> snapshot) {
        Map<String, Object> requirement = object(inventory.get("edgeRequirement"),
                "edgeRequirement");
        Map<String, Object> edge = object(artifact.get("edgeArchitecture"),
                "edgeArchitecture");
        long throughput = integer(requirement, "northSouthGbpsPerActiveEdge");
        equal(throughput, integer(edge, "requiredThroughputGbpsPerActiveEdge"),
                "Edge throughput");
        equal(integer(requirement, "edgeNodeCount"), integer(edge, "nodeCount"),
                "Edge nodeCount");
        equal(requirement.get("routingMode"), edge.get("routingMode"), "Edge routingMode");

        String expectedFactor = null;
        long bestCapacity = Long.MAX_VALUE;
        for (Object value : array(snapshot.get("edgeSizingBands"), "edgeSizingBands")) {
            Map<String, Object> band = object(value, "edge sizing band");
            long capacity = integer(band, "maxRecommendedGbps");
            if (capacity >= throughput && capacity < bestCapacity) {
                bestCapacity = capacity;
                expectedFactor = text(band, "formFactor");
            }
        }
        if (expectedFactor == null) {
            fail("no pinned Edge form factor meets throughput");
        }
        equal(expectedFactor, edge.get("formFactor"), "Edge formFactor");

        long expectedSpeed = Long.MAX_VALUE;
        for (Object value : array(requirement.get("availableUplinkSpeedsGbps"),
                "availableUplinkSpeedsGbps")) {
            long speed = wholeNumber(value, "uplink speed");
            if (speed >= throughput && speed < expectedSpeed) {
                expectedSpeed = speed;
            }
        }
        if (expectedSpeed == Long.MAX_VALUE) {
            fail("no available uplink can carry required failover throughput");
        }

        Map<String, Object> layout = object(edge.get("uplinkLayout"), "uplinkLayout");
        equal(2L, integer(layout, "perNodeUplinks"), "perNodeUplinks");
        equal(expectedSpeed, integer(layout, "linkSpeedGbps"), "linkSpeedGbps");
        equal(requirement.get("routingMode"), layout.get("mode"), "uplink mode");
        if (!Boolean.TRUE.equals(requirement.get("maintainThroughputOnSingleUplinkFailure"))) {
            fail("fixture must require single-uplink failure survival");
        }

        List<Object> domains = array(requirement.get("failureDomains"), "failureDomains");
        List<Object> uplinks = array(layout.get("uplinks"), "uplinks");
        equal(domains.size(), uplinks.size(), "uplink failure-domain count");
        Set<String> expectedDomains = new HashSet<>();
        for (Object domain : domains) {
            if (!expectedDomains.add(String.valueOf(domain))) {
                fail("fixture contains duplicate Edge failure domains");
            }
        }
        Set<String> seenDomains = new HashSet<>();
        for (Object value : uplinks) {
            Map<String, Object> uplink = object(value, "uplink");
            String domain = text(uplink, "fabric");
            if (!expectedDomains.contains(domain)) {
                fail("uplink uses unknown failure domain " + domain);
            }
            equal(expectedSpeed, integer(uplink, "speedGbps"), "uplink speedGbps");
            if (!seenDomains.add(domain)) {
                fail("uplinks do not use independent failure domains");
            }
        }
        equal(expectedDomains, seenDomains, "uplink failure-domain coverage");
    }

    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?>)) {
            fail(label + " must be an object");
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) value;
        return result;
    }

    private static List<Object> array(Object value, String label) {
        if (!(value instanceof List<?>)) {
            fail(label + " must be an array");
        }
        @SuppressWarnings("unchecked")
        List<Object> result = (List<Object>) value;
        return result;
    }

    private static String text(Map<String, Object> object, String field) {
        Object value = object.get(field);
        if (!(value instanceof String) || ((String) value).isEmpty()) {
            fail(field + " must be a non-empty string");
        }
        return (String) value;
    }

    private static long integer(Map<String, Object> object, String field) {
        return wholeNumber(object.get(field), field);
    }

    private static long wholeNumber(Object value, String label) {
        if (!(value instanceof Number)) {
            fail(label + " must be an integer");
        }
        try {
            return new BigDecimal(value.toString()).longValueExact();
        } catch (ArithmeticException error) {
            fail(label + " must be an integer");
            return 0;
        }
    }

    private static void equal(Object expected, Object actual, String label) {
        if (!deepEqual(expected, actual)) {
            fail(label + " mismatch: expected " + expected + " but got " + actual);
        }
    }

    private static boolean deepEqual(Object left, Object right) {
        if (left instanceof Number a && right instanceof Number b) {
            return new BigDecimal(a.toString()).compareTo(new BigDecimal(b.toString())) == 0;
        }
        return left == null ? right == null : left.equals(right);
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(Object instance, Object schemaValue, Object root,
                             String path, List<String> errors) {
            if (schemaValue instanceof Boolean bool) {
                if (!bool) {
                    errors.add(path + " is rejected by a false schema");
                }
                return;
            }
            if (!(schemaValue instanceof Map<?, ?>)) {
                errors.add(path + " has an invalid schema node");
                return;
            }
            Map<String, Object> schema = object(schemaValue, "schema node");
            if (schema.get("$ref") instanceof String reference) {
                validate(instance, resolveReference(root, reference), root, path, errors);
                return;
            }

            validateSubschemas(instance, schema, root, path, errors);
            if (schema.containsKey("const") && !deepEqual(schema.get("const"), instance)) {
                errors.add(path + " does not equal const value");
            }
            if (schema.get("enum") instanceof List<?> allowed) {
                boolean found = false;
                for (Object candidate : allowed) {
                    found |= deepEqual(candidate, instance);
                }
                if (!found) {
                    errors.add(path + " is not in enum");
                }
            }

            Object typeSpec = schema.get("type");
            if (typeSpec != null && !matchesType(instance, typeSpec)) {
                errors.add(path + " must have type " + typeSpec);
                return;
            }
            if (instance == null) {
                return;
            }
            if (instance instanceof Map<?, ?> rawObject) {
                validateObject(object(rawObject, path), schema, root, path, errors);
            } else if (instance instanceof List<?> rawArray) {
                validateArray(rawArray, schema, root, path, errors);
            } else if (instance instanceof String text) {
                validateString(text, schema, path, errors);
            } else if (instance instanceof Number number) {
                validateNumber(number, schema, path, errors);
            }
        }

        private static void validateSubschemas(Object instance, Map<String, Object> schema,
                                               Object root, String path,
                                               List<String> errors) {
            if (schema.get("allOf") instanceof List<?> allOf) {
                for (Object child : allOf) {
                    validate(instance, child, root, path, errors);
                }
            }
            if (schema.get("anyOf") instanceof List<?> anyOf) {
                int matches = matchingSubschemas(instance, anyOf, root, path);
                if (matches == 0) {
                    errors.add(path + " does not match anyOf");
                }
            }
            if (schema.get("oneOf") instanceof List<?> oneOf) {
                int matches = matchingSubschemas(instance, oneOf, root, path);
                if (matches != 1) {
                    errors.add(path + " must match exactly one oneOf branch");
                }
            }
        }

        private static int matchingSubschemas(Object instance, List<?> children,
                                              Object root, String path) {
            int matches = 0;
            for (Object child : children) {
                List<String> trial = new ArrayList<>();
                validate(instance, child, root, path, trial);
                if (trial.isEmpty()) {
                    matches++;
                }
            }
            return matches;
        }

        private static void validateObject(Map<String, Object> instance,
                                           Map<String, Object> schema, Object root,
                                           String path, List<String> errors) {
            if (schema.get("required") instanceof List<?> required) {
                for (Object name : required) {
                    if (name instanceof String field && !instance.containsKey(field)) {
                        errors.add(path + " is missing required property " + field);
                    }
                }
            }
            Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?> raw
                    ? object(raw, "schema properties") : Map.of();
            for (Map.Entry<String, Object> entry : properties.entrySet()) {
                if (instance.containsKey(entry.getKey())) {
                    validate(instance.get(entry.getKey()), entry.getValue(), root,
                            path + "." + entry.getKey(), errors);
                }
            }
            Object additional = schema.get("additionalProperties");
            if (Boolean.FALSE.equals(additional)) {
                for (String field : instance.keySet()) {
                    if (!properties.containsKey(field)) {
                        errors.add(path + " contains additional property " + field);
                    }
                }
            } else if (additional instanceof Map<?, ?> || additional instanceof Boolean) {
                for (Map.Entry<String, Object> entry : instance.entrySet()) {
                    if (!properties.containsKey(entry.getKey())) {
                        validate(entry.getValue(), additional, root,
                                path + "." + entry.getKey(), errors);
                    }
                }
            }
        }

        private static void validateArray(List<?> instance, Map<String, Object> schema,
                                          Object root, String path, List<String> errors) {
            if (schema.get("minItems") instanceof Number minimum
                    && instance.size() < minimum.intValue()) {
                errors.add(path + " has fewer than minItems");
            }
            if (schema.get("maxItems") instanceof Number maximum
                    && instance.size() > maximum.intValue()) {
                errors.add(path + " has more than maxItems");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                for (int i = 0; i < instance.size(); i++) {
                    for (int j = i + 1; j < instance.size(); j++) {
                        if (deepEqual(instance.get(i), instance.get(j))) {
                            errors.add(path + " has duplicate items");
                        }
                    }
                }
            }
            if (schema.containsKey("items")) {
                for (int index = 0; index < instance.size(); index++) {
                    validate(instance.get(index), schema.get("items"), root,
                            path + "[" + index + "]", errors);
                }
            }
        }

        private static void validateString(String instance, Map<String, Object> schema,
                                           String path, List<String> errors) {
            int length = instance.codePointCount(0, instance.length());
            if (schema.get("minLength") instanceof Number minimum
                    && length < minimum.intValue()) {
                errors.add(path + " is shorter than minLength");
            }
            if (schema.get("maxLength") instanceof Number maximum
                    && length > maximum.intValue()) {
                errors.add(path + " is longer than maxLength");
            }
            if (schema.get("pattern") instanceof String pattern) {
                try {
                    if (!Pattern.compile(pattern).matcher(instance).find()) {
                        errors.add(path + " does not match pattern");
                    }
                } catch (PatternSyntaxException error) {
                    errors.add(path + " schema contains an invalid pattern");
                }
            }
        }

        private static void validateNumber(Number instance, Map<String, Object> schema,
                                           String path, List<String> errors) {
            BigDecimal actual = new BigDecimal(instance.toString());
            if (schema.get("minimum") instanceof Number minimum
                    && actual.compareTo(new BigDecimal(minimum.toString())) < 0) {
                errors.add(path + " is below minimum");
            }
            if (schema.get("maximum") instanceof Number maximum
                    && actual.compareTo(new BigDecimal(maximum.toString())) > 0) {
                errors.add(path + " is above maximum");
            }
        }

        private static boolean matchesType(Object instance, Object typeSpec) {
            if (typeSpec instanceof List<?> types) {
                for (Object type : types) {
                    if (matchesType(instance, type)) {
                        return true;
                    }
                }
                return false;
            }
            if (!(typeSpec instanceof String type)) {
                return false;
            }
            return switch (type) {
                case "null" -> instance == null;
                case "object" -> instance instanceof Map<?, ?>;
                case "array" -> instance instanceof List<?>;
                case "string" -> instance instanceof String;
                case "boolean" -> instance instanceof Boolean;
                case "number" -> instance instanceof Number;
                case "integer" -> instance instanceof Number number && isInteger(number);
                default -> true;
            };
        }

        private static boolean isInteger(Number number) {
            try {
                new BigDecimal(number.toString()).toBigIntegerExact();
                return true;
            } catch (ArithmeticException error) {
                return false;
            }
        }

        private static Object resolveReference(Object root, String reference) {
            if (!reference.startsWith("#/")) {
                fail("only local schema references are supported: " + reference);
            }
            Object current = root;
            for (String encoded : reference.substring(2).split("/", -1)) {
                String token = encoded.replace("~1", "/").replace("~0", "~");
                Map<String, Object> currentObject = object(current, "schema reference " + reference);
                if (!currentObject.containsKey(token)) {
                    fail("unresolved schema reference " + reference);
                }
                current = currentObject.get(token);
            }
            return current;
        }
    }

    private static final class Json {
        private final String input;
        private int offset;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            if (input == null) {
                fail("JSON input is null");
            }
            Json parser = new Json(input);
            Object value = parser.value();
            parser.whitespace();
            if (parser.offset != input.length()) {
                fail("trailing JSON data at offset " + parser.offset);
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (offset >= input.length()) {
                fail("unexpected end of JSON");
            }
            return switch (input.charAt(offset)) {
                case '{' -> objectValue();
                case '[' -> arrayValue();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> objectValue() {
            offset++;
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (offset >= input.length() || input.charAt(offset) != '"') {
                    fail("expected object key at offset " + offset);
                }
                String key = string();
                whitespace();
                expect(':');
                Object value = value();
                if (result.containsKey(key)) {
                    fail("duplicate JSON key " + key);
                }
                result.put(key, value);
                whitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            offset++;
            ArrayList<Object> result = new ArrayList<>();
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
            while (offset < input.length()) {
                char character = input.charAt(offset++);
                if (character == '"') {
                    return result.toString();
                }
                if (character == '\\') {
                    if (offset >= input.length()) {
                        fail("unfinished JSON escape");
                    }
                    char escape = input.charAt(offset++);
                    switch (escape) {
                        case '"', '\\', '/' -> result.append(escape);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (offset + 4 > input.length()) {
                                fail("unfinished Unicode escape");
                            }
                            try {
                                result.append((char) Integer.parseInt(input.substring(offset,
                                        offset + 4), 16));
                            } catch (NumberFormatException error) {
                                fail("invalid Unicode escape");
                            }
                            offset += 4;
                        }
                        default -> fail("invalid JSON escape at offset " + (offset - 1));
                    }
                } else {
                    if (character < 0x20) {
                        fail("unescaped control character in JSON string");
                    }
                    result.append(character);
                }
            }
            fail("unterminated JSON string");
            return null;
        }

        private Object number() {
            int start = offset;
            if (take('-')) {
                // sign consumed
            }
            if (take('0')) {
                if (offset < input.length() && Character.isDigit(input.charAt(offset))) {
                    fail("leading zero in JSON number");
                }
            } else {
                digits();
            }
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                digits();
            }
            if (offset < input.length() && (input.charAt(offset) == 'e'
                    || input.charAt(offset) == 'E')) {
                decimal = true;
                offset++;
                if (offset < input.length() && (input.charAt(offset) == '+'
                        || input.charAt(offset) == '-')) {
                    offset++;
                }
                digits();
            }
            String token = input.substring(start, offset);
            try {
                return decimal ? new BigDecimal(token) : Long.valueOf(token);
            } catch (NumberFormatException error) {
                fail("invalid JSON number at offset " + start);
                return null;
            }
        }

        private void digits() {
            int start = offset;
            while (offset < input.length() && Character.isDigit(input.charAt(offset))) {
                offset++;
            }
            if (start == offset) {
                fail("expected digit at offset " + offset);
            }
        }

        private Object literal(String token, Object value) {
            if (!input.startsWith(token, offset)) {
                fail("invalid JSON token at offset " + offset);
            }
            offset += token.length();
            return value;
        }

        private void whitespace() {
            while (offset < input.length()) {
                char character = input.charAt(offset);
                if (character != ' ' && character != '\n' && character != '\r'
                        && character != '\t') {
                    return;
                }
                offset++;
            }
        }

        private boolean take(char expected) {
            if (offset < input.length() && input.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                fail("expected '" + expected + "' at offset " + offset);
            }
        }
    }
}
