import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");
    private static final Path SPEC = Path.of("installer-spec.json");
    private static final Path ARTIFACT = Path.of("migration-plan.json");

    private static final String INVENTORY_SHA256 =
            "871ed6a068a18b6c84ddd85d8156ea69b8c505a0bf7efc84380e62456667502c";
    private static final String SNAPSHOT_SHA256 =
            "b5f33fd929b7599609f4133250a1cea2d1e5d41503a0375bea6c1f041f7fa248";
    private static final String SPEC_SHA256 =
            "d5c99c0005894576aea9c3de2f4ce99f3b49e7d2a48d3b2d015803b8b2e40918";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Files.deleteIfExists(ARTIFACT);
        EstateMigrationClient.main(new String[] {
                INVENTORY.toString(), SNAPSHOT.toString(), SPEC.toString(), ARTIFACT.toString()
        });
        require(Files.isRegularFile(ARTIFACT), "client did not create migration-plan.json");

        Object specification = Json.parse(Files.readString(SPEC));
        Object artifact = Json.parse(Files.readString(ARTIFACT));

        // Contract validation is deliberately the first verification phase. No fixture,
        // snapshot, compatibility, sizing, or research assertion runs before it succeeds.
        Object artifactSchema = object(specification, "installer specification").get("artifactSchema");
        SchemaValidator.validate(artifactSchema, artifact, "$");

        verifyDigest(INVENTORY, INVENTORY_SHA256);
        verifyDigest(SNAPSHOT, SNAPSHOT_SHA256);
        verifyDigest(SPEC, SPEC_SHA256);

        Object inventory = Json.parse(Files.readString(INVENTORY));
        Object snapshot = Json.parse(Files.readString(SNAPSHOT));
        verifyArchitecture(object(artifact, "artifact"), object(inventory, "inventory"),
                object(snapshot, "snapshot"));

        System.out.println("PASS: migration-plan.json conforms to the installer schema and pinned architecture");
    }

    private static void verifyArchitecture(Map<String, Object> artifact,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        equal("1.0", string(artifact.get("schemaVersion"), "schemaVersion"), "schemaVersion");
        equal(string(inventory.get("inventoryVersion"), "inventoryVersion"),
                string(artifact.get("inventoryVersion"), "artifact inventoryVersion"),
                "inventoryVersion");
        equal(string(snapshot.get("snapshotVersion"), "snapshotVersion"),
                string(artifact.get("snapshotVersion"), "artifact snapshotVersion"),
                "snapshotVersion");
        equal(inventory.get("targetBundle"), artifact.get("targetBundle"), "targetBundle from inventory");
        equal(snapshot.get("targetBundle"), artifact.get("targetBundle"), "targetBundle from snapshot");

        verifyResearch(array(artifact.get("research"), "research"));

        List<Object> expectedPlacements = array(snapshot.get("targetSizing"), "targetSizing");
        List<Object> actualPlacements = array(artifact.get("placements"), "placements");
        equal(expectedPlacements.size(), actualPlacements.size(), "placement count");
        Map<String, Map<String, Object>> placementById = indexBy(actualPlacements, "placementId", "placements");
        for (Object expectedObject : expectedPlacements) {
            Map<String, Object> expected = object(expectedObject, "target sizing entry");
            String id = string(expected.get("placementId"), "target sizing placementId");
            require(placementById.containsKey(id), "missing placement " + id);
            equal(expected, placementById.get(id), "placement " + id);
        }
        verifyPlacementCapacity(actualPlacements, object(inventory.get("infrastructure"), "infrastructure"));

        Map<String, Map<String, Object>> sourceById = indexBy(
                array(inventory.get("sourceProducts"), "sourceProducts"), "id", "sourceProducts");
        List<Object> rules = array(snapshot.get("migrationRules"), "migrationRules");
        List<Object> plan = array(artifact.get("migrationPlan"), "migrationPlan");
        equal(rules.size(), plan.size(), "migration plan length");
        equal(sourceById.size(), plan.size(), "every inventoried source must have one migration step");

        Set<String> plannedSources = new HashSet<>();
        for (int i = 0; i < rules.size(); i++) {
            Map<String, Object> rule = object(rules.get(i), "migration rule " + i);
            Map<String, Object> step = object(plan.get(i), "migration step " + i);
            long expectedSequence = integer(rule.get("sequence"), "rule sequence");
            equal(expectedSequence, integer(step.get("sequence"), "step sequence"),
                    "sequence at migrationPlan[" + i + "]");
            equal((long) i + 1, expectedSequence, "contiguous sequence at rule " + i);

            String sourceId = string(rule.get("sourceId"), "rule sourceId");
            require(plannedSources.add(sourceId), "duplicate migration step for " + sourceId);
            Map<String, Object> sourceInventory = sourceById.get(sourceId);
            require(sourceInventory != null, "snapshot references unknown source " + sourceId);
            Map<String, Object> source = object(step.get("source"), "step source");
            equal(sourceId, source.get("id"), "source id for " + sourceId);
            equal(sourceInventory.get("product"), source.get("product"), "source product for " + sourceId);
            equal(sourceInventory.get("version"), source.get("version"), "source version for " + sourceId);
            equal(rule.get("sourceProduct"), source.get("product"), "snapshot source product for " + sourceId);
            equal(rule.get("sourceVersion"), source.get("version"), "snapshot source version for " + sourceId);

            Map<String, Object> target = object(step.get("target"), "step target");
            equal(rule.get("targetComponent"), target.get("component"), "target component for " + sourceId);
            equal(rule.get("targetVersion"), target.get("version"), "target version for " + sourceId);
            equal(rule.get("placementId"), target.get("placementId"), "target placement for " + sourceId);
            require(placementById.containsKey(string(target.get("placementId"), "placement reference")),
                    "step references an unknown placement for " + sourceId);

            equal(rule.get("method"), step.get("method"), "migration method for " + sourceId);
            equal(rule.get("directUpgradeSupported"), step.get("directUpgradeSupported"),
                    "direct-upgrade support for " + sourceId);
            equal(rule.get("carryForward"), step.get("carryForward"), "carry-forward content for " + sourceId);
            equal(rule.get("abandoned"), step.get("abandoned"), "abandoned content for " + sourceId);

            List<Object> expectedGateIds = array(rule.get("requiredGateIds"), "requiredGateIds");
            List<Object> gates = array(step.get("gates"), "gates");
            equal(expectedGateIds.size(), gates.size(), "gate count for " + sourceId);
            List<Object> actualGateIds = new ArrayList<>();
            Set<String> uniqueGateIds = new HashSet<>();
            for (Object gateObject : gates) {
                Map<String, Object> gate = object(gateObject, "gate for " + sourceId);
                String gateId = string(gate.get("id"), "gate id");
                require(uniqueGateIds.add(gateId), "duplicate gate " + gateId + " for " + sourceId);
                actualGateIds.add(gateId);
            }
            equal(expectedGateIds, actualGateIds, "ordered gate ids for " + sourceId);
        }
        equal(sourceById.keySet(), plannedSources, "planned source coverage");

        equal(snapshot.get("supportBoundaries"), artifact.get("supportBoundaries"),
                "pinned support boundaries");
    }

    private static void verifyResearch(List<Object> research) {
        for (int i = 0; i < research.size(); i++) {
            Map<String, Object> entry = object(research.get(i), "research entry " + i);
            String urlText = string(entry.get("url"), "research URL " + i);
            URI url;
            try {
                url = URI.create(urlText);
            } catch (IllegalArgumentException exception) {
                throw new AssertionError("research URL " + i + " is invalid: " + urlText, exception);
            }
            String host = url.getHost();
            require("https".equalsIgnoreCase(url.getScheme()),
                    "research URL " + i + " must use HTTPS");
            require(host != null && (host.equalsIgnoreCase("broadcom.com")
                            || host.toLowerCase().endsWith(".broadcom.com")),
                    "research URL " + i + " must be a Broadcom-published page");

            String consultedOn = string(entry.get("consultedOn"), "research consultation date " + i);
            try {
                LocalDate.parse(consultedOn);
            } catch (DateTimeParseException exception) {
                throw new AssertionError(
                        "research consultation date " + i + " must be an ISO-8601 calendar date", exception);
            }
        }
    }

    private static void verifyPlacementCapacity(List<Object> placements, Map<String, Object> infrastructure) {
        long totalCpu = 0;
        long totalMemory = 0;
        long totalStorage = 0;
        for (Object placementObject : placements) {
            Map<String, Object> placement = object(placementObject, "placement");
            for (Object nodeObject : array(placement.get("nodes"), "placement nodes")) {
                Map<String, Object> node = object(nodeObject, "node sizing");
                long count = integer(node.get("count"), "node count");
                totalCpu += count * integer(node.get("vCpu"), "node vCpu");
                totalMemory += count * integer(node.get("memoryGiB"), "node memoryGiB");
                totalStorage += count * integer(node.get("dataDiskGiB"), "node dataDiskGiB");
            }
        }
        Map<String, Object> available = object(infrastructure.get("availableCapacity"), "availableCapacity");
        require(totalCpu <= integer(available.get("vCpu"), "available vCpu"),
                "placement vCPU exceeds inventory capacity");
        require(totalMemory <= integer(available.get("memoryGiB"), "available memoryGiB"),
                "placement memory exceeds inventory capacity");
        require(totalStorage <= integer(available.get("storageGiB"), "available storageGiB"),
                "placement storage exceeds inventory capacity");
    }

    private static Map<String, Map<String, Object>> indexBy(List<Object> values, String key, String label) {
        Map<String, Map<String, Object>> indexed = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> entry = object(value, label + " entry");
            String id = string(entry.get(key), label + " " + key);
            require(indexed.put(id, entry) == null, "duplicate " + key + " " + id + " in " + label);
        }
        return indexed;
    }

    private static void verifyDigest(Path path, String expected) throws Exception {
        byte[] bytes = Files.readAllBytes(path);
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder actual = new StringBuilder();
        for (byte value : digest) {
            actual.append(String.format("%02x", value & 0xff));
        }
        equal(expected, actual.toString(), path + " digest");
    }

    private static Map<String, Object> object(Object value, String label) {
        require(value instanceof Map<?, ?>, label + " must be an object");
        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) value;
        return result;
    }

    private static List<Object> array(Object value, String label) {
        require(value instanceof List<?>, label + " must be an array");
        @SuppressWarnings("unchecked")
        List<Object> result = (List<Object>) value;
        return result;
    }

    private static String string(Object value, String label) {
        require(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static long integer(Object value, String label) {
        require(value instanceof Long, label + " must be an integer");
        return (Long) value;
    }

    private static void equal(Object expected, Object actual, String label) {
        require(Objects.equals(expected, actual),
                label + " mismatch; expected " + expected + " but was " + actual);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(Object schemaObject, Object value, String path) {
            Map<String, Object> schema = object(schemaObject, "schema at " + path);
            Object typeObject = schema.get("type");
            if (typeObject != null) {
                String type = string(typeObject, "schema type at " + path);
                switch (type) {
                    case "object" -> validateObject(schema, value, path);
                    case "array" -> validateArray(schema, value, path);
                    case "string" -> validateString(schema, value, path);
                    case "integer" -> validateInteger(schema, value, path);
                    case "boolean" -> require(value instanceof Boolean, path + " must be a boolean");
                    default -> throw new AssertionError("unsupported schema type " + type + " at " + path);
                }
            }
            if (schema.containsKey("const")) {
                equal(schema.get("const"), value, path + " const");
            }
        }

        private static void validateObject(Map<String, Object> schema, Object value, String path) {
            Map<String, Object> instance = object(value, path);
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), "properties schema at " + path)
                    : Map.of();
            if (schema.containsKey("required")) {
                for (Object required : array(schema.get("required"), "required schema at " + path)) {
                    String name = string(required, "required property at " + path);
                    require(instance.containsKey(name), path + " is missing required property " + name);
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : instance.keySet()) {
                    require(properties.containsKey(key), path + " has unexpected property " + key);
                }
            }
            for (Map.Entry<String, Object> property : properties.entrySet()) {
                if (instance.containsKey(property.getKey())) {
                    validate(property.getValue(), instance.get(property.getKey()), path + "." + property.getKey());
                }
            }
        }

        private static void validateArray(Map<String, Object> schema, Object value, String path) {
            List<Object> instance = array(value, path);
            if (schema.containsKey("minItems")) {
                long minimum = integer(schema.get("minItems"), "minItems at " + path);
                require(instance.size() >= minimum, path + " must contain at least " + minimum + " items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                require(new HashSet<>(instance).size() == instance.size(), path + " must contain unique items");
            }
            if (schema.containsKey("items")) {
                for (int i = 0; i < instance.size(); i++) {
                    validate(schema.get("items"), instance.get(i), path + "[" + i + "]");
                }
            }
        }

        private static void validateString(Map<String, Object> schema, Object value, String path) {
            String instance = string(value, path);
            if (schema.containsKey("minLength")) {
                long minimum = integer(schema.get("minLength"), "minLength at " + path);
                require(instance.length() >= minimum, path + " is shorter than " + minimum);
            }
        }

        private static void validateInteger(Map<String, Object> schema, Object value, String path) {
            long instance = integer(value, path);
            if (schema.containsKey("minimum")) {
                long minimum = integer(schema.get("minimum"), "minimum at " + path);
                require(instance >= minimum, path + " must be at least " + minimum);
            }
        }
    }

    private static final class Json {
        private final String source;
        private int index;

        private Json(String source) {
            this.source = source;
        }

        static Object parse(String source) {
            Json parser = new Json(source);
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (parser.index != parser.source.length()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        private Object readValue() {
            skipWhitespace();
            if (index >= source.length()) {
                throw error("unexpected end of input");
            }
            char current = source.charAt(index);
            return switch (current) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> {
                    if (current == '-' || Character.isDigit(current)) {
                        yield readNumber();
                    }
                    throw error("unexpected character " + current);
                }
            };
        }

        private Map<String, Object> readObject() {
            expect('{');
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            skipWhitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                require(!result.containsKey(key), "duplicate JSON key " + key);
                skipWhitespace();
                expect(':');
                result.put(key, readValue());
                skipWhitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> readArray() {
            expect('[');
            ArrayList<Object> result = new ArrayList<>();
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
            while (index < source.length()) {
                char current = source.charAt(index++);
                if (current == '"') {
                    return result.toString();
                }
                if (current == '\\') {
                    if (index >= source.length()) {
                        throw error("unterminated escape");
                    }
                    char escaped = source.charAt(index++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (index + 4 > source.length()) {
                                throw error("short unicode escape");
                            }
                            String hex = source.substring(index, index + 4);
                            try {
                                result.append((char) Integer.parseInt(hex, 16));
                            } catch (NumberFormatException exception) {
                                throw error("invalid unicode escape " + hex);
                            }
                            index += 4;
                        }
                        default -> throw error("invalid escape " + escaped);
                    }
                } else {
                    require(current >= 0x20, "unescaped control character in JSON string");
                    result.append(current);
                }
            }
            throw error("unterminated string");
        }

        private Object readLiteral(String literal, Object value) {
            if (!source.startsWith(literal, index)) {
                throw error("expected " + literal);
            }
            index += literal.length();
            return value;
        }

        private Long readNumber() {
            int start = index;
            if (take('-')) {
                // sign consumed
            }
            require(index < source.length() && Character.isDigit(source.charAt(index)),
                    "invalid JSON integer at offset " + index);
            if (source.charAt(index) == '0') {
                index++;
            } else {
                while (index < source.length() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            }
            if (index < source.length()
                    && (source.charAt(index) == '.' || source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                throw error("only integers are supported by this installer specification");
            }
            try {
                return Long.parseLong(source.substring(start, index));
            } catch (NumberFormatException exception) {
                throw error("invalid integer");
            }
        }

        private void skipWhitespace() {
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
                throw error("expected " + expected);
            }
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at JSON offset " + index);
        }
    }
}
