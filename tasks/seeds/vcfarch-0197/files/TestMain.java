import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Protected deterministic acceptance harness. It performs no network access. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new AssertionError("TestMain needs estate, snapshot, and output paths");

        Map<String, Object> estate = object(Parser.parse(Files.readString(
                Path.of(args[0]), StandardCharsets.UTF_8)), "estate");
        Map<String, Object> snapshot = object(Parser.parse(Files.readString(
                Path.of(args[1]), StandardCharsets.UTF_8)), "snapshot");

        Path output = Path.of(args[2]);
        Files.deleteIfExists(output);
        MigrationPlanner.main(new String[] {args[0], args[1], args[2]});
        require(Files.isRegularFile(output), "client did not create the output artifact");
        Map<String, Object> plan = object(Parser.parse(Files.readString(
                output, StandardCharsets.UTF_8)), "plan");

        validate(plan, estate, snapshot);
        System.out.println("PASS: VCF migration architecture matches the pinned snapshot");
    }

    private static void validate(Map<String, Object> plan,
                                 Map<String, Object> estate,
                                 Map<String, Object> snapshot) {
        exactKeys(plan, "plan", "schemaVersion", "site", "deployments",
                "supportBoundaries", "steps", "research");
        equal(1, integer(field(plan, "schemaVersion"), "schemaVersion"), "schemaVersion");

        Map<String, Object> estateSite = object(field(estate, "site"), "estate.site");
        Map<String, Object> topology = object(field(snapshot, "topology"), "snapshot.topology");
        Map<String, Object> planSite = object(field(plan, "site"), "plan.site");
        exactKeys(planSite, "plan.site", "siteId", "architecture", "managementDomain");
        equal(string(field(estateSite, "siteId"), "estate.siteId"),
                string(field(planSite, "siteId"), "plan.siteId"), "siteId");
        equal(string(field(topology, "architecture"), "topology.architecture"),
                string(field(planSite, "architecture"), "plan.architecture"), "architecture");

        List<Object> estateHosts = array(field(estateSite, "hosts"), "estate.hosts");
        LinkedHashMap<String, Capacity> capacities = new LinkedHashMap<>();
        for (Object value : estateHosts) {
            Map<String, Object> host = object(value, "estate host");
            String id = string(field(host, "id"), "host.id");
            require(capacities.put(id, new Capacity(
                    integer(field(host, "vCpuCapacity"), "host.vCpuCapacity"),
                    integer(field(host, "memoryGiBCapacity"), "host.memoryGiBCapacity"))) == null,
                    "duplicate estate host " + id);
        }

        Map<String, Object> domain = object(field(planSite, "managementDomain"),
                "plan.managementDomain");
        exactKeys(domain, "plan.managementDomain", "hostCount", "failuresToTolerate",
                "storagePolicy", "hosts");
        int hostCount = integer(field(domain, "hostCount"), "managementDomain.hostCount");
        int ftt = integer(field(domain, "failuresToTolerate"),
                "managementDomain.failuresToTolerate");
        int minimumHostCount = integer(field(topology, "minimumHostCount"),
                "topology.minimumHostCount");
        int pinnedFtt = integer(field(topology, "failuresToTolerate"),
                "topology.failuresToTolerate");
        equal(minimumHostCount, hostCount, "management-domain host count");
        equal(pinnedFtt, ftt, "management-domain failures-to-tolerate");
        require(ftt >= 0, "failures-to-tolerate cannot be negative");
        require(hostCount >= 2 * ftt + 1,
                "host count " + hostCount + " contradicts failures-to-tolerate " + ftt
                        + "; RAID-1 requires at least " + (2 * ftt + 1) + " fault domains");
        require(minimumHostCount >= 2 * pinnedFtt + 1,
                "pinned host count contradicts pinned failures-to-tolerate");
        equal(string(field(topology, "storagePolicy"), "topology.storagePolicy"),
                string(field(domain, "storagePolicy"), "managementDomain.storagePolicy"),
                "storage policy");
        List<String> plannedHosts = strings(field(domain, "hosts"), "managementDomain.hosts");
        equal(hostCount, plannedHosts.size(), "hostCount versus hosts array");
        equal(plannedHosts.size(), new HashSet<>(plannedHosts).size(),
                "management-domain host uniqueness");
        equal(capacities.keySet(), new LinkedHashSet<>(plannedHosts),
                "management-domain host coverage");

        Map<String, Map<String, Object>> expectedDeployments = index(
                array(field(snapshot, "deployments"), "snapshot.deployments"), "id", "deployment");
        List<Object> planDeploymentValues = array(field(plan, "deployments"), "plan.deployments");
        Map<String, Map<String, Object>> planDeployments = index(
                planDeploymentValues, "id", "plan deployment");
        equal(expectedDeployments.keySet(), planDeployments.keySet(), "deployment ids");

        HashSet<String> nodeNames = new HashSet<>();
        HashMap<String, Usage> usage = new HashMap<>();
        for (String host : capacities.keySet()) usage.put(host, new Usage());
        for (Map.Entry<String, Map<String, Object>> entry : expectedDeployments.entrySet()) {
            String id = entry.getKey();
            Map<String, Object> expected = entry.getValue();
            Map<String, Object> actual = planDeployments.get(id);
            exactKeys(actual, "plan deployment " + id, "id", "component", "version", "size",
                    "nodes");
            equal(string(field(expected, "component"), id + ".component"),
                    string(field(actual, "component"), id + ".component"), id + " component");
            equal(string(field(expected, "version"), id + ".version"),
                    string(field(actual, "version"), id + ".version"), id + " version");
            Map<String, Object> expectedSize = object(field(expected, "size"), id + ".size");
            Map<String, Object> actualSize = object(field(actual, "size"), id + ".size");
            equal(expectedSize, actualSize, id + " size");

            int nodesExpected = integer(field(expectedSize, "nodeCount"), id + ".nodeCount");
            int cpu = integer(field(expectedSize, "vCpuPerNode"), id + ".vCpuPerNode");
            int memory = integer(field(expectedSize, "memoryGiBPerNode"), id + ".memoryGiBPerNode");
            List<Object> nodes = array(field(actual, "nodes"), id + ".nodes");
            equal(nodesExpected, nodes.size(), id + " node count");
            HashSet<String> replicaHosts = new HashSet<>();
            for (Object nodeValue : nodes) {
                Map<String, Object> node = object(nodeValue, id + " node");
                exactKeys(node, id + " node", "name", "host");
                String name = string(field(node, "name"), id + " node.name");
                String host = string(field(node, "host"), id + " node.host");
                require(!name.isBlank(), id + " has a blank node name");
                require(nodeNames.add(name), "duplicate node name " + name);
                require(capacities.containsKey(host), id + " node uses unknown host " + host);
                require(replicaHosts.add(host), id + " replicas are not on distinct hosts");
                usage.get(host).cpu += cpu;
                usage.get(host).memory += memory;
            }
        }
        for (Map.Entry<String, Capacity> entry : capacities.entrySet()) {
            Usage assigned = usage.get(entry.getKey());
            Capacity available = entry.getValue();
            require(assigned.cpu <= available.cpu,
                    entry.getKey() + " vCPU capacity exceeded: " + assigned.cpu + "/" + available.cpu);
            require(assigned.memory <= available.memory,
                    entry.getKey() + " memory capacity exceeded: " + assigned.memory + "/" + available.memory);
        }

        Map<String, Map<String, Object>> products = index(
                array(field(estate, "products"), "estate.products"), "key", "source product");
        List<Object> expectedBoundaryValues = array(field(snapshot, "supportBoundaries"),
                "snapshot.supportBoundaries");
        List<Object> actualBoundaryValues = array(field(plan, "supportBoundaries"),
                "plan.supportBoundaries");
        equal(expectedBoundaryValues.size(), actualBoundaryValues.size(), "support boundary count");
        LinkedHashMap<String, Map<String, Object>> actualBoundaries = new LinkedHashMap<>();
        for (Object value : actualBoundaryValues) {
            Map<String, Object> actual = object(value, "actual boundary");
            exactKeys(actual, "actual boundary", "sourceProduct", "sourceVersion",
                    "endOfGeneralSupport", "assessmentAtSnapshot");
            String source = string(field(actual, "sourceProduct"), "boundary.sourceProduct")
                    + "\u0000" + string(field(actual, "sourceVersion"), "boundary.sourceVersion");
            require(actualBoundaries.put(source, actual) == null,
                    "duplicate support boundary for " + source.replace('\u0000', ' '));
        }
        LinkedHashSet<String> boundaryCoverage = new LinkedHashSet<>();
        for (int index = 0; index < expectedBoundaryValues.size(); index++) {
            Map<String, Object> expected = object(expectedBoundaryValues.get(index), "expected boundary");
            String key = string(field(expected, "sourceKey"), "boundary.sourceKey");
            Map<String, Object> product = products.get(key);
            require(product != null, "support boundary names unknown source " + key);
            String sourceProduct = string(field(product, "product"), "product.product");
            String sourceVersion = string(field(product, "version"), "product.version");
            Map<String, Object> actual = actualBoundaries.get(sourceProduct + "\u0000" + sourceVersion);
            require(actual != null, "missing support boundary for " + sourceProduct + " "
                    + sourceVersion);
            equal(sourceProduct,
                    string(field(actual, "sourceProduct"), "boundary.sourceProduct"),
                    key + " support product");
            equal(sourceVersion,
                    string(field(actual, "sourceVersion"), "boundary.sourceVersion"),
                    key + " support version");
            equal(string(field(expected, "endOfGeneralSupport"), "boundary EOGS"),
                    string(field(actual, "endOfGeneralSupport"), "boundary EOGS"),
                    key + " EOGS");
            equal(string(field(expected, "assessmentAtSnapshot"), "boundary assessment"),
                    string(field(actual, "assessmentAtSnapshot"), "boundary assessment"),
                    key + " support assessment");
            require(boundaryCoverage.add(key), "duplicate support boundary for " + key);
        }
        equal(products.keySet(), boundaryCoverage, "support-boundary source coverage");

        List<Object> rules = array(field(snapshot, "migrationRules"), "snapshot.migrationRules");
        List<Object> steps = array(field(plan, "steps"), "plan.steps");
        equal(rules.size(), steps.size(), "migration step count");
        LinkedHashSet<String> sourceCoverage = new LinkedHashSet<>();
        LinkedHashSet<String> completedTargets = new LinkedHashSet<>();
        completedTargets.add("fleet-management");
        for (int index = 0; index < rules.size(); index++) {
            Map<String, Object> rule = object(rules.get(index), "migration rule");
            Map<String, Object> step = object(steps.get(index), "migration step");
            exactKeys(step, "migration step", "order", "source", "target", "mode",
                    "deploymentRef", "dependsOn", "carries", "abandons", "gates");
            int expectedOrder = integer(field(rule, "order"), "rule.order");
            equal(index + 1, expectedOrder, "pinned rule order");
            equal(expectedOrder, integer(field(step, "order"), "step.order"), "step order");

            String sourceKey = string(field(rule, "sourceKey"), "rule.sourceKey");
            require(sourceCoverage.add(sourceKey), "duplicate source step " + sourceKey);
            Map<String, Object> sourceProduct = products.get(sourceKey);
            require(sourceProduct != null, "unknown source key " + sourceKey);
            Map<String, Object> source = object(field(step, "source"), "step.source");
            exactKeys(source, "step.source", "product", "version");
            equal(string(field(sourceProduct, "product"), "source product"),
                    string(field(source, "product"), "step source product"), sourceKey + " product");
            equal(string(field(sourceProduct, "version"), "source version"),
                    string(field(source, "version"), "step source version"), sourceKey + " version");

            String deploymentId = string(field(rule, "targetDeployment"), "rule.targetDeployment");
            Map<String, Object> targetDeployment = expectedDeployments.get(deploymentId);
            require(targetDeployment != null, "unknown target deployment " + deploymentId);
            equal(deploymentId, string(field(step, "deploymentRef"), "step.deploymentRef"),
                    sourceKey + " deploymentRef");
            Map<String, Object> target = object(field(step, "target"), "step.target");
            exactKeys(target, "step.target", "component", "version");
            equal(string(field(targetDeployment, "component"), "target component"),
                    string(field(target, "component"), "step.target.component"),
                    sourceKey + " target component");
            equal(string(field(targetDeployment, "version"), "target version"),
                    string(field(target, "version"), "step.target.version"),
                    sourceKey + " target version");
            equal(string(field(rule, "mode"), "rule.mode"),
                    string(field(step, "mode"), "step.mode"), sourceKey + " migration mode");

            List<String> dependencies = strings(field(step, "dependsOn"), "step.dependsOn");
            equal(strings(field(rule, "dependsOn"), "rule.dependsOn"), dependencies,
                    sourceKey + " dependencies");
            for (String dependency : dependencies) {
                require(completedTargets.contains(dependency),
                        sourceKey + " depends on target not yet available: " + dependency);
            }
            equal(strings(field(rule, "carries"), "rule.carries"),
                    strings(field(step, "carries"), "step.carries"), sourceKey + " carries");
            equal(strings(field(rule, "abandons"), "rule.abandons"),
                    strings(field(step, "abandons"), "step.abandons"), sourceKey + " abandons");
            equal(strings(field(rule, "gates"), "rule.gates"),
                    strings(field(step, "gates"), "step.gates"), sourceKey + " gates");

            LinkedHashSet<String> carries = new LinkedHashSet<>(
                    strings(field(step, "carries"), "step.carries"));
            LinkedHashSet<String> abandons = new LinkedHashSet<>(
                    strings(field(step, "abandons"), "step.abandons"));
            HashSet<String> overlap = new HashSet<>(carries);
            overlap.retainAll(abandons);
            require(overlap.isEmpty(), sourceKey + " carries and abandons overlap: " + overlap);
            LinkedHashSet<String> dispositions = new LinkedHashSet<>(carries);
            dispositions.addAll(abandons);
            equal(new LinkedHashSet<>(strings(field(sourceProduct, "content"), "product.content")),
                    dispositions, sourceKey + " content disposition coverage");
            completedTargets.add(deploymentId);
        }
        equal(products.keySet(), sourceCoverage, "migration source coverage");

        validateResearch(array(field(plan, "research"), "plan.research"),
                string(field(snapshot, "snapshotDate"), "snapshot.snapshotDate"));
    }

    private static void validateResearch(List<Object> values, String snapshotDateText) {
        require(!values.isEmpty(), "research must record at least one consulted source");
        LocalDate snapshotDate;
        try {
            snapshotDate = LocalDate.parse(snapshotDateText);
        } catch (DateTimeParseException failure) {
            throw new AssertionError("snapshotDate is not YYYY-MM-DD", failure);
        }

        HashSet<String> urls = new HashSet<>();
        for (int index = 0; index < values.size(); index++) {
            String description = "research[" + index + "]";
            Map<String, Object> item = object(values.get(index), description);
            exactKeys(item, description, "publisher", "title", "url", "accessedOn", "usedFor");
            equal("Broadcom", string(field(item, "publisher"), description + ".publisher"),
                    description + " publisher");
            require(!string(field(item, "title"), description + ".title").isBlank(),
                    description + " has a blank title");

            String url = string(field(item, "url"), description + ".url");
            require(urls.add(url), "duplicate research URL " + url);
            try {
                URI uri = new URI(url);
                require("https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null,
                        description + " URL must be an absolute HTTPS URL");
                require(!uri.getHost().endsWith(".invalid"),
                        description + " URL must not use a reserved invalid host");
            } catch (URISyntaxException failure) {
                throw new AssertionError(description + " has an invalid URL", failure);
            }

            String accessedOnText = string(field(item, "accessedOn"), description + ".accessedOn");
            try {
                LocalDate accessedOn = LocalDate.parse(accessedOnText);
                require(!accessedOn.isBefore(snapshotDate),
                        description + " predates the pinned snapshot");
            } catch (DateTimeParseException failure) {
                throw new AssertionError(description + ".accessedOn is not YYYY-MM-DD", failure);
            }

            List<String> usedFor = strings(field(item, "usedFor"), description + ".usedFor");
            require(!usedFor.isEmpty(), description + ".usedFor must not be empty");
            for (String use : usedFor) {
                require(!use.isBlank(), description + ".usedFor contains a blank value");
            }
        }
    }

    private static Map<String, Map<String, Object>> index(List<Object> values,
                                                           String keyField,
                                                           String description) {
        LinkedHashMap<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, description);
            String key = string(field(item, keyField), description + "." + keyField);
            require(result.put(key, item) == null, "duplicate " + description + " key " + key);
        }
        return result;
    }

    private static Object field(Map<String, Object> object, String name) {
        require(object.containsKey(name), "missing field " + name);
        return object.get(name);
    }

    private static void exactKeys(Map<String, Object> object, String description,
                                  String... expectedKeys) {
        equal(Set.of(expectedKeys), object.keySet(), description + " fields");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String description) {
        if (!(value instanceof Map<?, ?>)) fail(description + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String description) {
        if (!(value instanceof List<?>)) fail(description + " must be an array");
        return (List<Object>) value;
    }

    private static List<String> strings(Object value, String description) {
        List<Object> values = array(value, description);
        ArrayList<String> result = new ArrayList<>();
        for (int index = 0; index < values.size(); index++) {
            result.add(string(values.get(index), description + "[" + index + "]"));
        }
        return result;
    }

    private static String string(Object value, String description) {
        if (!(value instanceof String text)) fail(description + " must be a string");
        return (String) value;
    }

    private static int integer(Object value, String description) {
        if (!(value instanceof Number number) || number.doubleValue() != number.intValue()) {
            fail(description + " must be an integer");
        }
        return ((Number) value).intValue();
    }

    private static void equal(Object expected, Object actual, String description) {
        if (!expected.equals(actual)) {
            fail(description + " mismatch; expected " + expected + " but got " + actual);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) fail(message);
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    private record Capacity(int cpu, int memory) {}

    private static final class Usage {
        int cpu;
        int memory;
    }

    /** Independent JSON parser used only by the protected harness. */
    private static final class Parser {
        private final String input;
        private int position;

        private Parser(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            Parser parser = new Parser(input);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != input.length()) throw parser.error("trailing input");
            return value;
        }

        private Object value() {
            whitespace();
            if (position >= input.length()) throw error("expected value");
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
            position++;
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) return result;
            while (true) {
                whitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    throw error("expected object key");
                }
                String key = stringValue();
                whitespace();
                expect(':');
                require(!result.containsKey(key), "duplicate JSON key " + key);
                result.put(key, value());
                whitespace();
                if (take('}')) return result;
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            position++;
            ArrayList<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) return result;
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) return result;
                expect(',');
            }
        }

        private String stringValue() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char current = input.charAt(position++);
                if (current == '"') return result.toString();
                if (current != '\\') {
                    if (current < 0x20) throw error("control character in string");
                    result.append(current);
                    continue;
                }
                if (position >= input.length()) throw error("incomplete escape");
                char escaped = input.charAt(position++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (position + 4 > input.length()) throw error("incomplete unicode escape");
                        try {
                            result.append((char) Integer.parseInt(
                                    input.substring(position, position + 4), 16));
                        } catch (NumberFormatException failure) {
                            throw error("invalid unicode escape");
                        }
                        position += 4;
                    }
                    default -> throw error("invalid escape");
                }
            }
            throw error("unterminated string");
        }

        private Object numberValue() {
            int start = position;
            take('-');
            while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            }
            if (position < input.length() && (input.charAt(position) == 'e'
                    || input.charAt(position) == 'E')) {
                decimal = true;
                position++;
                if (position < input.length() && (input.charAt(position) == '+'
                        || input.charAt(position) == '-')) position++;
                while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            }
            if (start == position) throw error("expected number");
            String text = input.substring(start, position);
            try {
                return decimal ? Double.valueOf(text) : Long.valueOf(text);
            } catch (NumberFormatException failure) {
                throw error("invalid number");
            }
        }

        private Object literal(String text, Object value) {
            if (!input.startsWith(text, position)) throw error("invalid literal");
            position += text.length();
            return value;
        }

        private void whitespace() {
            while (position < input.length() && Character.isWhitespace(input.charAt(position))) position++;
        }

        private boolean take(char expected) {
            if (position < input.length() && input.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) throw error("expected '" + expected + "'");
        }

        private IllegalArgumentException error(String message) {
            return new IllegalArgumentException(message + " at character " + position);
        }
    }
}
