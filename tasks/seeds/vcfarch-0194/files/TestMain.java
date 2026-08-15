import java.math.BigDecimal;
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

/** Protected, dependency-free verifier for the generated architecture artifact. */
public final class TestMain {
    private static boolean schemaValidated;

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Path inventoryPath = Path.of("estate-inventory.json");
        Path snapshotPath = Path.of("compatibility-snapshot.json");

        String artifactText = MigrationPlanClient.buildPlan(inventoryPath, snapshotPath);

        // Contractual first check: parse the installer specification and validate the
        // artifact against its schema before loading or asserting fixture semantics.
        Map<String, Object> installerSpec = object(Json.parse(Files.readString(
                Path.of("installer-spec.json"), StandardCharsets.UTF_8)), "installer specification");
        Object artifactValue = Json.parse(artifactText);
        SchemaValidator.validate(installerSpec, artifactValue, "$");

        Path commandLineOutput = Files.createTempFile("migration-plan-client-", ".json");
        try {
            MigrationPlanClient.main(new String[] {
                    inventoryPath.toString(), snapshotPath.toString(), commandLineOutput.toString()
            });
            Object commandLineArtifact = Json.parse(Files.readString(
                    commandLineOutput, StandardCharsets.UTF_8));
            SchemaValidator.validate(installerSpec, commandLineArtifact, "$cli");
            equal(artifactValue, commandLineArtifact,
                    "command-line entry point artifact");
        } finally {
            Files.deleteIfExists(commandLineOutput);
        }

