import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");
    private static final Path PLAN_SCHEMA = Path.of("migration-plan-schema.json");
    private static final Path INSTALLER_SPEC = Path.of(
            "specifications", "vcf-installer", "vcf-installer-openapi.json");
    private static final String INSTALLER_SPEC_SHA256 =
            "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String inventoryText = Files.readString(INVENTORY);
        String snapshotText = Files.readString(SNAPSHOT);
        String artifactText = MigrationPlanClient.buildPlan(inventoryText, snapshotText);
        Map<String, Object> artifact = object(Json.parse(artifactText), "artifact");

        // This is intentionally the first artifact validation stage. Compatibility,
        // inventory coverage, plan-schema checks, and research are not inspected first.
        byte[] installerSpecBytes = Files.readAllBytes(INSTALLER_SPEC);
        Map<String, Object> installerSpec = object(
                Json.parse(new String(installerSpecBytes, StandardCharsets.UTF_8)),
                "installer OpenAPI document");
        Object targetSddcSpec = required(artifact, "targetSddcSpec", "artifact");
        Map<String, Object> schemas = object(required(
                object(required(installerSpec, "components", "installer OpenAPI document"), "components"),
                "schemas", "components"), "components.schemas");
        Object sddcSchema = required(schemas, "SddcSpec", "components.schemas");
        new SchemaValidator(installerSpec).validate(targetSddcSpec, sddcSchema, "$.targetSddcSpec");

        require(INSTALLER_SPEC_SHA256.equals(sha256(installerSpecBytes)),
                "pinned installer OpenAPI document has changed");

        Map<String, Object> planSchema = object(
                Json.parse(Files.readString(PLAN_SCHEMA)), "migration plan schema");
        new SchemaValidator(planSchema).validate(artifact, planSchema, "$");

        Map<String, Object> inventory = object(Json.parse(inventoryText), "inventory");
        Map<String, Object> snapshot = object(Json.parse(snapshotText), "compatibility snapshot");
        verifyArchitecture(artifact, inventory, snapshot);

        System.out.println("PASS: SddcSpec schema, plan schema, research record, compatibility paths, gates, and targets validated");
    }

    private static void verifyArchitecture(
            Map<String, Object> plan,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        String estateId = string(required(inventory, "estateId", "inventory"), "inventory.estateId");
        String targetVersion = string(
                required(inventory, "targetVcfVersion", "inventory"), "inventory.targetVcfVersion");
        expectString(plan, "estateId", estateId, "artifact");
        expectString(plan, "targetVcfVersion", targetVersion, "artifact");
        expectString(snapshot, "targetVcfVersion", targetVersion, "compatibility snapshot");

        verifyTargetSddcSpec(
                object(required(plan, "targetSddcSpec", "artifact"), "artifact.targetSddcSpec"),
                object(required(inventory, "sddc", "inventory"), "inventory.sddc"),
                targetVersion);

        Map<String, Component> components = readInventoryComponents(inventory);
        Map<String, State> targetStates = readTargetStates(snapshot, components.keySet());
        Map<String, Gate> gates = readGates(snapshot, components.keySet());
        List<Map<String, Object>> transitions = objectList(
                required(snapshot, "transitions", "compatibility snapshot"),
                "compatibility snapshot.transitions");
        List<Map<String, Object>> steps = objectList(
                required(plan, "steps", "artifact"), "artifact.steps");

        Map<String, State> state = new HashMap<>();
        for (Component component : components.values()) {
            state.put(component.id, new State(component.product, component.version));
        }

        Set<String> stepIds = new HashSet<>();
        Set<String> coveredComponents = new HashSet<>();
        for (int index = 0; index < steps.size(); index++) {
            Map<String, Object> step = steps.get(index);
            String context = "artifact.steps[" + index + "]";
            long order = integer(required(step, "order", context), context + ".order");
            require(order == index + 1L, context + ".order must be contiguous and match array order");
            String stepId = string(required(step, "id", context), context + ".id");
            require(stepIds.add(stepId), "duplicate step id: " + stepId);

            String componentId = string(
                    required(step, "componentId", context), context + ".componentId");
            Component component = components.get(componentId);
            require(component != null, context + " references component outside inventory: " + componentId);
            expectString(step, "componentName", component.name, context);
            coveredComponents.add(componentId);

            State current = state.get(componentId);
            expectString(step, "fromProduct", current.product, context);
            expectString(step, "fromVersion", current.version, context);

            Map<String, Object> edge = findTransition(step, transitions, context);
            List<String> suppliedGates = strings(required(step, "gates", context), context + ".gates");
            Set<String> supplied = new HashSet<>(suppliedGates);
            Set<String> requiredGates = new HashSet<>(strings(
                    required(edge, "requiredGates", "transition"), "transition.requiredGates"));
            require(supplied.containsAll(requiredGates),
                    context + " omits required compatibility gates " + difference(requiredGates, supplied));
            for (String gateId : suppliedGates) {
                Gate gate = gates.get(gateId);
                require(gate != null, context + " names unknown gate: " + gateId);
                State gateState = state.get(gate.componentId);
                require(gateState != null
                                && gate.product.equals(gateState.product)
                                && gate.version.equals(gateState.version),
                        context + " runs before gate is true: " + gateId);
            }

            state.put(componentId, new State(
                    string(required(step, "toProduct", context), context + ".toProduct"),
                    string(required(step, "toVersion", context), context + ".toVersion")));
        }

        require(coveredComponents.equals(components.keySet()),
                "plan does not name every inventory component; missing "
                        + difference(components.keySet(), coveredComponents));
        for (String componentId : components.keySet()) {
            State actual = state.get(componentId);
            State expected = targetStates.get(componentId);
            require(actual.equals(expected),
                    "component " + componentId + " finishes at " + actual + " instead of " + expected);
        }
    }

    private static void verifyTargetSddcSpec(
            Map<String, Object> spec,
            Map<String, Object> inventorySddc,
            String targetVersion) {
        expectString(spec, "sddcId", string(
                required(inventorySddc, "sddcId", "inventory.sddc"), "inventory.sddc.sddcId"),
                "artifact.targetSddcSpec");
        expectString(spec, "workflowType", "VCF", "artifact.targetSddcSpec");
        expectString(spec, "version", targetVersion, "artifact.targetSddcSpec");

        Map<String, Object> expectedDns = object(
                required(inventorySddc, "dns", "inventory.sddc"), "inventory.sddc.dns");
        Map<String, Object> actualDns = object(
                required(spec, "dnsSpec", "artifact.targetSddcSpec"),
                "artifact.targetSddcSpec.dnsSpec");
        expectString(actualDns, "subdomain", string(
                required(expectedDns, "subdomain", "inventory.sddc.dns"),
                "inventory.sddc.dns.subdomain"), "artifact.targetSddcSpec.dnsSpec");
        require(required(actualDns, "nameservers", "artifact.targetSddcSpec.dnsSpec")
                        .equals(required(expectedDns, "nameservers", "inventory.sddc.dns")),
                "target SddcSpec must preserve inventory DNS nameservers");

        Map<String, Object> vcenter = object(
                required(spec, "vcenterSpec", "artifact.targetSddcSpec"),
                "artifact.targetSddcSpec.vcenterSpec");
        expectString(vcenter, "vcenterHostname", string(
                required(inventorySddc, "vcenterHostname", "inventory.sddc"),
                "inventory.sddc.vcenterHostname"), "artifact.targetSddcSpec.vcenterSpec");
        expectString(vcenter, "rootVcenterPassword", string(
                required(inventorySddc, "vcenterPasswordToken", "inventory.sddc"),
                "inventory.sddc.vcenterPasswordToken"), "artifact.targetSddcSpec.vcenterSpec");
        expectString(vcenter, "version", targetVersion, "artifact.targetSddcSpec.vcenterSpec");
        require(Boolean.TRUE.equals(required(
                        vcenter, "useExistingDeployment", "artifact.targetSddcSpec.vcenterSpec")),
                "target SddcSpec must reuse the existing vCenter deployment");

        List<Map<String, Object>> networks = objectList(
                required(spec, "networkSpecs", "artifact.targetSddcSpec"),
                "artifact.targetSddcSpec.networkSpecs");
        require(networks.size() == 1, "target SddcSpec must contain the one inventoried management network");
        Map<String, Object> expectedNetwork = object(
                required(inventorySddc, "managementNetwork", "inventory.sddc"),
                "inventory.sddc.managementNetwork");
        Map<String, Object> network = networks.get(0);
        for (String key : List.of("networkType", "vlanId", "subnet", "gateway", "subnetMask", "mtu")) {
            require(required(network, key, "artifact.targetSddcSpec.networkSpecs[0]")
                            .equals(required(expectedNetwork, key, "inventory.sddc.managementNetwork")),
                    "target SddcSpec must preserve management-network field " + key);
        }
    }

    private static Map<String, Component> readInventoryComponents(Map<String, Object> inventory) {
        Map<String, Component> result = new LinkedHashMap<>();
        for (Map<String, Object> item : objectList(
                required(inventory, "components", "inventory"), "inventory.components")) {
            Component component = new Component(
                    string(required(item, "id", "inventory component"), "component.id"),
                    string(required(item, "name", "inventory component"), "component.name"),
                    string(required(item, "product", "inventory component"), "component.product"),
                    string(required(item, "version", "inventory component"), "component.version"));
            require(result.put(component.id, component) == null,
                    "duplicate inventory component id: " + component.id);
        }
        return result;
    }

    private static Map<String, State> readTargetStates(
            Map<String, Object> snapshot, Set<String> componentIds) {
        Map<String, State> result = new HashMap<>();
        for (Map<String, Object> item : objectList(
                required(snapshot, "targetStates", "compatibility snapshot"),
                "compatibility snapshot.targetStates")) {
            String componentId = string(
                    required(item, "componentId", "target state"), "targetState.componentId");
            require(componentIds.contains(componentId), "target state is outside inventory: " + componentId);
            State prior = result.put(componentId, new State(
                    string(required(item, "product", "target state"), "targetState.product"),
                    string(required(item, "version", "target state"), "targetState.version")));
            require(prior == null, "duplicate target state: " + componentId);
        }
        require(result.keySet().equals(componentIds),
                "compatibility snapshot target states do not cover inventory");
        return result;
    }

    private static Map<String, Gate> readGates(
            Map<String, Object> snapshot, Set<String> componentIds) {
        Map<String, Gate> result = new HashMap<>();
        for (Map<String, Object> item : objectList(
                required(snapshot, "gates", "compatibility snapshot"),
                "compatibility snapshot.gates")) {
            String id = string(required(item, "id", "gate"), "gate.id");
            String componentId = string(
                    required(item, "componentId", "gate"), "gate.componentId");
            require(componentIds.contains(componentId), "gate references component outside inventory: " + id);
            Gate prior = result.put(id, new Gate(
                    componentId,
                    string(required(item, "product", "gate"), "gate.product"),
                    string(required(item, "version", "gate"), "gate.version")));
            require(prior == null, "duplicate gate id: " + id);
        }
        return result;
    }

    private static Map<String, Object> findTransition(
            Map<String, Object> step,
            List<Map<String, Object>> transitions,
            String context) {
        String[] keys = {
            "componentId", "fromProduct", "fromVersion", "toProduct", "toVersion", "action"
        };
        Map<String, Object> match = null;
        for (Map<String, Object> candidate : transitions) {
            boolean same = true;
            for (String key : keys) {
                if (!required(step, key, context).equals(required(candidate, key, "transition"))) {
                    same = false;
                    break;
                }
            }
            if (same) {
                require(match == null, "compatibility snapshot has a duplicate transition");
                match = candidate;
            }
        }
        require(match != null, context + " is not a supported transition");
        return match;
    }

    private static String sha256(byte[] bytes) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder result = new StringBuilder();
        for (byte value : digest) {
            result.append(String.format("%02x", value));
        }
        return result.toString();
    }

    private static void expectString(
            Map<String, Object> object, String key, String expected, String context) {
        String actual = string(required(object, key, context), context + "." + key);
        require(expected.equals(actual),
                context + "." + key + " must be " + expected + " but was " + actual);
    }

    private static Object required(Map<String, Object> object, String key, String context) {
        require(object.containsKey(key), context + " is missing " + key);
        return object.get(key);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String context) {
        require(value instanceof Map<?, ?>, context + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object value, String context) {
        require(value instanceof List<?>, context + " must be an array");
        return (List<Object>) value;
    }

    private static List<Map<String, Object>> objectList(Object value, String context) {
        List<Map<String, Object>> result = new ArrayList<>();
        List<Object> values = list(value, context);
        for (int index = 0; index < values.size(); index++) {
            result.add(object(values.get(index), context + "[" + index + "]"));
        }
        return result;
    }

    private static List<String> strings(Object value, String context) {
        List<String> result = new ArrayList<>();
        List<Object> values = list(value, context);
        for (int index = 0; index < values.size(); index++) {
            result.add(string(values.get(index), context + "[" + index + "]"));
        }
        return result;
    }

    private static String string(Object value, String context) {
        require(value instanceof String, context + " must be a string");
        return (String) value;
    }

    private static long integer(Object value, String context) {
        require(value instanceof Long, context + " must be an integer");
        return (Long) value;
    }

    private static <T> Set<T> difference(Set<T> left, Set<T> right) {
        Set<T> result = new HashSet<>(left);
        result.removeAll(right);
        return result;
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private record Component(String id, String name, String product, String version) {
    }

    private record Gate(String componentId, String product, String version) {
    }

    private record State(String product, String version) {
        @Override
        public String toString() {
            return product + " " + version;
        }
    }

    private static final class SchemaValidator {
        private final Map<String, Object> root;

        private SchemaValidator(Map<String, Object> root) {
            this.root = root;
        }

        private void validate(Object instance, Object schemaValue, String path) {
            Map<String, Object> schema = object(schemaValue, path + " schema");
            if (schema.containsKey("$ref")) {
                validate(instance, resolve(string(schema.get("$ref"), path + " schema.$ref")), path);
                return;
            }
            if (schema.containsKey("allOf")) {
                for (Object part : list(schema.get("allOf"), path + " schema.allOf")) {
                    validate(instance, part, path);
                }
            }
            if (schema.containsKey("const")) {
                require(schema.get("const").equals(instance), path + " does not equal schema const");
            }
            if (schema.containsKey("enum")) {
                require(list(schema.get("enum"), path + " schema.enum").contains(instance),
                        path + " is not one of the schema enum values");
            }

            String type = schema.containsKey("type")
                    ? string(schema.get("type"), path + " schema.type") : null;
            if (type != null) {
                boolean correct = switch (type) {
                    case "object" -> instance instanceof Map<?, ?>;
                    case "array" -> instance instanceof List<?>;
                    case "string" -> instance instanceof String;
                    case "integer" -> instance instanceof Long;
                    case "number" -> instance instanceof Number;
                    case "boolean" -> instance instanceof Boolean;
                    case "null" -> instance == null;
                    default -> throw new AssertionError("unsupported schema type " + type + " at " + path);
                };
                require(correct, path + " must have schema type " + type);
            }

            if (instance instanceof Map<?, ?>) {
                validateObject(object(instance, path), schema, path);
            } else if (instance instanceof List<?>) {
                validateArray(list(instance, path), schema, path);
            } else if (instance instanceof String text) {
                validateString(text, schema, path);
            } else if (instance instanceof Number number) {
                validateNumber(number, schema, path);
            }
        }

        private void validateObject(
                Map<String, Object> instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("required")) {
                for (String key : strings(schema.get("required"), path + " schema.required")) {
                    require(instance.containsKey(key), path + " is missing required property " + key);
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), path + " schema.properties") : Map.of();
            for (Map.Entry<String, Object> entry : properties.entrySet()) {
                if (instance.containsKey(entry.getKey())) {
                    validate(instance.get(entry.getKey()), entry.getValue(), path + "." + entry.getKey());
                }
            }
            if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                for (String key : instance.keySet()) {
                    require(properties.containsKey(key), path + " has additional property " + key);
                }
            }
        }

        private void validateArray(
                List<Object> instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("minItems")) {
                require(instance.size() >= number(schema.get("minItems"), path).intValueExact(),
                        path + " has fewer than minItems");
            }
            if (schema.containsKey("maxItems")) {
                require(instance.size() <= number(schema.get("maxItems"), path).intValueExact(),
                        path + " has more than maxItems");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                require(new HashSet<>(instance).size() == instance.size(), path + " items must be unique");
            }
            if (schema.containsKey("items")) {
                for (int index = 0; index < instance.size(); index++) {
                    validate(instance.get(index), schema.get("items"), path + "[" + index + "]");
                }
            }
        }

        private void validateString(
                String instance, Map<String, Object> schema, String path) {
            if (schema.containsKey("minLength")) {
                require(instance.codePointCount(0, instance.length())
                                >= number(schema.get("minLength"), path).intValueExact(),
                        path + " is shorter than minLength");
            }
            if (schema.containsKey("maxLength")) {
                require(instance.codePointCount(0, instance.length())
                                <= number(schema.get("maxLength"), path).intValueExact(),
                        path + " is longer than maxLength");
            }
            if (schema.containsKey("pattern")) {
                Pattern pattern = Pattern.compile(string(schema.get("pattern"), path + " schema.pattern"));
                require(pattern.matcher(instance).find(), path + " does not match schema pattern");
            }
        }

        private void validateNumber(
                Number instance, Map<String, Object> schema, String path) {
            BigDecimal actual = number(instance, path);
            if (schema.containsKey("minimum")) {
                require(actual.compareTo(number(schema.get("minimum"), path)) >= 0,
                        path + " is below minimum");
            }
            if (schema.containsKey("maximum")) {
                require(actual.compareTo(number(schema.get("maximum"), path)) <= 0,
                        path + " is above maximum");
            }
        }

        private Object resolve(String reference) {
            require(reference.startsWith("#/"), "only local schema references are supported: " + reference);
            Object current = root;
            for (String raw : reference.substring(2).split("/")) {
                String token = raw.replace("~1", "/").replace("~0", "~");
                current = required(object(current, "schema reference " + reference), token,
                        "schema reference " + reference);
            }
            return current;
        }

        private BigDecimal number(Object value, String context) {
            require(value instanceof Number, context + " schema limit must be numeric");
            return value instanceof BigDecimal decimal
                    ? decimal : new BigDecimal(value.toString());
        }
    }

    private static final class Json {
        private final String text;
        private int cursor;

        private Json(String text) {
            this.text = text;
        }

        private static Object parse(String text) {
            require(text != null, "JSON text must not be null");
            Json parser = new Json(text);
            Object value = parser.value();
            parser.space();
            require(parser.cursor == text.length(), "unexpected trailing JSON at character " + parser.cursor);
            return value;
        }

        private Object value() {
            space();
            require(cursor < text.length(), "unexpected end of JSON");
            return switch (text.charAt(cursor)) {
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
            cursor++;
            Map<String, Object> result = new LinkedHashMap<>();
            space();
            if (take('}')) {
                return result;
            }
            while (true) {
                space();
                require(cursor < text.length() && text.charAt(cursor) == '"',
                        "object key must be a string at character " + cursor);
                String key = stringValue();
                space();
                require(take(':'), "expected ':' at character " + cursor);
                require(!result.containsKey(key), "duplicate JSON object key: " + key);
                result.put(key, value());
                space();
                if (take('}')) {
                    return result;
                }
                require(take(','), "expected ',' at character " + cursor);
            }
        }

        private List<Object> arrayValue() {
            cursor++;
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
                require(take(','), "expected ',' at character " + cursor);
            }
        }

        private String stringValue() {
            require(take('"'), "expected string at character " + cursor);
            StringBuilder result = new StringBuilder();
            while (cursor < text.length()) {
                char value = text.charAt(cursor++);
                if (value == '"') {
                    return result.toString();
                }
                if (value == '\\') {
                    require(cursor < text.length(), "unterminated JSON escape");
                    char escaped = text.charAt(cursor++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicode());
                        default -> throw new AssertionError("invalid JSON escape: \\" + escaped);
                    }
                } else {
                    require(value >= 0x20, "control character in JSON string");
                    result.append(value);
                }
            }
            throw new AssertionError("unterminated JSON string");
        }

        private char unicode() {
            require(cursor + 4 <= text.length(), "short JSON unicode escape");
            String digits = text.substring(cursor, cursor + 4);
            cursor += 4;
            try {
                return (char) Integer.parseInt(digits, 16);
            } catch (NumberFormatException exception) {
                throw new AssertionError("invalid JSON unicode escape: " + digits);
            }
        }

        private Object numberValue() {
            int start = cursor;
            if (take('-')) {
                require(cursor < text.length(), "short JSON number");
            }
            if (take('0')) {
                // A zero integer part is complete.
            } else {
                require(cursor < text.length() && Character.isDigit(text.charAt(cursor)),
                        "invalid JSON value at character " + cursor);
                while (cursor < text.length() && Character.isDigit(text.charAt(cursor))) {
                    cursor++;
                }
            }
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                int digits = cursor;
                while (cursor < text.length() && Character.isDigit(text.charAt(cursor))) {
                    cursor++;
                }
                require(cursor > digits, "fraction requires a digit");
            }
            if (cursor < text.length() && (text.charAt(cursor) == 'e' || text.charAt(cursor) == 'E')) {
                decimal = true;
                cursor++;
                if (cursor < text.length() && (text.charAt(cursor) == '+' || text.charAt(cursor) == '-')) {
                    cursor++;
                }
                int digits = cursor;
                while (cursor < text.length() && Character.isDigit(text.charAt(cursor))) {
                    cursor++;
                }
                require(cursor > digits, "exponent requires a digit");
            }
            String token = text.substring(start, cursor);
            try {
                return decimal ? new BigDecimal(token) : Long.valueOf(token);
            } catch (NumberFormatException exception) {
                throw new AssertionError("invalid JSON number: " + token);
            }
        }

        private Object literal(String token, Object value) {
            require(text.startsWith(token, cursor), "invalid JSON literal at character " + cursor);
            cursor += token.length();
            return value;
        }

        private boolean take(char expected) {
            if (cursor < text.length() && text.charAt(cursor) == expected) {
                cursor++;
                return true;
            }
            return false;
        }

        private void space() {
            while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) {
                cursor++;
            }
        }
    }
}
