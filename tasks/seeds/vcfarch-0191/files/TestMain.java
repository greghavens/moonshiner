import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        Path inventoryPath = Path.of("estate-inventory.json");
        Path snapshotPath = Path.of("compatibility-snapshot.json");

        Map<String, Object> installerSpec = object(
                Json.parse(Files.readString(Path.of("installer-spec.json"), StandardCharsets.UTF_8)),
                "installer specification");
        Map<String, Object> planSchema = object(installerSpec.get("planSchema"), "planSchema");

        String serialized = MigrationPlanClient.buildPlan(inventoryPath, snapshotPath);
        Object candidate;
        try {
            candidate = Json.parse(serialized);
        } catch (RuntimeException error) {
            throw new AssertionError("SCHEMA: client did not return valid JSON: " + error.getMessage());
        }

        List<String> schemaErrors = new ArrayList<>();
        Schema.validate(planSchema, candidate, "$", schemaErrors);
        if (!schemaErrors.isEmpty()) {
            throw new AssertionError("SCHEMA: " + String.join("; ", schemaErrors));
        }

        // No estate or compatibility assertion is evaluated before the artifact
        // has passed the planSchema above.
        Map<String, Object> plan = object(candidate, "plan");
        Map<String, Object> inventory = object(
                Json.parse(Files.readString(inventoryPath, StandardCharsets.UTF_8)), "inventory");
        Map<String, Object> snapshot = object(
                Json.parse(Files.readString(snapshotPath, StandardCharsets.UTF_8)), "snapshot");

        checkEqual(plan.get("schemaVersion"), installerSpec.get("schemaVersion"), "schemaVersion");
        checkEqual(plan.get("snapshotId"), snapshot.get("snapshotId"), "snapshotId");
        verifyResearch(plan, snapshot);

        Map<String, Object> architecture = object(plan.get("architecture"), "architecture");
        checkEqual(architecture.get("targetRelease"), inventory.get("targetRelease"),
                "architecture.targetRelease");
        checkEqual(architecture.get("targetRelease"), snapshot.get("targetRelease"),
                "snapshot targetRelease");

        verifyPlacements(architecture, snapshot);
        verifyEdge(architecture, inventory, snapshot);
        verifySteps(plan, inventory, snapshot);

        System.out.println("PASS: schema, migration ordering, compatibility, placement, sizing, and Edge design");
    }

    private static void verifyResearch(Map<String, Object> plan,
                                       Map<String, Object> snapshot) {
        List<Object> requiredValues = array(snapshot.get("requiredResearchTopics"),
                "snapshot.requiredResearchTopics");
        Set<String> required = new HashSet<>();
        for (Object value : requiredValues) {
            check(required.add(string(value, "required research topic")),
                    "duplicate required research topic " + value);
        }

        Set<String> covered = new HashSet<>();
        Set<String> urls = new HashSet<>();
        for (Object value : array(plan.get("research"), "research")) {
            Map<String, Object> source = object(value, "research source");
            checkEqual(source.get("publisher"), "Broadcom", "research publisher");

            String url = string(source.get("url"), "research.url");
            check(urls.add(url), "duplicate research URL " + url);
            URI parsed;
            try {
                parsed = URI.create(url);
            } catch (IllegalArgumentException error) {
                throw new AssertionError("research URL is invalid: " + url);
            }
            String host = parsed.getHost();
            check("https".equalsIgnoreCase(parsed.getScheme())
                            && host != null
                            && (host.equalsIgnoreCase("broadcom.com")
                            || host.toLowerCase().endsWith(".broadcom.com")),
                    "research URL must be an HTTPS Broadcom source: " + url);

            for (Object topicValue : array(source.get("consultedFor"),
                    "research.consultedFor")) {
                String topic = string(topicValue, "research topic");
                if (required.contains(topic)) {
                    covered.add(topic);
                }
            }
        }
        check(covered.equals(required),
                "research coverage expected " + required + " but got " + covered);
    }

    private static void verifyPlacements(Map<String, Object> architecture,
                                         Map<String, Object> snapshot) {
        List<Object> actual = array(architecture.get("placements"), "architecture.placements");
        List<Object> expected = array(snapshot.get("placements"), "snapshot.placements");
        check(actual.size() == expected.size(), "placement count");

        Map<String, Map<String, Object>> byComponent = new LinkedHashMap<>();
        for (Object value : actual) {
            Map<String, Object> placement = object(value, "placement");
            String component = string(placement.get("component"), "placement.component");
            check(byComponent.put(component, placement) == null,
                    "duplicate placement for " + component);
        }
        for (Object value : expected) {
            Map<String, Object> wanted = object(value, "snapshot placement");
            String component = string(wanted.get("component"), "snapshot placement.component");
            Map<String, Object> got = byComponent.get(component);
            check(got != null, "missing placement for " + component);
            for (String field : List.of("site", "cluster", "nodeCount", "profile",
                    "vcpuPerNode", "memoryGiBPerNode", "capacityBasis")) {
                checkEqual(got.get(field), wanted.get(field),
                        "placement " + component + "." + field);
            }
        }
    }

    private static void verifyEdge(Map<String, Object> architecture,
                                   Map<String, Object> inventory,
                                   Map<String, Object> snapshot) {
        Map<String, Object> edge = object(architecture.get("edge"), "architecture.edge");
        Map<String, Object> traffic = object(inventory.get("edgeTraffic"), "inventory.edgeTraffic");
        Map<String, Object> management = object(inventory.get("managementPlacement"),
                "inventory.managementPlacement");
        Map<String, Object> layout = object(snapshot.get("edgeLayout"), "snapshot.edgeLayout");

        double throughput = number(traffic.get("requiredNorthSouthGbps"), "requiredNorthSouthGbps");
        String expectedFactor = null;
        for (Object value : array(snapshot.get("edgeSizingRules"), "edgeSizingRules")) {
            Map<String, Object> rule = object(value, "edge sizing rule");
            double min = number(rule.get("minGbpsInclusive"), "minGbpsInclusive");
            double max = number(rule.get("maxGbpsExclusive"), "maxGbpsExclusive");
            if (throughput >= min && throughput < max) {
                check(expectedFactor == null, "overlapping Edge sizing rules");
                expectedFactor = string(rule.get("formFactor"), "formFactor");
            }
        }
        check(expectedFactor != null, "no Edge sizing rule covers estate throughput");

        checkNumber(edge.get("requiredNorthSouthGbps"), throughput,
                "edge.requiredNorthSouthGbps");
        checkEqual(edge.get("formFactor"), expectedFactor, "edge.formFactor");
        checkEqual(edge.get("edgeCount"), layout.get("edgeCount"), "edge.edgeCount");
        checkEqual(edge.get("haMode"), layout.get("haMode"), "edge.haMode");
        checkEqual(edge.get("uplinksPerEdge"), layout.get("uplinksPerEdge"),
                "edge.uplinksPerEdge");
        checkEqual(edge.get("tepPolicy"), layout.get("tepPolicy"), "edge.tepPolicy");
        checkEqual(edge.get("uplinkSpeedGbps"), management.get("physicalNicSpeedGbps"),
                "edge.uplinkSpeedGbps");
        checkListEqual(edge.get("uplinkSwitches"), management.get("leafSwitches"),
                "edge.uplinkSwitches");
        checkEqual(edge.get("hostTepVlan"), traffic.get("hostTepVlan"), "edge.hostTepVlan");
        checkEqual(edge.get("edgeTepVlan"), traffic.get("edgeTepVlan"), "edge.edgeTepVlan");
        check(!edge.get("hostTepVlan").equals(edge.get("edgeTepVlan")),
                "host and Edge TEP VLANs must differ");
        checkListEqual(edge.get("uplinkVlans"), traffic.get("uplinkVlans"), "edge.uplinkVlans");

        if (Boolean.TRUE.equals(layout.get("physicalSwitchDiversityRequired"))) {
            List<Object> switches = array(edge.get("uplinkSwitches"), "edge.uplinkSwitches");
            check(new HashSet<>(switches).size() == switches.size(),
                    "Edge uplinks must terminate on distinct physical switches");
        }
    }

    private static void verifySteps(Map<String, Object> plan,
                                    Map<String, Object> inventory,
                                    Map<String, Object> snapshot) {
        List<Object> steps = array(plan.get("steps"), "steps");
        List<Object> order = array(snapshot.get("migrationOrder"), "migrationOrder");
        List<Object> inventoryProducts = array(inventory.get("products"), "inventory.products");
        List<Object> snapshotProducts = array(snapshot.get("products"), "snapshot.products");
        check(steps.size() == inventoryProducts.size(), "one migration step is required per product");
        check(steps.size() == order.size(), "step count must match migration order");

        Map<String, Map<String, Object>> inventoryById = indexBy(inventoryProducts, "id");
        Map<String, Map<String, Object>> snapshotById = indexBy(snapshotProducts, "inventoryId");
        Set<String> seen = new HashSet<>();

        for (int index = 0; index < steps.size(); index++) {
            Map<String, Object> step = object(steps.get(index), "step " + (index + 1));
            checkNumber(step.get("order"), index + 1, "steps[" + index + "].order");
            Map<String, Object> source = object(step.get("source"), "step.source");
            String id = string(source.get("id"), "step.source.id");
            checkEqual(id, order.get(index), "migration order at step " + (index + 1));
            check(seen.add(id), "source product repeated: " + id);

            Map<String, Object> inventoried = inventoryById.get(id);
            Map<String, Object> compatible = snapshotById.get(id);
            check(inventoried != null, "step names unknown inventory product " + id);
            check(compatible != null, "step names product absent from snapshot " + id);

            checkEqual(source.get("product"), inventoried.get("product"), id + " source product");
            checkEqual(source.get("version"), inventoried.get("version"), id + " source version");
            checkEqual(source.get("eogs"), compatible.get("sourceEogs"), id + " EOGS");

            Map<String, Object> target = object(step.get("target"), id + " target");
            checkEqual(target.get("component"), compatible.get("targetComponent"),
                    id + " target component");
            checkEqual(target.get("version"), compatible.get("targetVersion"),
                    id + " target version");
            checkEqual(step.get("strategy"), compatible.get("strategy"), id + " strategy");
            checkListEqual(step.get("transitions"), compatible.get("transitions"),
                    id + " transitions");
            checkSetEqual(step.get("carries"), compatible.get("carries"), id + " carried content");
            checkSetEqual(step.get("abandons"), compatible.get("abandons"), id + " abandoned content");
            checkSetEqual(step.get("gates"), compatible.get("gates"), id + " gates");
        }
        check(seen.equals(inventoryById.keySet()), "every inventory product must appear exactly once");
    }

    private static Map<String, Map<String, Object>> indexBy(List<Object> rows, String key) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : rows) {
            Map<String, Object> row = object(value, "row");
            String id = string(row.get(key), key);
            check(result.put(id, row) == null, "duplicate " + key + ": " + id);
        }
        return result;
    }

    private static void checkListEqual(Object actual, Object expected, String label) {
        List<Object> left = array(actual, label);
        List<Object> right = array(expected, "expected " + label);
        check(left.equals(right), label + " expected " + right + " but got " + left);
    }

    private static void checkSetEqual(Object actual, Object expected, String label) {
        List<Object> left = array(actual, label);
        List<Object> right = array(expected, "expected " + label);
        check(left.size() == new HashSet<>(left).size(), label + " contains duplicates");
        check(new HashSet<>(left).equals(new HashSet<>(right)),
                label + " expected " + right + " but got " + left);
    }

    private static void checkEqual(Object actual, Object expected, String label) {
        if (actual instanceof Number && expected instanceof Number) {
            checkNumber(actual, ((Number) expected).doubleValue(), label);
        } else {
            check(expected != null && expected.equals(actual),
                    label + " expected " + expected + " but got " + actual);
        }
    }

    private static void checkNumber(Object actual, double expected, String label) {
        check(actual instanceof Number
                        && Math.abs(((Number) actual).doubleValue() - expected) < 0.0000001,
                label + " expected " + expected + " but got " + actual);
    }

    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?> raw)) {
            throw new AssertionError(label + " must be an object");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : raw.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new AssertionError(label + " has a non-string key");
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    private static List<Object> array(Object value, String label) {
        if (!(value instanceof List<?> raw)) {
            throw new AssertionError(label + " must be an array");
        }
        return new ArrayList<>(raw);
    }

    private static String string(Object value, String label) {
        if (!(value instanceof String text)) {
            throw new AssertionError(label + " must be a string");
        }
        return text;
    }

    private static double number(Object value, String label) {
        if (!(value instanceof Number number)) {
            throw new AssertionError(label + " must be a number");
        }
        return number.doubleValue();
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class Schema {
        private Schema() {}

        static void validate(Map<String, Object> schema, Object value, String path,
                             List<String> errors) {
            String type = schema.get("type") instanceof String text ? text : null;
            if (type != null && !matchesType(type, value)) {
                errors.add(path + " must be " + type);
                return;
            }

            if (value instanceof Map<?, ?> raw) {
                Map<String, Object> candidate = new LinkedHashMap<>();
                for (Map.Entry<?, ?> entry : raw.entrySet()) {
                    candidate.put(String.valueOf(entry.getKey()), entry.getValue());
                }
                if (schema.get("required") instanceof List<?> required) {
                    for (Object key : required) {
                        if (!candidate.containsKey(key)) {
                            errors.add(path + " missing required property " + key);
                        }
                    }
                }
                Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?> map
                        ? copyStringMap(map) : Map.of();
                if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    for (String key : candidate.keySet()) {
                        if (!properties.containsKey(key)) {
                            errors.add(path + " has additional property " + key);
                        }
                    }
                }
                for (Map.Entry<String, Object> entry : candidate.entrySet()) {
                    Object childSchema = properties.get(entry.getKey());
                    if (childSchema instanceof Map<?, ?> map) {
                        validate(copyStringMap(map), entry.getValue(),
                                path + "." + entry.getKey(), errors);
                    }
                }
            }

            if (value instanceof List<?> list) {
                if (schema.get("minItems") instanceof Number minimum
                        && list.size() < minimum.intValue()) {
                    errors.add(path + " must contain at least " + minimum.intValue() + " items");
                }
                if (schema.get("items") instanceof Map<?, ?> itemSchema) {
                    Map<String, Object> child = copyStringMap(itemSchema);
                    for (int index = 0; index < list.size(); index++) {
                        validate(child, list.get(index), path + "[" + index + "]", errors);
                    }
                }
            }

            if (value instanceof String text && schema.get("minLength") instanceof Number minimum
                    && text.length() < minimum.intValue()) {
                errors.add(path + " must contain at least " + minimum.intValue() + " characters");
            }
            if (value instanceof Number number && schema.get("minimum") instanceof Number minimum
                    && number.doubleValue() < minimum.doubleValue()) {
                errors.add(path + " must be at least " + minimum);
            }
        }

        private static boolean matchesType(String type, Object value) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "number" -> value instanceof Number;
                case "integer" -> value instanceof Number number
                        && Double.isFinite(number.doubleValue())
                        && Math.rint(number.doubleValue()) == number.doubleValue();
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> false;
            };
        }

        private static Map<String, Object> copyStringMap(Map<?, ?> raw) {
            Map<String, Object> result = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : raw.entrySet()) {
                result.put(String.valueOf(entry.getKey()), entry.getValue());
            }
            return result;
        }
    }

    private static final class Json {
        private final String input;
        private int position;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            if (input == null) {
                throw new IllegalArgumentException("JSON text is null");
            }
            Json parser = new Json(input);
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (parser.position != input.length()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        private Object readValue() {
            skipWhitespace();
            if (position >= input.length()) {
                throw error("expected a value");
            }
            return switch (input.charAt(position)) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            expect('{');
            Map<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    throw error("expected object key");
                }
                String key = readString();
                skipWhitespace();
                expect(':');
                Object value = readValue();
                if (result.containsKey(key)) {
                    throw error("duplicate object key " + key);
                }
                result.put(key, value);
                skipWhitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> readArray() {
            expect('[');
            List<Object> result = new ArrayList<>();
            skipWhitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(readValue());
                skipWhitespace();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String readString() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char current = input.charAt(position++);
                if (current == '"') {
                    return result.toString();
                }
                if (current == '\\') {
                    if (position >= input.length()) {
                        throw error("unfinished escape");
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
                            String digits = input.substring(position, position + 4);
                            try {
                                result.append((char) Integer.parseInt(digits, 16));
                            } catch (NumberFormatException error) {
                                throw error("invalid unicode escape");
                            }
                            position += 4;
                        }
                        default -> throw error("invalid escape");
                    }
                } else {
                    if (current < 0x20) {
                        throw error("control character in string");
                    }
                    result.append(current);
                }
            }
            throw error("unterminated string");
        }

        private Object readNumber() {
            int start = position;
            if (take('-')) {
                // sign consumed
            }
            if (take('0')) {
                // zero consumed
            } else {
                requireDigit();
                while (position < input.length() && Character.isDigit(input.charAt(position))) {
                    position++;
                }
            }
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                requireDigit();
                while (position < input.length() && Character.isDigit(input.charAt(position))) {
                    position++;
                }
            }
            if (position < input.length()
                    && (input.charAt(position) == 'e' || input.charAt(position) == 'E')) {
                decimal = true;
                position++;
                if (position < input.length()
                        && (input.charAt(position) == '+' || input.charAt(position) == '-')) {
                    position++;
                }
                requireDigit();
                while (position < input.length() && Character.isDigit(input.charAt(position))) {
                    position++;
                }
            }
            if (start == position) {
                throw error("expected number");
            }
            String token = input.substring(start, position);
            try {
                return decimal ? Double.valueOf(token) : Long.valueOf(token);
            } catch (NumberFormatException error) {
                throw error("invalid number");
            }
        }

        private void requireDigit() {
            if (position >= input.length() || !Character.isDigit(input.charAt(position))) {
                throw error("expected digit");
            }
        }

        private Object readLiteral(String literal, Object value) {
            if (!input.startsWith(literal, position)) {
                throw error("expected " + literal);
            }
            position += literal.length();
            return value;
        }

        private void skipWhitespace() {
            while (position < input.length()) {
                char current = input.charAt(position);
                if (current != ' ' && current != '\n' && current != '\r' && current != '\t') {
                    return;
                }
                position++;
            }
        }

        private boolean take(char expected) {
            if (position < input.length() && input.charAt(position) == expected) {
                position++;
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
            return new IllegalArgumentException(message + " at character " + position);
        }
    }
}
