import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Deterministic verifier. It performs no network access and never grades search behavior. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        Path root = args.length == 0 ? Path.of(".") : Path.of(args[0]);
        Path inventoryPath = root.resolve("files/estate-inventory.json");
        Path snapshotPath = root.resolve("files/compatibility-snapshot.json");
        Path specPath = root.resolve("files/installer-spec.json");

        Map<String, Object> inventory = object(Json.parse(Files.readString(inventoryPath, StandardCharsets.UTF_8)), "inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(snapshotPath, StandardCharsets.UTF_8)), "snapshot");
        Map<String, Object> installerSpec = object(Json.parse(Files.readString(specPath, StandardCharsets.UTF_8)), "installer spec");

        String artifactText = MigrationPlanClient.buildPlan(inventoryPath, snapshotPath, specPath);
        Object artifactValue = Json.parse(artifactText);

        // Contract requirement: schema validation from the installer specification is the
        // first validation performed on the artifact. Semantic assertions start below.
        SchemaValidator.validate(artifactValue, installerSpec.get("schema"), "$");
        System.out.println("PASS installer schema");

        Map<String, Object> artifact = object(artifactValue, "artifact");
        requireEqual(snapshot.get("snapshotVersion"), artifact.get("snapshotVersion"), "snapshotVersion");
        requireEqual(inventory.get("estateId"), artifact.get("estateId"), "estateId");

        verifyResearchCoverage(artifact);
        verifySupportBoundaries(artifact, inventory, snapshot);
        verifyStorage(artifact, inventory, snapshot);
        verifyPlacements(artifact, snapshot);
        verifyMigrations(artifact, inventory, snapshot);
        verifyStepsAndGates(artifact, snapshot);

        // Deliberately do not fetch, rank, whitelist, or otherwise constrain source choice.
        System.out.println("PASS deterministic architecture checks");
        System.out.println("PASS research topic coverage; verifier made no network requests");
    }

    private static void verifyResearchCoverage(Map<String, Object> artifact) {
        Set<String> coveredTopics = new LinkedHashSet<>();
        for (Object sourceValue : array(artifact.get("researchSources"), "researchSources")) {
            Map<String, Object> source = object(sourceValue, "research source");
            coveredTopics.addAll(stringList(source.get("topics"), "research source topics"));
        }
        Set<String> requiredTopics = new LinkedHashSet<>(List.of(
                "migration-path", "content-compatibility", "support-boundary", "storage-networking"));
        require(coveredTopics.containsAll(requiredTopics),
                "research sources must cover " + requiredTopics + " but covered " + coveredTopics);
        System.out.println("PASS required research topics recorded");
    }

    private static void verifySupportBoundaries(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> actual = indexBy(array(artifact.get("supportBoundaries"), "supportBoundaries"), "sourceKey");
        Map<String, Map<String, Object>> expected = indexBy(array(snapshot.get("migrations"), "snapshot.migrations"), "key");
        Map<String, Map<String, Object>> products = indexBy(array(inventory.get("products"), "inventory.products"), "key");
        requireExactKeys(expected.keySet(), actual.keySet(), "support boundary source keys");

        LocalDate inventoryDate = LocalDate.parse(string(inventory.get("inventoryDate"), "inventoryDate"));
        for (String key : expected.keySet()) {
            Map<String, Object> got = actual.get(key);
            Map<String, Object> rule = expected.get(key);
            Map<String, Object> product = products.get(key);
            requireEqual(product.get("product"), got.get("product"), key + " support product");
            requireEqual(product.get("version"), got.get("version"), key + " support version");
            requireEqual(rule.get("endOfGeneralSupport"), got.get("endOfGeneralSupport"), key + " EOGS");
            LocalDate eogs = LocalDate.parse(string(got.get("endOfGeneralSupport"), key + " EOGS"));
            String expectedState = inventoryDate.isAfter(eogs) ? "ended" : "active";
            requireEqual(expectedState, got.get("stateAtInventory"), key + " support state");
        }
        System.out.println("PASS support boundaries");
    }

    private static void verifyStorage(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> decision = object(artifact.get("storageDecision"), "storageDecision");
        requireEqual(snapshot.get("selectedStorage"), decision.get("selected"), "selected storage");

        Map<String, Map<String, Object>> actualOptions = indexBy(array(decision.get("options"), "storage options"), "architecture");
        Map<String, Map<String, Object>> expectedOptions = indexBy(array(snapshot.get("storageOptions"), "snapshot storage options"), "architecture");
        requireExactKeys(expectedOptions.keySet(), actualOptions.keySet(), "storage option architectures");
        List<String> optionFields = List.of(
                "hostProfile", "hostCount", "rawCapacityTiB", "usableCapacityTiB", "policy",
                "nicSpeedGbps", "nicPortsPerHost", "rackFits", "hclEligible");
        for (String architecture : expectedOptions.keySet()) {
            for (String field : optionFields) {
                requireEqual(expectedOptions.get(architecture).get(field), actualOptions.get(architecture).get(field),
                        architecture + " option " + field);
            }
        }

        Map<String, Object> constraints = object(inventory.get("managementDomainConstraints"), "managementDomainConstraints");
        String selectedName = string(decision.get("selected"), "selected storage");
        Map<String, Object> selected = actualOptions.get(selectedName);
        require(bool(selected.get("rackFits"), "selected rackFits"), "selected storage does not fit the available rack slots");
        require(bool(selected.get("hclEligible"), "selected hclEligible"), "selected storage is not HCL eligible");
        require(decimal(selected.get("hostCount"), "selected hostCount").compareTo(decimal(constraints.get("rackSlotsAvailable"), "rack slots")) <= 0,
                "selected host count exceeds available rack slots");
        require(decimal(selected.get("usableCapacityTiB"), "selected usable").compareTo(decimal(constraints.get("requiredUsableTiB"), "required usable")) >= 0,
                "selected storage misses usable capacity requirement");

        Set<String> actualChanges = indexBy(array(decision.get("networkChanges"), "network changes"), "id").keySet();
        Set<String> expectedChanges = new LinkedHashSet<>(stringList(snapshot.get("requiredNetworkChanges"), "required network changes"));
        requireExactKeys(expectedChanges, actualChanges, "network changes");
        System.out.println("PASS OSA/ESA decision and network design");
    }

    private static void verifyPlacements(Map<String, Object> artifact, Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> actual = indexBy(array(artifact.get("placements"), "placements"), "id");
        Map<String, Map<String, Object>> expected = indexBy(array(snapshot.get("placements"), "snapshot placements"), "id");
        requireExactKeys(expected.keySet(), actual.keySet(), "placement ids");
        List<String> fields = List.of(
                "component", "version", "site", "cluster", "resourcePool", "nodeCount",
                "size", "vCpuEach", "memoryGiBEach", "diskGiBEach");
        for (String id : expected.keySet()) {
            for (String field : fields) {
                requireEqual(expected.get(id).get(field), actual.get(id).get(field), id + " placement " + field);
            }
        }
        System.out.println("PASS component placement and sizing");
    }

    private static void verifyMigrations(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> actual = indexBy(array(artifact.get("componentMigrations"), "componentMigrations"), "sourceKey");
        Map<String, Map<String, Object>> expected = indexBy(array(snapshot.get("migrations"), "snapshot migrations"), "key");
        Map<String, Map<String, Object>> products = indexBy(array(inventory.get("products"), "inventory products"), "key");
        requireExactKeys(expected.keySet(), actual.keySet(), "migration source keys");

        for (String key : expected.keySet()) {
            Map<String, Object> got = actual.get(key);
            Map<String, Object> rule = expected.get(key);
            Map<String, Object> product = products.get(key);
            requireEqual(product.get("product"), got.get("sourceProduct"), key + " source product");
            requireEqual(product.get("version"), got.get("sourceVersion"), key + " source version");
            for (String field : List.of("targetComponent", "targetVersion", "method")) {
                requireEqual(rule.get(field), got.get(field), key + " migration " + field);
            }
            requireListEqual(stringList(rule.get("versionPath"), key + " expected version path"),
                    stringList(got.get("versionPath"), key + " version path"), key + " version path");

            Map<String, Map<String, Object>> dispositions = indexBy(array(got.get("contentDisposition"), key + " contentDisposition"), "item");
            Map<String, Map<String, Object>> contentRules = indexBy(array(rule.get("contentRules"), key + " content rules"), "item");
            List<Object> inventoriedContent = array(product.get("content"), key + " inventory content");
            Set<String> inventoryItems = new LinkedHashSet<>();
            for (Object item : inventoriedContent) {
                inventoryItems.add(string(object(item, key + " content item").get("id"), key + " content id"));
            }
            requireExactKeys(inventoryItems, dispositions.keySet(), key + " inventoried content coverage");
            requireExactKeys(contentRules.keySet(), dispositions.keySet(), key + " compatibility content coverage");
            for (String item : contentRules.keySet()) {
                requireEqual(contentRules.get(item).get("decision"), dispositions.get(item).get("decision"),
                        key + " disposition for " + item);
            }
        }
        System.out.println("PASS migration paths and content dispositions");
    }

    private static void verifyStepsAndGates(Map<String, Object> artifact, Map<String, Object> snapshot) {
        List<Object> actualSteps = array(artifact.get("steps"), "steps");
        List<Object> expectedSteps = array(snapshot.get("stepSequence"), "snapshot stepSequence");
        require(actualSteps.size() == expectedSteps.size(), "step count differs from pinned sequence");
        Set<String> requiredGateIds = new LinkedHashSet<>();
        for (int i = 0; i < expectedSteps.size(); i++) {
            Map<String, Object> got = object(actualSteps.get(i), "step " + (i + 1));
            Map<String, Object> expected = object(expectedSteps.get(i), "expected step " + (i + 1));
            requireEqual(expected.get("order"), got.get("order"), "step order at index " + i);
            requireEqual(expected.get("id"), got.get("id"), "step id at index " + i);
            requireListEqual(stringList(expected.get("componentKeys"), "expected componentKeys"),
                    stringList(got.get("componentKeys"), "componentKeys"), "componentKeys for " + expected.get("id"));
            List<String> expectedGates = stringList(expected.get("gateIds"), "expected gateIds");
            requireListEqual(expectedGates, stringList(got.get("gateIds"), "gateIds"), "gateIds for " + expected.get("id"));
            requiredGateIds.addAll(expectedGates);
        }
        Set<String> actualGateIds = indexBy(array(artifact.get("gates"), "gates"), "id").keySet();
        requireExactKeys(requiredGateIds, actualGateIds, "technical gate definitions");
        System.out.println("PASS ordered gated implementation plan");
    }

    private static Map<String, Map<String, Object>> indexBy(List<Object> values, String field) {
        Map<String, Map<String, Object>> indexed = new LinkedHashMap<>();
        for (int i = 0; i < values.size(); i++) {
            Map<String, Object> value = object(values.get(i), "array item " + i);
            String key = string(value.get(field), "field " + field);
            require(indexed.put(key, value) == null, "duplicate " + field + ": " + key);
        }
        return indexed;
    }

    private static List<String> stringList(Object value, String label) {
        List<String> result = new ArrayList<>();
        for (Object item : array(value, label)) {
            result.add(string(item, label + " item"));
        }
        return result;
    }

    private static void requireListEqual(List<String> expected, List<String> actual, String label) {
        require(expected.equals(actual), label + " expected " + expected + " but was " + actual);
    }

    private static void requireExactKeys(Set<String> expected, Set<String> actual, String label) {
        require(expected.equals(actual), label + " expected " + expected + " but was " + actual);
    }

    private static void requireEqual(Object expected, Object actual, String label) {
        boolean equal;
        if (expected instanceof BigDecimal || actual instanceof BigDecimal) {
            equal = expected instanceof BigDecimal && actual instanceof BigDecimal
                    && ((BigDecimal) expected).compareTo((BigDecimal) actual) == 0;
        } else {
            equal = expected == null ? actual == null : expected.equals(actual);
        }
        require(equal, label + " expected " + expected + " but was " + actual);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        require(value instanceof Map<?, ?>, label + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        require(value instanceof List<?>, label + " must be an array");
        return (List<Object>) value;
    }

    private static String string(Object value, String label) {
        require(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static boolean bool(Object value, String label) {
        require(value instanceof Boolean, label + " must be a boolean");
        return (Boolean) value;
    }

    private static BigDecimal decimal(Object value, String label) {
        require(value instanceof BigDecimal, label + " must be a number");
        return (BigDecimal) value;
    }

    private static final class SchemaValidator {
        static void validate(Object value, Object schemaValue, String path) {
            Map<String, Object> schema = object(schemaValue, "schema at " + path);
            Object type = schema.get("type");
            if (type != null) {
                validateType(value, string(type, "schema type at " + path), path);
            }
            if (schema.containsKey("const")) {
                requireEqual(schema.get("const"), value, path + " const");
            }
            if (schema.containsKey("enum")) {
                boolean found = false;
                for (Object candidate : array(schema.get("enum"), "enum at " + path)) {
                    try {
                        requireEqual(candidate, value, path + " enum candidate");
                        found = true;
                        break;
                    } catch (AssertionError ignored) {
                        // Try the next enum member.
                    }
                }
                require(found, path + " is not one of the allowed enum values");
            }

            if (value instanceof Map<?, ?>) {
                validateObject(object(value, path), schema, path);
            } else if (value instanceof List<?>) {
                validateArray(array(value, path), schema, path);
            } else if (value instanceof String) {
                validateString((String) value, schema, path);
            } else if (value instanceof BigDecimal) {
                validateNumber((BigDecimal) value, schema, path);
            }
        }

        private static void validateType(Object value, String type, String path) {
            boolean valid = switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "number" -> value instanceof BigDecimal;
                case "integer" -> value instanceof BigDecimal && ((BigDecimal) value).stripTrailingZeros().scale() <= 0;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported schema type " + type + " at " + path);
            };
            require(valid, path + " must have schema type " + type);
        }

        private static void validateObject(Map<String, Object> value, Map<String, Object> schema, String path) {
            if (schema.containsKey("required")) {
                for (String key : stringList(schema.get("required"), "required at " + path)) {
                    require(value.containsKey(key), path + " is missing required property " + key);
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), "properties at " + path)
                    : Map.of();
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : value.keySet()) {
                    require(properties.containsKey(key), path + " has additional property " + key);
                }
            }
            for (Map.Entry<String, Object> property : properties.entrySet()) {
                if (value.containsKey(property.getKey())) {
                    validate(value.get(property.getKey()), property.getValue(), path + "." + property.getKey());
                }
            }
        }

        private static void validateArray(List<Object> value, Map<String, Object> schema, String path) {
            if (schema.containsKey("minItems")) {
                int minimum = decimal(schema.get("minItems"), "minItems at " + path).intValueExact();
                require(value.size() >= minimum, path + " must have at least " + minimum + " items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                require(new HashSet<>(value).size() == value.size(), path + " items must be unique");
            }
            if (schema.containsKey("items")) {
                for (int i = 0; i < value.size(); i++) {
                    validate(value.get(i), schema.get("items"), path + "[" + i + "]");
                }
            }
        }

        private static void validateString(String value, Map<String, Object> schema, String path) {
            if (schema.containsKey("minLength")) {
                int minimum = decimal(schema.get("minLength"), "minLength at " + path).intValueExact();
                require(value.length() >= minimum, path + " must have length >= " + minimum);
            }
            if (schema.containsKey("pattern")) {
                String regex = string(schema.get("pattern"), "pattern at " + path);
                require(Pattern.compile(regex).matcher(value).matches(), path + " does not match " + regex);
            }
        }

        private static void validateNumber(BigDecimal value, Map<String, Object> schema, String path) {
            if (schema.containsKey("minimum")) {
                BigDecimal minimum = decimal(schema.get("minimum"), "minimum at " + path);
                require(value.compareTo(minimum) >= 0, path + " must be >= " + minimum);
            }
        }
    }

    private static final class Json {
        static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.value();
            parser.whitespace();
            require(parser.atEnd(), "unexpected trailing JSON at character " + parser.position());
            return value;
        }

        private static final class Parser {
            private final String text;
            private int position;

            Parser(String text) {
                this.text = text;
            }

            int position() {
                return position;
            }

            boolean atEnd() {
                return position == text.length();
            }

            void whitespace() {
                while (!atEnd() && Character.isWhitespace(text.charAt(position))) {
                    position++;
                }
            }

            Object value() {
                whitespace();
                require(!atEnd(), "unexpected end of JSON");
                char current = text.charAt(position);
                return switch (current) {
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
                expect('{');
                Map<String, Object> result = new LinkedHashMap<>();
                whitespace();
                if (take('}')) {
                    return result;
                }
                while (true) {
                    whitespace();
                    require(!atEnd() && text.charAt(position) == '"', "object key must be a string at " + position);
                    String key = stringValue();
                    require(!result.containsKey(key), "duplicate JSON key " + key);
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

            private List<Object> arrayValue() {
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

            private String stringValue() {
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!atEnd()) {
                    char current = text.charAt(position++);
                    if (current == '"') {
                        return result.toString();
                    }
                    if (current == '\\') {
                        require(!atEnd(), "unterminated JSON escape");
                        char escaped = text.charAt(position++);
                        switch (escaped) {
                            case '"', '\\', '/' -> result.append(escaped);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> {
                                require(position + 4 <= text.length(), "short unicode escape");
                                result.append((char) Integer.parseInt(text.substring(position, position + 4), 16));
                                position += 4;
                            }
                            default -> throw new AssertionError("invalid JSON escape at " + (position - 1));
                        }
                    } else {
                        require(current >= 0x20, "control character in JSON string");
                        result.append(current);
                    }
                }
                throw new AssertionError("unterminated JSON string");
            }

            private Object literal(String token, Object value) {
                require(text.startsWith(token, position), "expected " + token + " at " + position);
                position += token.length();
                return value;
            }

            private BigDecimal numberValue() {
                int start = position;
                if (!atEnd() && text.charAt(position) == '-') position++;
                require(!atEnd() && Character.isDigit(text.charAt(position)), "invalid JSON number at " + start);
                if (text.charAt(position) == '0') {
                    position++;
                } else {
                    while (!atEnd() && Character.isDigit(text.charAt(position))) position++;
                }
                if (!atEnd() && text.charAt(position) == '.') {
                    position++;
                    require(!atEnd() && Character.isDigit(text.charAt(position)), "invalid JSON fraction at " + position);
                    while (!atEnd() && Character.isDigit(text.charAt(position))) position++;
                }
                if (!atEnd() && (text.charAt(position) == 'e' || text.charAt(position) == 'E')) {
                    position++;
                    if (!atEnd() && (text.charAt(position) == '+' || text.charAt(position) == '-')) position++;
                    require(!atEnd() && Character.isDigit(text.charAt(position)), "invalid JSON exponent at " + position);
                    while (!atEnd() && Character.isDigit(text.charAt(position))) position++;
                }
                try {
                    return new BigDecimal(text.substring(start, position));
                } catch (NumberFormatException error) {
                    throw new AssertionError("invalid JSON number at " + start, error);
                }
            }

            private boolean take(char expected) {
                if (!atEnd() && text.charAt(position) == expected) {
                    position++;
                    return true;
                }
                return false;
            }

            private void expect(char expected) {
                whitespace();
                require(take(expected), "expected '" + expected + "' at character " + position);
            }
        }
    }
}