        schemaValidated = true;
        Map<String, Object> plan = object(artifactValue, "plan");
        Map<String, Object> inventory = object(Json.parse(Files.readString(
                inventoryPath, StandardCharsets.UTF_8)), "inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(
                snapshotPath, StandardCharsets.UTF_8)), "compatibility snapshot");
        verifySemantics(plan, inventory, snapshot);
        verifyResearchNotes(Path.of("RESEARCH.md"));
        System.out.println("PASS: installer schema validated first; architecture matches fixture and pinned snapshot");
    }

    private static void verifyResearchNotes(Path researchPath) throws Exception {
        check(Files.isRegularFile(researchPath), "missing RESEARCH.md");
        String notes = Files.readString(researchPath, StandardCharsets.UTF_8);
        check(Pattern.compile("\\b\\d{4}-\\d{2}-\\d{2}\\b").matcher(notes).find(),
                "RESEARCH.md must record an ISO access date");

        var sourceUrls = Pattern.compile("https?://[^\\s)>]+").matcher(notes);
        int sourceEntries = 0;
        while (sourceUrls.find()) {
            sourceEntries++;
        }
        check(sourceEntries >= 2,
                "RESEARCH.md must document multiple reachable HTTP(S) sources");
        for (String component : List.of("VCF Operations", "VCF Automation",
                "VCF Operations for Logs")) {
            check(notes.contains(component),
                    "RESEARCH.md does not cover " + component);
        }
    }

    private static void verifySemantics(Map<String, Object> plan,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        requireSchemaValidated();
        equal(1L, plan.get("schemaVersion"), "schemaVersion");
        equal(inventory.get("estateId"), plan.get("estateId"), "estateId");
        equal(inventory.get("targetRelease"), plan.get("targetRelease"), "targetRelease");
        equal(snapshot.get("targetRelease"), plan.get("targetRelease"), "snapshot targetRelease");

        verifyTopology(plan, inventory, snapshot);
        verifyPlacements(plan, inventory, snapshot);
        verifySteps(plan, inventory, snapshot);
    }

    private static void verifyTopology(Map<String, Object> plan,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        requireSchemaValidated();
        Map<String, Object> actual = object(plan.get("topologyDecision"), "topologyDecision");
        Map<String, Object> expected = object(snapshot.get("topology"), "snapshot.topology");
        equal(expected.get("selectedDomain"), actual.get("selectedDomain"), "selected domain");
        equal(expected.get("rejectedDomain"), actual.get("rejectedDomain"), "rejected domain");
        equal(expected.get("rejectionReasonCode"), actual.get("reasonCode"), "topology reason");

        Map<String, Object> selected = findBy(maps(inventory.get("domains"), "domains"),
                "id", string(actual.get("selectedDomain"), "selected domain"));
        Map<String, Object> rejected = findBy(maps(inventory.get("domains"), "domains"),
                "id", string(actual.get("rejectedDomain"), "rejected domain"));
        check(Boolean.TRUE.equals(selected.get("technicallySupportsManagementComponents")),
                "selected domain must technically support management components");
        check(Boolean.TRUE.equals(selected.get("entitled")), "selected domain is not entitled");
        check(Boolean.TRUE.equals(rejected.get("technicallySupportsManagementComponents")),
                "rejected alternative must be technically supported");
        check(Boolean.FALSE.equals(rejected.get("entitled")),
                "rejected alternative must be excluded by entitlement");
    }

    private static void verifyPlacements(Map<String, Object> plan,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        requireSchemaValidated();
        List<Map<String, Object>> actual = maps(plan.get("placements"), "placements");
        List<Map<String, Object>> expected = maps(snapshot.get("sizing"), "snapshot.sizing");
        equal(expected.size(), actual.size(), "placement count");
        String selectedDomain = string(object(plan.get("topologyDecision"),
                "topologyDecision").get("selectedDomain"), "selectedDomain");

        long totalCpu = 0;
        long totalMemory = 0;
        long totalStorage = 0;
        Set<String> seen = new LinkedHashSet<>();
        for (Map<String, Object> sizing : expected) {
            String component = string(sizing.get("component"), "sizing component");
            Map<String, Object> placement = findBy(actual, "component", component);
            check(seen.add(component), "duplicate placement for " + component);
            for (String field : List.of("component", "version", "profile", "nodes",
                    "vCpuPerNode", "memoryGiBPerNode", "usableStorageGiBPerNode",
                    "capacityBasis")) {
                equal(sizing.get(field), placement.get(field), "placement " + component + "." + field);
            }
            equal(selectedDomain, placement.get("domain"), "placement domain for " + component);
            long nodes = integer(placement.get("nodes"), component + " nodes");
            totalCpu += nodes * integer(placement.get("vCpuPerNode"), component + " vCPU");
            totalMemory += nodes * integer(placement.get("memoryGiBPerNode"), component + " memory");
            totalStorage += nodes * integer(placement.get("usableStorageGiBPerNode"), component + " storage");
        }

        Map<String, Object> domain = findBy(maps(inventory.get("domains"), "domains"),
                "id", selectedDomain);
        check(totalCpu <= integer(domain.get("availableVCpu"), "availableVCpu"),
                "target sizing exceeds licensed-domain vCPU capacity");
        check(totalMemory <= integer(domain.get("availableMemoryGiB"), "availableMemoryGiB"),
                "target sizing exceeds licensed-domain memory capacity");
        check(totalStorage <= integer(domain.get("availableUsableStorageGiB"),
                "availableUsableStorageGiB"),
                "target sizing exceeds licensed-domain storage capacity");
    }

    private static void verifySteps(Map<String, Object> plan,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        requireSchemaValidated();
        List<Map<String, Object>> actual = maps(plan.get("steps"), "steps");
        List<Map<String, Object>> expected = maps(snapshot.get("migrationPaths"),
                "snapshot.migrationPaths");
        List<Map<String, Object>> products = maps(inventory.get("products"), "products");
        equal(products.size(), actual.size(), "one migration step per inventoried product");
        equal(expected.size(), actual.size(), "migration path count");

        Set<String> representedInstances = new LinkedHashSet<>();
        Set<String> completed = new LinkedHashSet<>();
        for (int i = 0; i < expected.size(); i++) {
            Map<String, Object> path = expected.get(i);
            Map<String, Object> step = actual.get(i);
            String id = string(path.get("id"), "path id");
            equal(id, step.get("id"), "ordered step id at index " + i);
            equal((long) (i + 1), step.get("order"), "step order for " + id);
            equal(path.get("order"), step.get("order"), "snapshot order for " + id);
            equal(path.get("method"), step.get("method"), "transition method for " + id);

            Map<String, Object> source = object(step.get("source"), id + ".source");
            for (Map.Entry<String, String> mapping : Map.of(
                    "instanceId", "sourceInstanceId",
                    "product", "sourceProduct",
                    "version", "sourceVersion",
                    "eogs", "sourceEogs").entrySet()) {
                equal(path.get(mapping.getValue()), source.get(mapping.getKey()),
                        id + " source " + mapping.getKey());
            }
            String instanceId = string(source.get("instanceId"), id + " source instance");
            check(representedInstances.add(instanceId), "duplicate source instance " + instanceId);
            Map<String, Object> product = findBy(products, "instanceId", instanceId);
            equal(product.get("product"), source.get("product"), id + " inventory product");
            equal(product.get("version"), source.get("version"), id + " inventory version");

            Map<String, Object> target = object(step.get("target"), id + ".target");
            equal(path.get("targetComponent"), target.get("component"), id + " target component");
            equal(path.get("targetVersion"), target.get("version"), id + " target version");

            equal(path.get("dependsOn"), step.get("dependsOn"), id + " dependencies");
            for (Object dependency : list(step.get("dependsOn"), id + ".dependsOn")) {
                check(completed.contains(dependency), id + " depends on incomplete step " + dependency);
            }
            equal(dispositions(path.get("carries"), "item", "method"),
                    dispositions(step.get("carries"), "item", "method"), id + " carried content");
            equal(dispositions(path.get("abandoned"), "item", "reason"),
                    dispositions(step.get("abandoned"), "item", "reason"), id + " abandoned content");
            equal(new LinkedHashSet<>(strings(path.get("gates"), id + " expected gates")),
                    new LinkedHashSet<>(strings(step.get("gates"), id + " gates")), id + " gates");
            completed.add(id);
        }
        Set<String> inventoryInstances = new LinkedHashSet<>();
        for (Map<String, Object> product : products) {
            inventoryInstances.add(string(product.get("instanceId"), "inventory instanceId"));
        }
        equal(inventoryInstances, representedInstances, "all inventory products represented");
    }

    private static Set<String> dispositions(Object value, String keyA, String keyB) {
        Set<String> result = new LinkedHashSet<>();
        for (Map<String, Object> entry : maps(value, "content dispositions")) {
            String normalized = string(entry.get(keyA), keyA) + "\u0000" + string(entry.get(keyB), keyB);
            check(result.add(normalized), "duplicate content disposition " + entry.get(keyA));
        }
        return result;
    }

    private static void requireSchemaValidated() {
        check(schemaValidated, "semantic verification ran before installer schema validation");
    }

    private static Map<String, Object> findBy(List<Map<String, Object>> values,
            String field, String wanted) {
        Map<String, Object> found = null;
        for (Map<String, Object> value : values) {
            if (wanted.equals(value.get(field))) {
                check(found == null, "duplicate " + field + " " + wanted);
                found = value;
            }
        }
        check(found != null, "missing " + field + " " + wanted);
        return found;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        check(value instanceof Map<?, ?>, label + " must be an object");
        return (Map<String, Object>) value;
    }

    private static List<Map<String, Object>> maps(Object value, String label) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (Object item : list(value, label)) {
            result.add(object(item, label + " item"));
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object value, String label) {
        check(value instanceof List<?>, label + " must be an array");
        return (List<Object>) value;
    }

    private static List<String> strings(Object value, String label) {
        List<String> result = new ArrayList<>();
        for (Object item : list(value, label)) {
            result.add(string(item, label + " item"));
        }
        return result;
    }

    private static String string(Object value, String label) {
        check(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static long integer(Object value, String label) {
        check(value instanceof Number, label + " must be an integer");
        return ((Number) value).longValue();
    }

    private static void equal(Object expected, Object actual, String label) {
        check(Objects.equals(expected, actual), label + ": expected " + expected + " but got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(Map<String, Object> schema, Object instance, String path) {
            Object type = schema.get("type");
            if (type != null) {
                validateType(string(type, path + " schema type"), instance, path);
            }
            if (schema.containsKey("const")) {
                check(Objects.equals(schema.get("const"), instance),
                        path + " does not equal schema const " + schema.get("const"));
            }
            if (schema.containsKey("enum")) {
                check(list(schema.get("enum"), path + " enum").contains(instance),
                        path + " is not in schema enum");
            }
            if (instance instanceof String text && schema.containsKey("minLength")) {
                check(text.length() >= integer(schema.get("minLength"), path + " minLength"),
                        path + " is shorter than minLength");
            }
            if (instance instanceof List<?> values) {
                if (schema.containsKey("minItems")) {
                    check(values.size() >= integer(schema.get("minItems"), path + " minItems"),
                            path + " has fewer than minItems");
                }
                if (schema.containsKey("items")) {
                    Map<String, Object> itemSchema = object(schema.get("items"), path + " items schema");
                    for (int i = 0; i < values.size(); i++) {
                        validate(itemSchema, values.get(i), path + "[" + i + "]");
                    }
                }
            }
            if (instance instanceof Map<?, ?>) {
                Map<String, Object> value = object(instance, path);
                Set<String> required = new LinkedHashSet<>();
                if (schema.containsKey("required")) {
                    required.addAll(strings(schema.get("required"), path + " required"));
                }
                for (String field : required) {
                    check(value.containsKey(field), path + " missing required property " + field);
                }
                Map<String, Object> properties = schema.containsKey("properties")
                        ? object(schema.get("properties"), path + " properties") : Map.of();
                if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    for (String field : value.keySet()) {
                        check(properties.containsKey(field), path + " has additional property " + field);
                    }
                }
                for (Map.Entry<String, Object> property : properties.entrySet()) {
                    if (value.containsKey(property.getKey())) {
                        validate(object(property.getValue(), path + " property schema " + property.getKey()),
                                value.get(property.getKey()), path + "." + property.getKey());
                    }
                }
            }
        }

        private static void validateType(String type, Object value, String path) {
            boolean valid = switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "integer" -> value instanceof Byte || value instanceof Short
                        || value instanceof Integer || value instanceof Long;
                case "number" -> value instanceof Number;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> false;
            };
            check(valid, path + " must have schema type " + type);
        }
    }

    /** Small strict JSON parser sufficient for the installer schema and artifacts. */
    private static final class Json {
        private final String input;
        private int position;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            Json parser = new Json(input);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != input.length()) {
                throw parser.error("trailing data");
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (position >= input.length()) {
                throw error("expected value");
            }
            return switch (input.charAt(position)) {
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
            do {
                whitespace();
                String key = stringValue();
                check(!result.containsKey(key), "duplicate JSON key " + key);
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
            } while (take(','));
            expect('}');
            return result;
        }

        private List<Object> arrayValue() {
            expect('[');
            List<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return result;
            }
            do {
                result.add(value());
                whitespace();
            } while (take(','));
            expect(']');
            return result;
        }

        private String stringValue() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char c = input.charAt(position++);
                if (c == '"') {
                    return result.toString();
                }
                if (c == '\\') {
                    if (position >= input.length()) {
                        throw error("unterminated escape");
                    }
                    char escaped = input.charAt(position++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (position + 4 > input.length()) {
                                throw error("short unicode escape");
                            }
                            result.append((char) Integer.parseInt(
                                    input.substring(position, position + 4), 16));
                            position += 4;
                        }
                        default -> throw error("invalid escape");
                    }
                } else {
                    if (c < 0x20) {
                        throw error("control character in string");
                    }
                    result.append(c);
                }
            }
            throw error("unterminated string");
        }

        private Object numberValue() {
            int start = position;
            if (take('-')) {
                // sign consumed
            }
            digits();
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                digits();
            }
            if (position < input.length()
                    && (input.charAt(position) == 'e' || input.charAt(position) == 'E')) {
                decimal = true;
                position++;
                if (position < input.length()
                        && (input.charAt(position) == '+' || input.charAt(position) == '-')) {
                    position++;
                }
                digits();
            }
            if (start == position) {
                throw error("invalid value");
            }
            String number = input.substring(start, position);
            try {
                return decimal ? new BigDecimal(number) : Long.valueOf(number);
            } catch (NumberFormatException exception) {
                throw error("invalid number");
            }
        }

        private void digits() {
            int start = position;
            while (position < input.length() && Character.isDigit(input.charAt(position))) {
                position++;
            }
            if (start == position) {
                throw error("expected digit");
            }
        }

        private Object literal(String token, Object value) {
            if (!input.startsWith(token, position)) {
                throw error("invalid literal");
            }
            position += token.length();
            return value;
        }

        private void whitespace() {
            while (position < input.length()
                    && Character.isWhitespace(input.charAt(position))) {
                position++;
            }
        }

        private boolean take(char wanted) {
            if (position < input.length() && input.charAt(position) == wanted) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char wanted) {
            if (!take(wanted)) {
                throw error("expected '" + wanted + "'");
            }
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at JSON offset " + position);
        }
    }
}
