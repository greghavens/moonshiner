import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Protected acceptance harness for the VCF management migration architecture artifact. */
public final class TestMain {
    private static final String INVENTORY_SHA256 =
            "d89a6fdfd4435f96c2ee1f28d386c0990f05f12c4f8546da62c75d5d7c24e5dd";
    private static final String SNAPSHOT_SHA256 =
            "455ef8ab4d23a3ca08f8be334e689b025c6f935ef70a6a63fec67588f5f46477";
    private static final String SPECIFICATION_SHA256 =
            "e8e8c799e426671affb1bfed25521a3ff342f10afe92de2a88356585a2dcd177";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String inventoryText = Files.readString(Path.of("estate-inventory.json"));
        String snapshotText = Files.readString(Path.of("compatibility-snapshot.json"));
        String specificationText = Files.readString(Path.of("installer-specification.json"));

        String artifactText = MigrationPlanClient.buildPlan(
                inventoryText, snapshotText, specificationText);
        Object artifact = Json.parse(artifactText);
        Map<String, Object> specification = object(Json.parse(specificationText), "specification");

        // Contract requirement: schema validation is the verifier's first artifact check.
        SchemaValidator.validate(artifact, specification.get("planSchema"), "$artifact");

        verifyProtectedInputs();
        verifyArchitecture(
                object(artifact, "artifact"),
                object(Json.parse(inventoryText), "inventory"),
                object(Json.parse(snapshotText), "snapshot"));
        verifyInputDriven(
                inventoryText,
                snapshotText,
                specificationText,
                specification.get("planSchema"));
        verifyInvalidInputs(inventoryText, snapshotText, specificationText);

