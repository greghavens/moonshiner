/*
 * Protected acceptance harness for vcfarch-0095.
 *
 * Run from the workspace root with: java TestMain.java
 */
import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

public final class TestMain {
    private static final Path INVENTORY = Path.of("fixtures/estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("fixtures/compatibility-snapshot.json");
    private static final Path PLAN_SCHEMA = Path.of("schemas/migration-plan.schema.json");
    private static final Path INSTALLER_SPEC =
            Path.of("specifications/vcf-installer/vcf-installer-openapi.json");

    private static int checks;

    private TestMain() {}

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
        checks++;
    }

    private static void equal(Object actual, Object expected, String message) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(message + ": got " + actual + ", expected " + expected);
        }
        checks++;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String path) {
        if (!(value instanceof Map)) throw new AssertionError(path + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String path) {
        if (!(value instanceof List)) throw new AssertionError(path + " must be an array");
        return (List<Object>) value;
    }

    private static String string(Object value, String path) {
        if (!(value instanceof String)) throw new AssertionError(path + " must be a string");
        return (String) value;
    }

    private static long integer(Object value, String path) {
        if (!(value instanceof BigDecimal) || ((BigDecimal) value).scale() > 0) {
            throw new AssertionError(path + " must be an integer");
        }
        return ((BigDecimal) value).longValueExact();
    }

    private static Map<String, Object> readObject(Path path) throws Exception {
        return object(Json.parse(Files.readString(path, StandardCharsets.UTF_8)), path.toString());
    }

    private static List<String> strings(Object value, String path) {
        List<Object> raw = array(value, path);
        List<String> result = new ArrayList<>();
        for (int i = 0; i < raw.size(); i++) result.add(string(raw.get(i), path + "[" + i + "]"));
        return result;
    }

    private static Map<String, Object> resolvePointer(Map<String, Object> root, String ref) {
        if (!ref.startsWith("#/")) throw new AssertionError("unsupported non-local schema ref: " + ref);
        Object current = root;
        for (String token : ref.substring(2).split("/")) {
            String decoded = token.replace("~1", "/").replace("~0", "~");
            current = object(current, ref).get(decoded);
            if (current == null) throw new AssertionError("unresolved schema ref: " + ref);
        }
        return object(current, ref);
    }

    /** Small self-contained validator for the JSON Schema keywords used by the pinned OpenAPI. */
    private static void validateSchema(Object instance, Map<String, Object> schema,
                                       Map<String, Object> schemaRoot, String path) {
        if (schema.containsKey("$ref")) {
            validateSchema(instance, resolvePointer(schemaRoot, string(schema.get("$ref"), "$ref")),
                    schemaRoot, path);
            return;
        }
        if (schema.containsKey("allOf")) {
            for (Object branch : array(schema.get("allOf"), path + " allOf")) {
                validateSchema(instance, object(branch, path + " allOf branch"), schemaRoot, path);
            }
        }
        if (schema.containsKey("anyOf")) {
            boolean matched = false;
            for (Object branch : array(schema.get("anyOf"), path + " anyOf")) {
                try {
                    validateSchema(instance, object(branch, path + " anyOf branch"), schemaRoot, path);
                    matched = true;
                    break;
                } catch (AssertionError ignored) {
                    // Try the next schema branch.
                }
            }
            if (!matched) throw new AssertionError(path + " does not match anyOf");
        }
        if (schema.containsKey("oneOf")) {
            int matches = 0;
            for (Object branch : array(schema.get("oneOf"), path + " oneOf")) {
                try {
                    validateSchema(instance, object(branch, path + " oneOf branch"), schemaRoot, path);
                    matches++;
                } catch (AssertionError ignored) {
                    // Count every matching schema branch.
                }
            }
            if (matches != 1) throw new AssertionError(path + " must match exactly one oneOf branch");
        }

        if (schema.containsKey("const") && !schema.get("const").equals(instance)) {
            throw new AssertionError(path + " violates const");
        }
        if (schema.containsKey("enum") && !array(schema.get("enum"), path + " enum").contains(instance)) {
            throw new AssertionError(path + " is outside enum");
        }

        String type = schema.get("type") instanceof String ? (String) schema.get("type") : null;
        if (type != null) {
            boolean rightType = switch (type) {
                case "object" -> instance instanceof Map;
                case "array" -> instance instanceof List;
                case "string" -> instance instanceof String;
                case "integer" -> instance instanceof BigDecimal
                        && ((BigDecimal) instance).stripTrailingZeros().scale() <= 0;
                case "number" -> instance instanceof BigDecimal;
                case "boolean" -> instance instanceof Boolean;
                case "null" -> instance == null;
                default -> throw new AssertionError("unsupported schema type " + type);
            };
            if (!rightType) throw new AssertionError(path + " must have schema type " + type);
        }

        if (instance instanceof Map) {
            Map<String, Object> map = object(instance, path);
            if (schema.containsKey("required")) {
                for (String key : strings(schema.get("required"), path + " required")) {
                    if (!map.containsKey(key)) throw new AssertionError(path + " missing required property " + key);
                }
            }
            Map<String, Object> properties = schema.get("properties") instanceof Map
                    ? object(schema.get("properties"), path + " properties") : Map.of();
            for (Map.Entry<String, Object> property : properties.entrySet()) {
                if (map.containsKey(property.getKey())) {
                    validateSchema(map.get(property.getKey()),
                            object(property.getValue(), path + "." + property.getKey() + " schema"),
                            schemaRoot, path + "." + property.getKey());
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : map.keySet()) {
                    if (!properties.containsKey(key)) {
                        throw new AssertionError(path + " has additional property " + key);
                    }
                }
            }
        }

        if (instance instanceof List) {
            List<Object> list = array(instance, path);
            if (schema.get("minItems") instanceof BigDecimal
                    && list.size() < ((BigDecimal) schema.get("minItems")).intValueExact()) {
                throw new AssertionError(path + " has too few items");
            }
            if (schema.get("maxItems") instanceof BigDecimal
                    && list.size() > ((BigDecimal) schema.get("maxItems")).intValueExact()) {
                throw new AssertionError(path + " has too many items");
            }
            if (schema.get("items") instanceof Map) {
                Map<String, Object> itemSchema = object(schema.get("items"), path + " items schema");
                for (int i = 0; i < list.size(); i++) {
                    validateSchema(list.get(i), itemSchema, schemaRoot, path + "[" + i + "]");
                }
            }
        }

        if (instance instanceof String) {
            String value = (String) instance;
            if (schema.get("minLength") instanceof BigDecimal
                    && value.codePointCount(0, value.length())
                    < ((BigDecimal) schema.get("minLength")).intValueExact()) {
                throw new AssertionError(path + " is shorter than minLength");
            }
            if (schema.get("maxLength") instanceof BigDecimal
                    && value.codePointCount(0, value.length())
                    > ((BigDecimal) schema.get("maxLength")).intValueExact()) {
                throw new AssertionError(path + " is longer than maxLength");
            }
            if (schema.get("pattern") instanceof String
                    && !Pattern.compile((String) schema.get("pattern")).matcher(value).find()) {
                throw new AssertionError(path + " does not match pattern");
            }
        }

        if (instance instanceof BigDecimal) {
            BigDecimal value = (BigDecimal) instance;
            if (schema.get("minimum") instanceof BigDecimal
                    && value.compareTo((BigDecimal) schema.get("minimum")) < 0) {
                throw new AssertionError(path + " is below minimum");
            }
            if (schema.get("maximum") instanceof BigDecimal
                    && value.compareTo((BigDecimal) schema.get("maximum")) > 0) {
                throw new AssertionError(path + " is above maximum");
            }
        }
    }

    private static List<Map<String, Object>> inventoryComponents(Map<String, Object> inventory) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (String domainKey : List.of("managementDomain", "workloadDomain")) {
            Map<String, Object> domain = object(inventory.get(domainKey), domainKey);
            String domainId = string(domain.get("domainId"), domainKey + ".domainId");
            for (Object raw : array(domain.get("components"), domainKey + ".components")) {
                Map<String, Object> component = new LinkedHashMap<>(object(raw, domainKey + " component"));
                component.put("domainId", domainId);
                result.add(component);
            }
        }
        return result;
    }

    private static void checkInstallerEnvelope(Map<String, Object> plan,
                                               Map<String, Object> inventory) {
        Map<String, Object> envelope = object(inventory.get("installerEnvelope"), "installerEnvelope");
        for (Map.Entry<String, Object> entry : envelope.entrySet()) {
            equal(plan.get(entry.getKey()), entry.getValue(),
                    "SddcSpec field must come from installerEnvelope: " + entry.getKey());
        }
    }

    private static void checkReleasePath(Map<String, Object> plan,
                                         Map<String, Object> snapshot) {
        String source = string(snapshot.get("sourceRelease"), "sourceRelease");
        String target = string(snapshot.get("targetRelease"), "targetRelease");
        List<String> path = strings(plan.get("releasePath"), "releasePath");
        equal(path, List.of(source, target), "releasePath must use the pinned direct edge");
        boolean supported = false;
        for (Object raw : array(snapshot.get("supportedReleaseEdges"), "supportedReleaseEdges")) {
            Map<String, Object> edge = object(raw, "supported release edge");
            if (source.equals(edge.get("from")) && target.equals(edge.get("to"))) supported = true;
        }
        check(supported, "pinned direct release edge must be supported");
    }

    private static void checkComponents(Map<String, Object> plan,
                                        Map<String, Object> inventory,
                                        Map<String, Object> snapshot) {
        List<Map<String, Object>> expected = inventoryComponents(inventory);
        List<Object> actual = array(plan.get("components"), "components");
        equal(actual.size(), expected.size(), "every inventory component appears exactly once");
        Map<String, Object> targets = object(snapshot.get("componentTargets"), "componentTargets");
        Map<String, Object> rules = object(snapshot.get("componentRules"), "componentRules");
        Set<String> seen = new LinkedHashSet<>();
        String managementId = string(object(inventory.get("managementDomain"), "managementDomain")
                .get("domainId"), "managementDomain.domainId");

        for (int i = 0; i < expected.size(); i++) {
            Map<String, Object> source = expected.get(i);
            Map<String, Object> emitted = object(actual.get(i), "components[" + i + "]");
            String id = string(source.get("id"), "inventory component id");
            check(seen.add(id), "duplicate component id " + id);
            equal(emitted.get("id"), id, "component order/id " + id);
            equal(emitted.get("name"), source.get("name"), "component name " + id);
            equal(emitted.get("domainId"), source.get("domainId"), "component domain " + id);
            equal(emitted.get("kind"), source.get("kind"), "component kind " + id);
            equal(emitted.get("currentVersion"), source.get("version"), "component currentVersion " + id);
            Map<String, Object> rule = object(rules.get(id), "componentRules." + id);
            equal(emitted.get("action"), rule.get("action"), "component action " + id);
            equal(emitted.get("gates"), rule.get("gates"), "component gates " + id);
            String expectedTarget = managementId.equals(source.get("domainId"))
                    ? string(source.get("version"), "management current version")
                    : string(targets.get(string(source.get("kind"), "component kind")), "component target");
            equal(emitted.get("targetVersion"), expectedTarget, "component targetVersion " + id);
        }
        equal(seen.size(), expected.size(), "component ids must be unique");
    }

    private static void checkSteps(Map<String, Object> plan,
                                   Map<String, Object> inventory,
                                   Map<String, Object> snapshot) {
        List<Object> expected = array(snapshot.get("orderedSteps"), "orderedSteps");
        List<Object> actual = array(plan.get("steps"), "steps");
        equal(actual.size(), expected.size(), "ordered step count");
        Set<String> managementComponents = new HashSet<>();
        Map<String, Object> management = object(inventory.get("managementDomain"), "managementDomain");
        for (Object raw : array(management.get("components"), "managementDomain.components")) {
            managementComponents.add(string(object(raw, "management component").get("id"), "component id"));
        }
        Set<String> priorSteps = new LinkedHashSet<>();
        Set<String> externalGates = new HashSet<>(strings(snapshot.get("externalGates"), "externalGates"));
        Set<String> steppedComponents = new LinkedHashSet<>();

        for (int i = 0; i < expected.size(); i++) {
            Map<String, Object> want = object(expected.get(i), "orderedSteps[" + i + "]");
            Map<String, Object> got = object(actual.get(i), "steps[" + i + "]");
            equal(integer(got.get("order"), "steps[" + i + "].order"), (long) i + 1,
                    "step order");
            equal(got.get("id"), want.get("id"), "step id at order " + (i + 1));
            equal(got.get("action"), want.get("action"), "step action " + want.get("id"));
            equal(got.get("componentIds"), want.get("componentIds"),
                    "step components " + want.get("id"));
            equal(got.get("gates"), want.get("gates"), "step gates " + want.get("id"));
            for (String componentId : strings(got.get("componentIds"), "step componentIds")) {
                check(!managementComponents.contains(componentId),
                        "management component must not occur in a migration step: " + componentId);
                check(steppedComponents.add(componentId),
                        "workload component occurs in more than one step: " + componentId);
            }
            for (String gate : strings(got.get("gates"), "step gates")) {
                check(priorSteps.contains(gate) || externalGates.contains(gate),
                        "step gate must be an external gate or an earlier step: " + gate);
            }
            priorSteps.add(string(got.get("id"), "step id"));
        }

        Set<String> workloadComponents = new LinkedHashSet<>();
        Map<String, Object> workload = object(inventory.get("workloadDomain"), "workloadDomain");
        for (Object raw : array(workload.get("components"), "workloadDomain.components")) {
            workloadComponents.add(string(object(raw, "workload component").get("id"), "component id"));
        }
        equal(steppedComponents, workloadComponents,
                "every workload component must be assigned to exactly one ordered step");
    }

    private static void checkResearch(Map<String, Object> plan) {
        List<Object> sources = array(plan.get("researchConsulted"), "researchConsulted");
        check(sources.size() >= 2,
                "researchConsulted must cover compatibility, upgrade guidance, and installer spec");
        boolean hasCompatibility = false;
        boolean hasUpgradeGuidance = false;
        boolean hasTaggedInstallerSpec = false;

        for (int i = 0; i < sources.size(); i++) {
            Map<String, Object> source = object(sources.get(i), "researchConsulted[" + i + "]");
            String title = string(source.get("title"), "researchConsulted[" + i + "].title");
            String url = string(source.get("url"), "researchConsulted[" + i + "].url");
            check(!title.isBlank(), "research source title must not be blank");
            check(url.equals(url.trim()), "research source URL must not contain outer whitespace");
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException invalid) {
                throw new AssertionError("research source URL must be a valid URI: " + url, invalid);
            }
            String scheme = uri.getScheme();
            String host = uri.getHost();
            check(("https".equalsIgnoreCase(scheme) || "http".equalsIgnoreCase(scheme))
                            && host != null,
                    "research source must be an absolute HTTP(S) URL: " + url);

            String lowerHost = host.toLowerCase();
            String description = (title + " " + url).toLowerCase();
            boolean broadcomPublished = lowerHost.equals("broadcom.com")
                    || lowerHost.endsWith(".broadcom.com");
            if (broadcomPublished && (description.contains("compatib")
                    || description.contains("interoperab")
                    || description.contains("correlat")
                    || description.contains("constituent product"))) {
                hasCompatibility = true;
            }
            if (broadcomPublished && (description.contains("upgrade")
                    || description.contains("update-path"))) {
                hasUpgradeGuidance = true;
            }
            String path = uri.getPath() == null ? "" : uri.getPath().toLowerCase();
            if ((lowerHost.equals("github.com") || lowerHost.equals("raw.githubusercontent.com"))
                    && path.contains("/vmware/vcf-api-specs/")
                    && path.contains("9.1.0.0")
                    && path.endsWith("/specifications/vcf-installer/vcf-installer-openapi.json")) {
                hasTaggedInstallerSpec = true;
            }
        }

        check(hasCompatibility,
                "researchConsulted must include Broadcom compatibility/interoperability material");
        check(hasUpgradeGuidance,
                "researchConsulted must include Broadcom 5.2.x-to-9.1 upgrade-path guidance");
        check(hasTaggedInstallerSpec,
                "researchConsulted must include vcf-api-specs installer OpenAPI at tag 9.1.0.0");
    }

    private static void checkPlan(Map<String, Object> plan,
                                  Map<String, Object> inventory,
                                  Map<String, Object> snapshot) {
        equal(plan.get("schemaVersion"), "1.0", "schemaVersion");
        equal(plan.get("planId"), inventory.get("planId"), "planId");
        equal(plan.get("fleetId"), inventory.get("fleetId"), "fleetId");
        Map<String, Object> workload = object(inventory.get("workloadDomain"), "workloadDomain");
        equal(plan.get("domainId"), workload.get("domainId"), "domainId");
        equal(plan.get("targetVcfVersion"), inventory.get("targetVcfVersion"), "targetVcfVersion");
        equal(plan.get("compatibilitySnapshot"), snapshot.get("snapshotId"),
                "compatibility snapshot identity");
        equal(plan.get("managementDomainDisposition"), "UNCHANGED",
                "management domain disposition");
        checkInstallerEnvelope(plan, inventory);
        checkReleasePath(plan, snapshot);
        checkComponents(plan, inventory, snapshot);
        checkSteps(plan, inventory, snapshot);
        checkResearch(plan);
    }

    private static void checkArgumentCount(Path invalidCountOutput) throws Exception {
        for (int count = 0; count <= 5; count++) {
            if (count == 3) continue;
            String[] candidate = new String[count];
            String[] valid = {
                    INVENTORY.toString(), SNAPSHOT.toString(), invalidCountOutput.toString()
            };
            for (int i = 0; i < count; i++) {
                candidate[i] = i < valid.length ? valid[i] : "unexpected-extra-argument";
            }
            boolean rejected = false;
            try {
                VcfMigrationPlanner.main(candidate);
            } catch (Exception expected) {
                rejected = true;
            }
            check(rejected, "client must reject argument count " + count);
        }
    }

    public static void main(String[] args) throws Exception {
        Path temporary = Files.createTempDirectory("vcfarch-0095-");
        Path output = temporary.resolve("migration-plan.json");
        Path repeatedOutput = temporary.resolve("migration-plan-repeated.json");
        Path invalidCountOutput = temporary.resolve("invalid-count.json");
        try {
            checkArgumentCount(invalidCountOutput);
            VcfMigrationPlanner.main(new String[] {
                    INVENTORY.toString(), SNAPSHOT.toString(), output.toString()
            });
            VcfMigrationPlanner.main(new String[] {
                    INVENTORY.toString(), SNAPSHOT.toString(), repeatedOutput.toString()
            });
            byte[] planBytes = Files.readAllBytes(output);
            byte[] repeatedBytes = Files.readAllBytes(repeatedOutput);
            check(java.util.Arrays.equals(planBytes, repeatedBytes),
                    "same inputs must produce byte-for-byte deterministic output");
            String planText = StandardCharsets.UTF_8.newDecoder()
                    .decode(java.nio.ByteBuffer.wrap(planBytes)).toString();
            Object planDocument = Json.parse(planText);
            Map<String, Object> installer = readObject(INSTALLER_SPEC);
            Map<String, Object> installerSchemas = object(
                    object(installer.get("components"), "installer.components").get("schemas"),
                    "installer.components.schemas");
            Map<String, Object> sddcSpec = object(installerSchemas.get("SddcSpec"),
                    "installer.components.schemas.SddcSpec");

            // This is intentionally the first assertion about plan content.
            validateSchema(planDocument, sddcSpec, installer, "$plan");
            System.out.println("ok   artifact validates against installer SddcSpec 9.1.0.0");

            Map<String, Object> plan = object(planDocument, "$plan");
            Map<String, Object> migrationSchema = readObject(PLAN_SCHEMA);
            validateSchema(plan, migrationSchema, migrationSchema, "$plan");
            System.out.println("ok   artifact validates against migration-plan schema");

            Map<String, Object> inventory = readObject(INVENTORY);
            Map<String, Object> snapshot = readObject(SNAPSHOT);
            checkPlan(plan, inventory, snapshot);
            System.out.println("ok   workload migration matches pinned compatibility architecture");
            System.out.println("PASS " + checks + " checks");
        } finally {
            Files.deleteIfExists(output);
            Files.deleteIfExists(repeatedOutput);
            Files.deleteIfExists(invalidCountOutput);
            Files.deleteIfExists(temporary);
        }
    }

    /** Strict JSON parser used so verification remains dependency-free and offline. */
    private static final class Json {
        private final String text;
        private int index;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            Json parser = new Json(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.index != text.length()) {
                throw new IllegalArgumentException("trailing JSON at " + parser.index);
            }
            return value;
        }

        private void whitespace() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) index++;
        }

        private char peek() {
            if (index >= text.length()) throw new IllegalArgumentException("unexpected end of JSON");
            return text.charAt(index);
        }

        private void expect(char expected) {
            if (peek() != expected) {
                throw new IllegalArgumentException("expected '" + expected + "' at " + index);
            }
            index++;
        }

        private Object value() {
            whitespace();
            return switch (peek()) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Object literal(String token, Object value) {
            if (!text.startsWith(token, index)) {
                throw new IllegalArgumentException("invalid JSON token at " + index);
            }
            index += token.length();
            return value;
        }

        private Map<String, Object> object() {
            Map<String, Object> result = new LinkedHashMap<>();
            expect('{');
            whitespace();
            if (peek() == '}') {
                index++;
                return result;
            }
            while (true) {
                whitespace();
                String key = string();
                whitespace();
                expect(':');
                Object previous = result.put(key, value());
                if (previous != null) throw new IllegalArgumentException("duplicate JSON key " + key);
                whitespace();
                char next = peek();
                if (next == ',') {
                    index++;
                    continue;
                }
                expect('}');
                return result;
            }
        }

        private List<Object> array() {
            List<Object> result = new ArrayList<>();
            expect('[');
            whitespace();
            if (peek() == ']') {
                index++;
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (peek() == ',') {
                    index++;
                    continue;
                }
                expect(']');
                return result;
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (true) {
                if (index >= text.length()) throw new IllegalArgumentException("unterminated string");
                char current = text.charAt(index++);
                if (current == '"') return result.toString();
                if (current == '\\') {
                    if (index >= text.length()) throw new IllegalArgumentException("unterminated escape");
                    char escaped = text.charAt(index++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (index + 4 > text.length()) {
                                throw new IllegalArgumentException("short unicode escape");
                            }
                            result.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                            index += 4;
                        }
                        default -> throw new IllegalArgumentException("invalid escape at " + (index - 1));
                    }
                } else {
                    if (current < 0x20) throw new IllegalArgumentException("control character in string");
                    result.append(current);
                }
            }
        }

        private BigDecimal number() {
            int start = index;
            if (peek() == '-') index++;
            if (peek() == '0') {
                index++;
            } else {
                if (!Character.isDigit(peek())) throw new IllegalArgumentException("invalid number");
                while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            }
            if (index < text.length() && text.charAt(index) == '.') {
                index++;
                if (index >= text.length() || !Character.isDigit(text.charAt(index))) {
                    throw new IllegalArgumentException("invalid fraction");
                }
                while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            }
            if (index < text.length() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                index++;
                if (index < text.length() && (text.charAt(index) == '+' || text.charAt(index) == '-')) index++;
                if (index >= text.length() || !Character.isDigit(text.charAt(index))) {
                    throw new IllegalArgumentException("invalid exponent");
                }
                while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            }
            return new BigDecimal(text.substring(start, index));
        }
    }
}
