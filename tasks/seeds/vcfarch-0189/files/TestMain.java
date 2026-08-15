import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SPEC = Path.of("installer-spec.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");

    // These hashes make the local installer contract and grading authority immutable.
    private static final String INVENTORY_SHA256 = "942be1dfb4c22ddf9cf94baf6e6315335fa8a051364fc7260e730a462297ec0a";
    private static final String SPEC_SHA256 = "84c7624d9df98a3f0c36de71178868e27c100cb3167d6102bf35d61256fa2ddc";
    private static final String SNAPSHOT_SHA256 = "5221071f44bf8840d7660022fa5b4bf9177688688a606cc8f7e98532a3efa7e4";

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        Map<String, Object> installer = object(Json.parse(Files.readString(SPEC)), "installer spec");
        String rawArtifact = EstateMigrationClient.buildPlan(INVENTORY, SPEC, SNAPSHOT);
        Object artifactValue = Json.parse(rawArtifact == null ? "null" : rawArtifact);

        // Contract rule: validate the installer's own schema before any fixture or
        // compatibility assertions are evaluated.
        validateSchema(artifactValue, installer.get("artifactSchema"), "$");

        require(INVENTORY_SHA256.equals(sha256(INVENTORY)), "estate-inventory.json was modified");
        require(SPEC_SHA256.equals(sha256(SPEC)), "installer-spec.json was modified");
        require(SNAPSHOT_SHA256.equals(sha256(SNAPSHOT)), "compatibility-snapshot.json was modified");

        Map<String, Object> artifact = object(artifactValue, "artifact");
        Map<String, Object> inventory = object(Json.parse(Files.readString(INVENTORY)), "inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(SNAPSHOT)), "snapshot");

        checkIdentityAndPlacement(artifact, inventory, snapshot);
        checkSizing(artifact, inventory, snapshot);
        checkSourceMigrations(artifact, inventory, snapshot);
        checkOrderedSteps(artifact, snapshot);
        checkResearch(artifact);

        System.out.println("ALL TESTS PASSED");
    }

    private static void checkIdentityAndPlacement(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        equal(text(artifact.get("estateId")), text(inventory.get("estateId")), "estateId");
        equal(text(artifact.get("snapshotId")), text(snapshot.get("snapshotId")), "snapshotId");

        Map<String, Object> fleet = object(inventory.get("existingFleet"), "existingFleet");
        Map<String, Object> workload = object(inventory.get("newWorkloadDomain"), "newWorkloadDomain");
        Map<String, Object> architecture = object(artifact.get("architecture"), "architecture");
        Map<String, Object> management = object(architecture.get("managementDomain"), "managementDomain");
        Map<String, Object> target = object(architecture.get("deploymentTarget"), "deploymentTarget");

        equal(text(management.get("name")), text(fleet.get("managementDomain")), "management domain name");
        equal(text(management.get("action")), "NO_CHANGE", "management domain action");
        equal(stringSet(management.get("protectedAssets")), stringSet(fleet.get("protectedAssets")),
                "protected management assets");
        equal(text(target.get("workloadDomain")), text(workload.get("name")), "workload domain placement");
        equal(text(target.get("vCenterFqdn")), text(workload.get("vCenterFqdn")), "target vCenter");
        equal(text(target.get("network")), text(workload.get("network")), "target network");
        equal(text(target.get("datastore")), text(workload.get("datastore")), "target datastore");
    }

    private static void checkSizing(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> architecture = object(artifact.get("architecture"), "architecture");
        Map<String, Map<String, Object>> actual = index(list(architecture.get("components"), "components"),
                "component");
        List<Object> expected = list(snapshot.get("targetSizing"), "targetSizing");
        require(actual.size() == expected.size(), "architecture must contain exactly the pinned target components");
        String targetVersion = text(snapshot.get("targetVersion"));
        String placement = text(object(inventory.get("newWorkloadDomain"), "workload").get("name"));

        for (Object value : expected) {
            Map<String, Object> sizing = object(value, "target sizing row");
            String component = text(sizing.get("component"));
            Map<String, Object> componentPlan = actual.get(component);
            require(componentPlan != null, "missing target component " + component);
            equal(text(componentPlan.get("version")), targetVersion, component + " version");
            equal(text(componentPlan.get("profile")), text(sizing.get("profile")), component + " profile");
            equal(integer(componentPlan.get("nodeCount")), integer(sizing.get("nodeCount")),
                    component + " node count");
            equal(integer(componentPlan.get("vCpuPerNode")), integer(sizing.get("vCpuPerNode")),
                    component + " vCPU per node");
            equal(integer(componentPlan.get("memoryGbPerNode")), integer(sizing.get("memoryGbPerNode")),
                    component + " memory per node");
            equal(integer(componentPlan.get("dataDiskTbPerNode")), integer(sizing.get("dataDiskTbPerNode")),
                    component + " data disk per node");
            equal(integer(componentPlan.get("reservedIps")), integer(sizing.get("reservedIps")),
                    component + " reserved IPs");
            equal(text(componentPlan.get("placement")), placement, component + " placement");
            equal(integerMap(componentPlan.get("capacity")), integerMap(sizing.get("capacity")),
                    component + " capacity");
        }

        Map<String, Map<String, Object>> sources = index(list(inventory.get("sources"), "inventory sources"), "id");
        assertCapacity(actual.get("VCF Operations"), object(sources.get("ops-legacy").get("demand"), "ops demand"));
        assertCapacity(actual.get("VCF Automation"),
                object(sources.get("automation-legacy").get("demand"), "automation demand"));
        assertCapacity(actual.get("VCF Operations for Logs"),
                object(sources.get("logs-legacy").get("demand"), "logs demand"));
    }

    private static void assertCapacity(Map<String, Object> component, Map<String, Object> demand) {
        Map<String, Long> capacity = integerMap(component.get("capacity"));
        for (Map.Entry<String, Object> entry : demand.entrySet()) {
            String capacityKey = entry.getKey().equals("requiredRetentionDays")
                    ? "retentionDays" : entry.getKey();
            Long available = capacity.get(capacityKey);
            require(available != null, text(component.get("component")) + " omits capacity " + entry.getKey());
            require(available >= integer(entry.getValue()),
                    text(component.get("component")) + " is undersized for " + entry.getKey());
        }
    }

    private static void checkSourceMigrations(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> plans = index(list(artifact.get("sources"), "artifact sources"), "sourceId");
        Map<String, Map<String, Object>> inventorySources = index(list(inventory.get("sources"), "inventory sources"),
                "id");
        Map<String, Map<String, Object>> routes = index(list(snapshot.get("routes"), "routes"), "sourceId");
        require(plans.keySet().equals(inventorySources.keySet()), "artifact must cover every and only inventoried source");
        require(routes.keySet().equals(inventorySources.keySet()), "snapshot routes and inventory disagree");

        for (String sourceId : inventorySources.keySet()) {
            Map<String, Object> plan = plans.get(sourceId);
            Map<String, Object> source = inventorySources.get(sourceId);
            Map<String, Object> route = routes.get(sourceId);
            equal(text(plan.get("sourceProduct")), text(source.get("product")), sourceId + " product");
            equal(text(plan.get("sourceVersion")), text(source.get("version")), sourceId + " version");
            equal(text(plan.get("targetComponent")), text(route.get("targetComponent")), sourceId + " target");
            equal(text(plan.get("targetVersion")), text(route.get("targetVersion")), sourceId + " target version");
            equal(text(plan.get("endOfGeneralSupport")), text(route.get("eogs")), sourceId + " EOGS");
            equal(text(plan.get("migrationMode")), text(route.get("migrationMode")), sourceId + " migration mode");
            equal(stringSet(plan.get("carryForward")), stringSet(route.get("requiredCarry")),
                    sourceId + " carried content");
            Set<String> expectedRecreate = route.containsKey("requiredRecreate")
                    ? stringSet(route.get("requiredRecreate")) : Set.of();
            equal(stringSet(plan.get("recreate")), expectedRecreate, sourceId + " recreated configuration");
            equal(stringSet(plan.get("abandoned")), stringSet(route.get("requiredAbandon")),
                    sourceId + " abandoned content");
            equal(stringSet(plan.get("stepIds")), stringSet(route.get("requiredStepIds")), sourceId + " step links");

            Set<String> accounted = new LinkedHashSet<>();
            addDisjoint(accounted, stringSet(plan.get("carryForward")), sourceId + " carryForward");
            addDisjoint(accounted, stringSet(plan.get("recreate")), sourceId + " recreate");
            addDisjoint(accounted, stringSet(plan.get("abandoned")), sourceId + " abandoned");
            equal(accounted, stringSet(source.get("content")), sourceId + " content accounting");
        }
    }

    private static void checkOrderedSteps(Map<String, Object> artifact, Map<String, Object> snapshot) {
        List<Object> steps = list(artifact.get("steps"), "steps");
        List<Object> order = list(snapshot.get("orderedSteps"), "orderedSteps");
        Map<String, Object> requirements = object(snapshot.get("stepRequirements"), "stepRequirements");
        Map<String, Object> actions = object(snapshot.get("stepActions"), "stepActions");
        Map<String, Object> rollbacks = object(snapshot.get("stepRollbacks"), "stepRollbacks");
        require(steps.size() == order.size(), "migration step count does not match pinned sequence");

        Set<String> seen = new HashSet<>();
        for (int i = 0; i < steps.size(); i++) {
            Map<String, Object> step = object(steps.get(i), "step " + (i + 1));
            String id = text(step.get("id"));
            equal(integer(step.get("order")), (long) i + 1, id + " order");
            equal(id, text(order.get(i)), "step id at order " + (i + 1));
            require(seen.add(id), "duplicate step id " + id);
            equal(text(step.get("action")), text(actions.get(id)), id + " action");
            equal(text(step.get("rollback")), text(rollbacks.get(id)), id + " rollback");
            Set<String> gates = new LinkedHashSet<>(stringSet(step.get("entryGates")));
            gates.addAll(stringSet(step.get("exitGates")));
            require(gates.containsAll(stringSet(requirements.get(id))), id + " omits a pinned gate");
        }
    }

    private static void checkResearch(Map<String, Object> artifact) {
        List<Object> research = list(artifact.get("research"), "research");
        require(research.size() >= 2, "research must cite multiple Broadcom pages");
        Set<String> urls = new HashSet<>();
        Set<String> titles = new HashSet<>();
        for (Object value : research) {
            Map<String, Object> source = object(value, "research source");
            String title = text(source.get("title"));
            String url = text(source.get("url"));
            require(titles.add(title), "research contains duplicate page title " + title);
            require(urls.add(url), "research contains duplicate page URL " + url);
            URI parsed;
            try {
                parsed = URI.create(url);
            } catch (IllegalArgumentException ex) {
                throw new AssertionError("research URL is malformed: " + url);
            }
            String host = parsed.getHost();
            require("https".equalsIgnoreCase(parsed.getScheme()), "research URL must use HTTPS: " + url);
            require(host != null && (host.equalsIgnoreCase("broadcom.com")
                            || host.toLowerCase().endsWith(".broadcom.com")),
                    "research URL must be a Broadcom-published page: " + url);
            require(parsed.getUserInfo() == null, "research URL must not contain user information: " + url);
        }
    }

    private static void addDisjoint(Set<String> destination, Set<String> values, String label) {
        for (String value : values) {
            require(destination.add(value), label + " overlaps another disposition at " + value);
        }
    }

    private static Map<String, Map<String, Object>> index(List<Object> rows, String key) {
        Map<String, Map<String, Object>> indexed = new LinkedHashMap<>();
        for (Object rowValue : rows) {
            Map<String, Object> row = object(rowValue, "row");
            String id = text(row.get(key));
            require(indexed.put(id, row) == null, "duplicate " + key + " " + id);
        }
        return indexed;
    }

    private static void validateSchema(Object value, Object schemaValue, String path) {
        Map<String, Object> schema = object(schemaValue, "schema at " + path);
        if (schema.containsKey("enum")) {
            boolean found = false;
            for (Object allowed : list(schema.get("enum"), "enum at " + path)) {
                if (Json.canonical(value).equals(Json.canonical(allowed))) {
                    found = true;
                    break;
                }
            }
            require(found, path + " is not an allowed enum value");
        }

        String type = text(schema.get("type"));
        switch (type) {
            case "object" -> validateObjectSchema(value, schema, path);
            case "array" -> validateArraySchema(value, schema, path);
            case "string" -> validateStringSchema(value, schema, path);
            case "integer" -> validateIntegerSchema(value, schema, path);
            case "boolean" -> require(value instanceof Boolean, path + " must be a boolean");
            default -> throw new AssertionError("unsupported schema type " + type + " at " + path);
        }
    }

    private static void validateObjectSchema(Object value, Map<String, Object> schema, String path) {
        require(value instanceof Map<?, ?>, path + " must be an object");
        Map<String, Object> actual = object(value, path);
        if (schema.containsKey("minProperties")) {
            require(actual.size() >= integer(schema.get("minProperties")), path + " has too few properties");
        }
        Set<String> required = schema.containsKey("required") ? stringSet(schema.get("required")) : Set.of();
        require(actual.keySet().containsAll(required), path + " is missing required properties " + difference(required, actual.keySet()));
        Map<String, Object> properties = schema.containsKey("properties")
                ? object(schema.get("properties"), "properties at " + path) : Map.of();
        Object additional = schema.getOrDefault("additionalProperties", Boolean.TRUE);
        for (Map.Entry<String, Object> entry : actual.entrySet()) {
            if (properties.containsKey(entry.getKey())) {
                validateSchema(entry.getValue(), properties.get(entry.getKey()), path + "." + entry.getKey());
            } else if (Boolean.FALSE.equals(additional)) {
                throw new AssertionError(path + " has unexpected property " + entry.getKey());
            } else if (additional instanceof Map<?, ?>) {
                validateSchema(entry.getValue(), additional, path + "." + entry.getKey());
            }
        }
    }

    private static void validateArraySchema(Object value, Map<String, Object> schema, String path) {
        require(value instanceof List<?>, path + " must be an array");
        List<Object> values = list(value, path);
        if (schema.containsKey("minItems")) {
            require(values.size() >= integer(schema.get("minItems")), path + " has too few items");
        }
        if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
            Set<String> canonical = new HashSet<>();
            for (Object item : values) {
                require(canonical.add(Json.canonical(item)), path + " contains duplicate items");
            }
        }
        if (schema.containsKey("items")) {
            for (int i = 0; i < values.size(); i++) {
                validateSchema(values.get(i), schema.get("items"), path + "[" + i + "]");
            }
        }
    }

    private static void validateStringSchema(Object value, Map<String, Object> schema, String path) {
        require(value instanceof String, path + " must be a string");
        String actual = (String) value;
        if (schema.containsKey("minLength")) {
            require(actual.length() >= integer(schema.get("minLength")), path + " is too short");
        }
        if (schema.containsKey("pattern")) {
            require(Pattern.compile(text(schema.get("pattern"))).matcher(actual).matches(),
                    path + " does not match its schema pattern");
        }
    }

    private static void validateIntegerSchema(Object value, Map<String, Object> schema, String path) {
        require(value instanceof Long, path + " must be an integer");
        long actual = (Long) value;
        if (schema.containsKey("minimum")) {
            require(actual >= integer(schema.get("minimum")), path + " is below its minimum");
        }
    }

    private static Set<String> difference(Set<String> left, Set<String> right) {
        Set<String> result = new LinkedHashSet<>(left);
        result.removeAll(right);
        return result;
    }

    private static String sha256(Path path) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));
        StringBuilder out = new StringBuilder();
        for (byte value : digest) {
            out.append(String.format("%02x", value));
        }
        return out.toString();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        require(value instanceof Map<?, ?>, label + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object value, String label) {
        require(value instanceof List<?>, label + " must be an array");
        return (List<Object>) value;
    }

    private static String text(Object value) {
        require(value instanceof String, "expected string but got " + value);
        return (String) value;
    }

    private static long integer(Object value) {
        require(value instanceof Long, "expected integer but got " + value);
        return (Long) value;
    }

    private static Set<String> stringSet(Object value) {
        Set<String> result = new LinkedHashSet<>();
        for (Object item : list(value, "string array")) {
            result.add(text(item));
        }
        return result;
    }

    private static Map<String, Long> integerMap(Object value) {
        Map<String, Long> result = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : object(value, "integer map").entrySet()) {
            result.put(entry.getKey(), integer(entry.getValue()));
        }
        return result;
    }

    private static void equal(Object actual, Object expected, String label) {
        require(actual.equals(expected), label + " mismatch: expected " + expected + " but got " + actual);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class Json {
        static Object parse(String source) {
            Parser parser = new Parser(source);
            Object value = parser.value();
            parser.space();
            if (!parser.end()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        static String canonical(Object value) {
            if (value == null) return "null";
            if (value instanceof String string) return quote(string);
            if (value instanceof Boolean || value instanceof Number) return value.toString();
            if (value instanceof List<?> values) {
                StringBuilder out = new StringBuilder("[");
                for (int i = 0; i < values.size(); i++) {
                    if (i > 0) out.append(',');
                    out.append(canonical(values.get(i)));
                }
                return out.append(']').toString();
            }
            if (value instanceof Map<?, ?> map) {
                List<String> keys = new ArrayList<>();
                for (Object key : map.keySet()) keys.add((String) key);
                keys.sort(String::compareTo);
                StringBuilder out = new StringBuilder("{");
                for (int i = 0; i < keys.size(); i++) {
                    if (i > 0) out.append(',');
                    String key = keys.get(i);
                    out.append(quote(key)).append(':').append(canonical(map.get(key)));
                }
                return out.append('}').toString();
            }
            throw new IllegalArgumentException("unsupported JSON value " + value);
        }

        private static String quote(String value) {
            StringBuilder out = new StringBuilder("\"");
            for (int i = 0; i < value.length(); i++) {
                char ch = value.charAt(i);
                switch (ch) {
                    case '\"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\b' -> out.append("\\b");
                    case '\f' -> out.append("\\f");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (ch < 0x20) out.append(String.format("\\u%04x", (int) ch));
                        else out.append(ch);
                    }
                }
            }
            return out.append('\"').toString();
        }

        private static final class Parser {
            private final String source;
            private int at;

            Parser(String source) {
                this.source = source;
            }

            boolean end() { return at == source.length(); }

            void space() {
                while (!end() && Character.isWhitespace(source.charAt(at))) at++;
            }

            Object value() {
                space();
                if (end()) throw error("expected value");
                return switch (source.charAt(at)) {
                    case '{' -> object();
                    case '[' -> array();
                    case '\"' -> string();
                    case 't' -> literal("true", Boolean.TRUE);
                    case 'f' -> literal("false", Boolean.FALSE);
                    case 'n' -> literal("null", null);
                    default -> number();
                };
            }

            Map<String, Object> object() {
                at++;
                Map<String, Object> result = new LinkedHashMap<>();
                space();
                if (take('}')) return result;
                while (true) {
                    space();
                    if (end() || source.charAt(at) != '\"') throw error("expected object key");
                    String key = string();
                    require(!result.containsKey(key), "duplicate JSON key " + key);
                    space();
                    expect(':');
                    result.put(key, value());
                    space();
                    if (take('}')) return result;
                    expect(',');
                }
            }

            List<Object> array() {
                at++;
                List<Object> result = new ArrayList<>();
                space();
                if (take(']')) return result;
                while (true) {
                    result.add(value());
                    space();
                    if (take(']')) return result;
                    expect(',');
                }
            }

            String string() {
                expect('\"');
                StringBuilder out = new StringBuilder();
                while (!end()) {
                    char ch = source.charAt(at++);
                    if (ch == '\"') return out.toString();
                    if (ch == '\\') {
                        if (end()) throw error("unfinished escape");
                        char escaped = source.charAt(at++);
                        switch (escaped) {
                            case '\"', '\\', '/' -> out.append(escaped);
                            case 'b' -> out.append('\b');
                            case 'f' -> out.append('\f');
                            case 'n' -> out.append('\n');
                            case 'r' -> out.append('\r');
                            case 't' -> out.append('\t');
                            case 'u' -> {
                                if (at + 4 > source.length()) throw error("short unicode escape");
                                try {
                                    out.append((char) Integer.parseInt(source.substring(at, at + 4), 16));
                                } catch (NumberFormatException ex) {
                                    throw error("invalid unicode escape");
                                }
                                at += 4;
                            }
                            default -> throw error("invalid escape");
                        }
                    } else {
                        if (ch < 0x20) throw error("control character in string");
                        out.append(ch);
                    }
                }
                throw error("unterminated string");
            }

            Object number() {
                int start = at;
                if (take('-')) { /* sign */ }
                if (end() || !Character.isDigit(source.charAt(at))) throw error("invalid number");
                if (source.charAt(at) == '0') at++;
                else while (!end() && Character.isDigit(source.charAt(at))) at++;
                boolean decimal = false;
                if (!end() && source.charAt(at) == '.') {
                    decimal = true;
                    at++;
                    if (end() || !Character.isDigit(source.charAt(at))) throw error("invalid fraction");
                    while (!end() && Character.isDigit(source.charAt(at))) at++;
                }
                if (!end() && (source.charAt(at) == 'e' || source.charAt(at) == 'E')) {
                    decimal = true;
                    at++;
                    if (!end() && (source.charAt(at) == '+' || source.charAt(at) == '-')) at++;
                    if (end() || !Character.isDigit(source.charAt(at))) throw error("invalid exponent");
                    while (!end() && Character.isDigit(source.charAt(at))) at++;
                }
                String token = source.substring(start, at);
                try {
                    return decimal ? new BigDecimal(token) : Long.valueOf(token);
                } catch (NumberFormatException ex) {
                    throw error("invalid number");
                }
            }

            Object literal(String token, Object value) {
                if (!source.startsWith(token, at)) throw error("invalid literal");
                at += token.length();
                return value;
            }

            boolean take(char wanted) {
                if (!end() && source.charAt(at) == wanted) {
                    at++;
                    return true;
                }
                return false;
            }

            void expect(char wanted) {
                if (!take(wanted)) throw error("expected '" + wanted + "'");
            }

            IllegalArgumentException error(String message) {
                return new IllegalArgumentException(message + " at JSON offset " + at);
            }
        }
    }
}