        String second = MigrationPlanClient.buildPlan(
                inventoryText, snapshotText, specificationText);
        equal(artifactText, second, "identical inputs must produce byte-identical JSON");
        System.out.println("PASS: schema-valid VCF management migration architecture");
    }

    private static void verifyProtectedInputs() throws Exception {
        equal(INVENTORY_SHA256, sha256(Path.of("estate-inventory.json")),
                "protected estate inventory hash");
        equal(SNAPSHOT_SHA256, sha256(Path.of("compatibility-snapshot.json")),
                "protected compatibility snapshot hash");
        equal(SPECIFICATION_SHA256, sha256(Path.of("installer-specification.json")),
                "protected installer specification hash");
    }

    private static void verifyArchitecture(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        equal("1.0", plan.get("schemaVersion"), "artifact schema version");
        equal(inventory.get("estateId"), plan.get("estateId"), "estate id");

        Map<String, Object> site = object(inventory.get("site"), "inventory.site");
        Map<String, Object> expectedArchitecture =
                object(snapshot.get("architecture"), "snapshot.architecture");
        Map<String, Object> actualArchitecture =
                object(plan.get("architecture"), "artifact.architecture");

        equal(site.get("siteId"), actualArchitecture.get("siteId"), "site id");
        equal(site.get("topology"), actualArchitecture.get("topology"), "topology");
        equal(site.get("managementDomain"), actualArchitecture.get("managementDomain"),
                "management domain");
        equal(site.get("cluster"), actualArchitecture.get("cluster"), "cluster");
        equal(site.get("hostCount"), actualArchitecture.get("hostCount"), "fixture host count");
        equal(expectedArchitecture.get("minimumSupportedHostCount"),
                actualArchitecture.get("hostCount"), "minimum supported host count");
        equal(site.get("storage"), actualArchitecture.get("storage"), "storage");
        equal(snapshot.get("targetRelease"), actualArchitecture.get("targetRelease"),
                "target release");
        equal(expectedArchitecture.get("deploymentModel"),
                actualArchitecture.get("deploymentModel"), "deployment model");
        equal(expectedArchitecture.get("availabilityTradeoff"),
                actualArchitecture.get("availabilityTradeoff"), "availability tradeoff");

        List<Object> expectedPlacements =
                array(expectedArchitecture.get("placements"), "snapshot placements");
        List<Object> actualPlacements =
                array(actualArchitecture.get("placements"), "artifact placements");
        equal(expectedPlacements.size(), actualPlacements.size(), "placement count");
        String placement = string(
                expectedArchitecture.get("managementDomainPlacement"), "management placement");
        for (int index = 0; index < expectedPlacements.size(); index++) {
            Map<String, Object> expected = new LinkedHashMap<>(
                    object(expectedPlacements.get(index), "expected placement"));
            expected.put("placement", placement);
            equal(expected, object(actualPlacements.get(index), "actual placement"),
                    "placement " + index);
        }

        equal(snapshot.get("supportBoundaries"), plan.get("supportBoundaries"),
                "support boundaries");

        List<Object> inventoryProducts = array(inventory.get("products"), "inventory products");
        List<Object> paths = array(snapshot.get("migrationPaths"), "snapshot migration paths");
        List<Object> steps = array(plan.get("steps"), "artifact steps");
        equal(inventoryProducts.size(), paths.size(), "one pinned path per inventory product");
        equal(paths.size(), steps.size(), "one artifact step per pinned path");

        Set<String> seenProducts = new LinkedHashSet<>();
        Set<String> seenStepIds = new LinkedHashSet<>();
        for (int index = 0; index < paths.size(); index++) {
            Map<String, Object> product = object(inventoryProducts.get(index), "inventory product");
            Map<String, Object> path = object(paths.get(index), "snapshot migration path");
            Map<String, Object> step = object(steps.get(index), "artifact migration step");

            equal(BigDecimal.valueOf(index + 1L), step.get("order"), "contiguous step order");
            equal(path.get("order"), step.get("order"), "pinned step order");
            equal(path.get("stepId"), step.get("stepId"), "step id");
            check(seenStepIds.add(string(step.get("stepId"), "step id")),
                    "duplicate step id");

            Map<String, Object> source = object(step.get("source"), "step source");
            equal(product.get("id"), source.get("productId"), "source fixture id");
            equal(product.get("product"), source.get("product"), "source fixture product");
            equal(product.get("version"), source.get("version"), "source fixture version");
            equal(path.get("sourceProductId"), source.get("productId"), "pinned source id");
            equal(path.get("sourceProduct"), source.get("product"), "pinned source product");
            equal(path.get("sourceVersion"), source.get("version"), "pinned source version");
            check(seenProducts.add(string(source.get("productId"), "source product id")),
                    "inventory product appears in more than one step");

            Map<String, Object> target = object(step.get("target"), "step target");
            equal(path.get("targetComponent"), target.get("component"), "target component");
            equal(path.get("targetVersion"), target.get("version"), "target version");
            equal(path.get("migrationMode"), step.get("migrationMode"), "migration mode");
            equal(path.get("dependsOn"), step.get("dependsOn"), "dependencies");
            equal(path.get("carriesForward"), step.get("carriesForward"),
                    "content carried forward");
            equal(path.get("abandoned"), step.get("abandoned"), "content abandoned");
            equal(path.get("gates"), step.get("gates"), "step gates");
            equal(path.get("actions"), step.get("actions"), "ordered step actions");

            for (Object dependency : array(step.get("dependsOn"), "step dependencies")) {
                check(seenStepIds.contains(string(dependency, "dependency")),
                        "step depends on a later or unknown step: " + dependency);
            }
        }
        equal(inventoryProducts.size(), seenProducts.size(),
                "every inventory product must appear exactly once");

        // Deliberately no semantic inspection of artifact.research. Its shape was handled by
        // the installer's schema; genuine tool use and source choice remain trace requirements.
    }

    private static void verifyInputDriven(
            String inventoryText,
            String snapshotText,
            String specificationText,
            Object planSchema) {
        Map<String, Object> changedInventoryObject =
                object(Json.parse(inventoryText), "changed inventory");
        Map<String, Object> changedSnapshotObject =
                object(Json.parse(snapshotText), "changed snapshot");

        changedInventoryObject.put("estateId", "rainpole-dal-management-variant");
        Map<String, Object> changedSite = object(
                changedInventoryObject.get("site"), "changed inventory site");
        changedSite.put("siteId", "dal-variant-01");
        changedSite.put("managementDomain", "dal-mgmt-domain-variant");
        changedSite.put("cluster", "dal-mgmt-variant-01");
        changedSite.put("hostCount", BigDecimal.valueOf(5));
        changedSite.put("storage", "variant storage");

        Map<String, Object> changedArchitecture = object(
                changedSnapshotObject.get("architecture"), "changed snapshot architecture");
        changedSnapshotObject.put("targetRelease", "9.0.9");
        changedArchitecture.put("deploymentModel", "high-availability");
        changedArchitecture.put("minimumSupportedHostCount", BigDecimal.valueOf(5));
        changedArchitecture.put(
                "managementDomainPlacement", "dal-mgmt-domain-variant/dal-mgmt-variant-01");
        changedArchitecture.put("availabilityTradeoff", "variant availability tradeoff");
        for (Object value : array(changedArchitecture.get("placements"), "changed placements")) {
            Map<String, Object> placement = object(value, "changed placement");
            placement.put("component", placement.get("component") + " Variant");
            placement.put("role", placement.get("role") + "-variant");
            placement.put("version", "9.0.9");
            placement.put("nodeCount", number(placement.get("nodeCount"), "node count")
                    .add(BigDecimal.ONE));
            placement.put("size", placement.get("size") + "-variant");
            placement.put("vCpuPerNode", number(placement.get("vCpuPerNode"), "vCPU")
                    .add(BigDecimal.ONE));
            placement.put("memoryGiBPerNode", number(
                    placement.get("memoryGiBPerNode"), "memory").add(BigDecimal.ONE));
            placement.put("sizingBasis", placement.get("sizingBasis") + " [variant]");
        }

        List<Object> changedProducts = array(
                changedInventoryObject.get("products"), "changed inventory products");
        // Change input order as well as every product identity so a fixture-shaped hard-coded
        // response cannot satisfy the variant run.
        changedProducts.add(0, changedProducts.remove(1));
        for (Object value : changedProducts) {
            Map<String, Object> product = object(value, "changed inventory product");
            product.put("id", product.get("id") + "-variant");
            product.put("product", product.get("product") + " Variant");
            product.put("version", product.get("version") + "-variant");
            mutateStrings(array(product.get("content"), "changed product content"));
        }

        for (Object value : array(
                changedSnapshotObject.get("supportBoundaries"), "changed support boundaries")) {
            Map<String, Object> boundary = object(value, "changed support boundary");
            boundary.put("sourceProductId", boundary.get("sourceProductId") + "-variant");
            boundary.put("product", boundary.get("product") + " Variant");
            boundary.put("version", boundary.get("version") + "-variant");
            boundary.put("endOfGeneralSupport", "2028-11-12");
            boundary.put("requiredDisposition",
                    boundary.get("requiredDisposition") + " [variant]");
        }

        List<Object> changedPaths = array(
                changedSnapshotObject.get("migrationPaths"), "changed migration paths");
        changedPaths.add(0, changedPaths.remove(1));
        ArrayList<String> priorStepIds = new ArrayList<>();
        for (int index = 0; index < changedPaths.size(); index++) {
            Map<String, Object> path = object(changedPaths.get(index), "changed migration path");
            Map<String, Object> product = object(
                    changedProducts.get(index), "matching changed product");
            path.put("order", BigDecimal.valueOf(index + 1L));
            String stepId = path.get("stepId") + "-variant";
            path.put("stepId", stepId);
            path.put("sourceProductId", product.get("id"));
            path.put("sourceProduct", product.get("product"));
            path.put("sourceVersion", product.get("version"));
            path.put("targetComponent", path.get("targetComponent") + " Variant");
            path.put("targetVersion", "9.0.9");
            path.put("migrationMode", path.get("migrationMode") + "-variant");
            path.put("dependsOn", new ArrayList<>(priorStepIds));
            mutateStrings(array(path.get("carriesForward"), "changed carried content"));
            mutateStrings(array(path.get("abandoned"), "changed abandoned content"));
            mutateStrings(array(path.get("gates"), "changed gates"));
            mutateStrings(array(path.get("actions"), "changed actions"));
            priorStepIds.add(stepId);
        }

        String changedInventory = Json.write(changedInventoryObject);
        String changedSnapshot = Json.write(changedSnapshotObject);

        Object variantValue = Json.parse(MigrationPlanClient.buildPlan(
                changedInventory, changedSnapshot, specificationText));
        SchemaValidator.validate(variantValue, planSchema, "$variantArtifact");
        Map<String, Object> variant = object(variantValue, "variant artifact");
        verifyArchitecture(variant, changedInventoryObject, changedSnapshotObject);
    }

    private static void mutateStrings(List<Object> values) {
        for (int index = 0; index < values.size(); index++) {
            values.set(index, string(values.get(index), "variant string") + " [variant]");
        }
    }

    private static void verifyInvalidInputs(
            String inventoryText, String snapshotText, String specificationText) {
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                null, snapshotText, specificationText), "null inventory");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                inventoryText, null, specificationText), "null snapshot");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                inventoryText, snapshotText, null), "null specification");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                "{", snapshotText, specificationText), "malformed inventory");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                inventoryText, "{", specificationText), "malformed snapshot");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                inventoryText, snapshotText, "{"), "malformed specification");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                "[]", snapshotText, specificationText), "wrong-shaped inventory");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                inventoryText, "[]", specificationText), "wrong-shaped snapshot");
        expectIllegal(() -> MigrationPlanClient.buildPlan(
                inventoryText, snapshotText, "[]"), "wrong-shaped specification");
    }

    private static void expectIllegal(ThrowingRunnable action, String label) {
        try {
            action.run();
            throw new AssertionError(label + " should throw IllegalArgumentException");
        } catch (IllegalArgumentException expected) {
            // expected
        } catch (Exception other) {
            throw new AssertionError(label + " threw the wrong exception", other);
        }
    }

    private static String sha256(Path path) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));
        StringBuilder result = new StringBuilder(digest.length * 2);
        for (byte value : digest) {
            result.append(String.format("%02x", value & 0xff));
        }
        return result.toString();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?>)) {
            throw new AssertionError(label + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        if (!(value instanceof List<?>)) {
            throw new AssertionError(label + " must be an array");
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String label) {
        if (!(value instanceof String text)) {
            throw new AssertionError(label + " must be a string");
        }
        return text;
    }

    private static BigDecimal number(Object value, String label) {
        if (!(value instanceof BigDecimal number)) {
            throw new AssertionError(label + " must be a number");
        }
        return number;
    }

    private static void equal(Object expected, Object actual, String label) {
        if (!java.util.Objects.equals(expected, actual)) {
            throw new AssertionError(label + "\nexpected: " + expected + "\nactual:   " + actual);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    /** The subset of JSON Schema used by installer-specification.json. */
    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(Object instance, Object schemaValue, String path) {
            Map<String, Object> schema = object(schemaValue, path + " schema");
            if (schema.containsKey("const")) {
                equal(schema.get("const"), instance, path + " const");
            }
            if (schema.containsKey("enum")) {
                check(array(schema.get("enum"), path + " enum").contains(instance),
                        path + " is outside the schema enum");
            }

            Object typeValue = schema.get("type");
            if (typeValue instanceof String type) {
                check(matchesType(instance, type), path + " must have type " + type);
            }

            if (instance instanceof Map<?, ?> rawObject) {
                @SuppressWarnings("unchecked")
                Map<String, Object> instanceObject = (Map<String, Object>) rawObject;
                Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?>
                        ? object(schema.get("properties"), path + " properties")
                        : Map.of();
                if (schema.get("required") instanceof List<?>) {
                    for (Object required : array(schema.get("required"), path + " required")) {
                        String name = string(required, path + " required member");
                        check(instanceObject.containsKey(name), path + " is missing required member " + name);
                    }
                }
                if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    for (String name : instanceObject.keySet()) {
                        check(properties.containsKey(name), path + " has additional member " + name);
                    }
                }
                for (Map.Entry<String, Object> property : properties.entrySet()) {
                    if (instanceObject.containsKey(property.getKey())) {
                        validate(instanceObject.get(property.getKey()), property.getValue(),
                                path + "." + property.getKey());
                    }
                }
            }

            if (instance instanceof List<?> list) {
                int minimum = integerKeyword(schema.get("minItems"), 0);
                check(list.size() >= minimum, path + " has fewer than minItems");
                if (schema.containsKey("maxItems")) {
                    check(list.size() <= integerKeyword(schema.get("maxItems"), Integer.MAX_VALUE),
                            path + " has more than maxItems");
                }
                if (schema.containsKey("items")) {
                    for (int index = 0; index < list.size(); index++) {
                        validate(list.get(index), schema.get("items"), path + "[" + index + "]");
                    }
                }
            }

            if (instance instanceof String text) {
                int minimum = integerKeyword(schema.get("minLength"), 0);
                check(text.codePointCount(0, text.length()) >= minimum,
                        path + " is shorter than minLength");
                if (schema.get("pattern") instanceof String regex) {
                    check(Pattern.compile(regex).matcher(text).find(),
                            path + " does not match schema pattern");
                }
            }

            if (instance instanceof BigDecimal number && schema.get("minimum") instanceof BigDecimal min) {
                check(number.compareTo(min) >= 0, path + " is below schema minimum");
            }
        }

        private static boolean matchesType(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "integer" -> value instanceof BigDecimal decimal
                        && decimal.stripTrailingZeros().scale() <= 0;
                case "number" -> value instanceof BigDecimal;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported schema type in protected spec: " + type);
            };
        }

        private static int integerKeyword(Object value, int fallback) {
            if (value == null) {
                return fallback;
            }
            if (!(value instanceof BigDecimal decimal)
                    || decimal.stripTrailingZeros().scale() > 0) {
                throw new AssertionError("schema integer keyword is invalid: " + value);
            }
            return decimal.intValueExact();
        }
    }

    /** Strict JSON parser used for the protected inputs and returned artifact. */
    private static final class Json {
        private final String text;
        private int offset;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            if (text == null) {
                throw new AssertionError("artifact must not be null");
            }
            Json parser = new Json(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.offset != text.length()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        static String write(Object value) {
            StringBuilder output = new StringBuilder();
            append(value, output);
            return output.toString();
        }

        private static void append(Object value, StringBuilder output) {
            if (value == null) {
                output.append("null");
            } else if (value instanceof String text) {
                appendString(text, output);
            } else if (value instanceof BigDecimal number) {
                output.append(number.toPlainString());
            } else if (value instanceof Boolean bool) {
                output.append(bool);
            } else if (value instanceof Map<?, ?> object) {
                output.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : object.entrySet()) {
                    if (!(entry.getKey() instanceof String key)) {
                        throw new AssertionError("JSON object member name must be a string");
                    }
                    if (!first) {
                        output.append(',');
                    }
                    first = false;
                    appendString(key, output);
                    output.append(':');
                    append(entry.getValue(), output);
                }
                output.append('}');
            } else if (value instanceof List<?> list) {
                output.append('[');
                for (int index = 0; index < list.size(); index++) {
                    if (index > 0) {
                        output.append(',');
                    }
                    append(list.get(index), output);
                }
                output.append(']');
            } else {
                throw new AssertionError("unsupported JSON value: " + value.getClass());
            }
        }

        private static void appendString(String text, StringBuilder output) {
            output.append('"');
            for (int index = 0; index < text.length(); index++) {
                char value = text.charAt(index);
                switch (value) {
                    case '"' -> output.append("\\\"");
                    case '\\' -> output.append("\\\\");
                    case '\b' -> output.append("\\b");
                    case '\f' -> output.append("\\f");
                    case '\n' -> output.append("\\n");
                    case '\r' -> output.append("\\r");
                    case '\t' -> output.append("\\t");
                    default -> {
                        if (value < 0x20) {
                            output.append(String.format("\\u%04x", (int) value));
                        } else {
                            output.append(value);
                        }
                    }
                }
            }
            output.append('"');
        }

        private Object value() {
            whitespace();
            if (offset >= text.length()) {
                throw error("expected value");
            }
            return switch (text.charAt(offset)) {
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
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (offset >= text.length() || text.charAt(offset) != '"') {
                    throw error("expected object member name");
                }
                String name = stringValue();
                if (result.containsKey(name)) {
                    throw error("duplicate object member " + name);
                }
                whitespace();
                expect(':');
                result.put(name, value());
                whitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            expect('[');
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

        private String stringValue() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (offset < text.length()) {
                char value = text.charAt(offset++);
                if (value == '"') {
                    return result.toString();
                }
                if (value == '\\') {
                    if (offset >= text.length()) {
                        throw error("unfinished escape");
                    }
                    char escaped = text.charAt(offset++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicodeEscape());
                        default -> throw error("invalid escape");
                    }
                } else {
                    if (value < 0x20) {
                        throw error("unescaped control character");
                    }
                    result.append(value);
                }
            }
            throw error("unterminated string");
        }

        private char unicodeEscape() {
            if (offset + 4 > text.length()) {
                throw error("short unicode escape");
            }
            int value = 0;
            for (int index = 0; index < 4; index++) {
                int digit = Character.digit(text.charAt(offset++), 16);
                if (digit < 0) {
                    throw error("invalid unicode escape");
                }
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private Object numberValue() {
            int start = offset;
            if (take('-')) {
                // sign consumed
            }
            if (take('0')) {
                if (offset < text.length() && Character.isDigit(text.charAt(offset))) {
                    throw error("leading zero");
                }
            } else {
                digits();
            }
            if (take('.')) {
                digits();
            }
            if (offset < text.length() && (text.charAt(offset) == 'e' || text.charAt(offset) == 'E')) {
                offset++;
                if (!take('+')) {
                    take('-');
                }
                digits();
            }
            try {
                return new BigDecimal(text.substring(start, offset));
            } catch (NumberFormatException exception) {
                throw error("invalid number");
            }
        }

        private void digits() {
            int start = offset;
            while (offset < text.length() && Character.isDigit(text.charAt(offset))) {
                offset++;
            }
            if (offset == start) {
                throw error("expected digit");
            }
        }

        private Object literal(String literal, Object value) {
            if (!text.startsWith(literal, offset)) {
                throw error("invalid literal");
            }
            offset += literal.length();
            return value;
        }

        private void whitespace() {
            while (offset < text.length()) {
                char value = text.charAt(offset);
                if (value == ' ' || value == '\n' || value == '\r' || value == '\t') {
                    offset++;
                } else {
                    break;
                }
            }
        }

        private boolean take(char expected) {
            if (offset < text.length() && text.charAt(offset) == expected) {
                offset++;
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
            return new IllegalArgumentException(message + " at offset " + offset);
        }
    }
}
