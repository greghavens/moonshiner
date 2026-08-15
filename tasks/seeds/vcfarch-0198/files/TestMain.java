import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Protected, deterministic verifier. It performs no network access. */
public final class TestMain {
    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Path inventoryPath = Path.of("estate-inventory.json");
        Path snapshotPath = Path.of("compatibility-snapshot.json");
        Map<String, Object> inventory = object(parseFile(inventoryPath), "inventory root");
        Map<String, Object> snapshot = object(parseFile(snapshotPath), "snapshot root");

        Map<String, Object> targetFixture = object(inventory.get("targetWorkloadDomain"),
                "targetWorkloadDomain");
        int fixtureHosts = integer(targetFixture.get("hostCount"), "fixture hostCount");
        int fixtureFtt = integer(targetFixture.get("failuresToTolerate"),
                "fixture failuresToTolerate");
        Map<String, Object> placementPolicy = object(snapshot.get("placementPolicy"), "placementPolicy");
        Map<String, Object> minimums = object(placementPolicy.get("minimumHostsByFailuresToTolerate"),
                "minimumHostsByFailuresToTolerate");
        int snapshotMinimum = integer(minimums.get(Integer.toString(fixtureFtt)),
                "minimum hosts for FTT=" + fixtureFtt);
        int calculatedMinimum = 2 * fixtureFtt + 1;
        check(snapshotMinimum == calculatedMinimum,
                "pinned minimum host rule is inconsistent with failures-to-tolerate");
        check(fixtureHosts >= calculatedMinimum,
                "host count " + fixtureHosts + " contradicts failures-to-tolerate " + fixtureFtt
                        + "; at least " + calculatedMinimum + " hosts are required");

        Path temporaryDirectory = Files.createTempDirectory("vcf-migration-plan-test-");
        Path artifactPath = temporaryDirectory.resolve("migration-plan.json");
        Path extraArgumentArtifact = temporaryDirectory.resolve("extra-argument.json");
        try {
            expectArgumentCountRejected(new String[0], "zero arguments");
            expectArgumentCountRejected(new String[] {
                    inventoryPath.toString()
            }, "one argument");
            expectArgumentCountRejected(new String[] {
                    inventoryPath.toString(), snapshotPath.toString()
            }, "two arguments");
            expectArgumentCountRejected(new String[] {
                    inventoryPath.toString(), snapshotPath.toString(),
                    extraArgumentArtifact.toString(), "unexpected-fourth-argument"
            }, "four arguments");
            check(!Files.exists(extraArgumentArtifact),
                    "client wrote an artifact after receiving four arguments");

            MigrationPlanClient.main(new String[] {
                    inventoryPath.toString(), snapshotPath.toString(), artifactPath.toString()
            });
            check(Files.isRegularFile(artifactPath), "client did not create the output artifact");
            Map<String, Object> artifact = object(parseFile(artifactPath), "artifact root");
            verifyArtifact(artifact, inventory, snapshot, fixtureHosts, fixtureFtt, calculatedMinimum);
        } finally {
            Files.deleteIfExists(artifactPath);
            Files.deleteIfExists(extraArgumentArtifact);
            Files.deleteIfExists(temporaryDirectory);
        }
        System.out.println("PASS: migration architecture matches the fixture and pinned snapshot");
    }

    private static void verifyArtifact(Map<String, Object> artifact, Map<String, Object> inventory,
            Map<String, Object> snapshot, int fixtureHosts, int fixtureFtt, int calculatedMinimum) {
        equal("1.0", text(artifact.get("schemaVersion"), "schemaVersion"), "schemaVersion");
        nonBlank(text(artifact.get("planId"), "planId"), "planId");
        equal(text(inventory.get("inventoryId"), "inventoryId"),
                text(artifact.get("inventoryId"), "artifact inventoryId"), "inventoryId");
        equal(text(snapshot.get("snapshotId"), "snapshotId"),
                text(artifact.get("compatibilitySnapshotId"), "compatibilitySnapshotId"),
                "compatibilitySnapshotId");

        verifyArchitecture(object(artifact.get("architecture"), "architecture"), inventory, snapshot,
                fixtureHosts, fixtureFtt, calculatedMinimum);
        verifyMigrations(array(artifact.get("migrations"), "migrations"), inventory, snapshot);
        verifySteps(array(artifact.get("orderedSteps"), "orderedSteps"), snapshot);
        verifyResearch(array(artifact.get("research"), "research"));
    }

    private static void verifyArchitecture(Map<String, Object> architecture,
            Map<String, Object> inventory, Map<String, Object> snapshot, int fixtureHosts,
            int fixtureFtt, int calculatedMinimum) {
        Map<String, Object> existingFleet = object(inventory.get("existingFleet"), "existingFleet");
        Map<String, Object> managementFixture = object(existingFleet.get("managementDomain"),
                "managementDomain fixture");
        Map<String, Object> management = object(architecture.get("managementDomain"),
                "architecture.managementDomain");
        Map<String, Object> managementPolicy = object(snapshot.get("managementDomainPolicy"),
                "managementDomainPolicy");
        equal(text(managementFixture.get("domainId"), "management fixture domainId"),
                text(management.get("domainId"), "management domainId"), "management domainId");
        equal(text(managementPolicy.get("change"), "management change policy"),
                text(management.get("change"), "management change"), "management domain change");
        equal(text(managementPolicy.get("placement"), "management placement policy"),
                text(management.get("placement"), "management placement"),
                "management domain placement");

        Map<String, Object> targetFixture = object(inventory.get("targetWorkloadDomain"),
                "targetWorkloadDomain");
        Map<String, Object> target = object(architecture.get("targetDomain"),
                "architecture.targetDomain");
        Map<String, Object> placementPolicy = object(snapshot.get("placementPolicy"), "placementPolicy");
        equal(text(targetFixture.get("domainId"), "fixture target domainId"),
                text(target.get("domainId"), "target domainId"), "target domainId");
        equal(text(placementPolicy.get("requiredDomainId"), "requiredDomainId"),
                text(target.get("domainId"), "target domainId"), "required target domainId");
        equal(text(targetFixture.get("clusterId"), "fixture target clusterId"),
                text(target.get("clusterId"), "target clusterId"), "target clusterId");
        equal(text(placementPolicy.get("requiredClusterId"), "requiredClusterId"),
                text(target.get("clusterId"), "target clusterId"), "required target clusterId");
        equal(fixtureHosts, integer(target.get("hostCount"), "artifact hostCount"), "hostCount");
        equal(fixtureFtt, integer(target.get("failuresToTolerate"), "artifact failuresToTolerate"),
                "failuresToTolerate");
        equal(calculatedMinimum, integer(target.get("minimumHostsRequired"), "minimumHostsRequired"),
                "minimumHostsRequired");
        check(integer(target.get("hostCount"), "artifact hostCount")
                        >= 2 * integer(target.get("failuresToTolerate"), "artifact failuresToTolerate") + 1,
                "artifact host count contradicts its stated failures-to-tolerate");
        equal(text(targetFixture.get("storagePolicy"), "fixture storagePolicy"),
                text(target.get("storagePolicy"), "target storagePolicy"), "storagePolicy");

        List<Object> profiles = array(snapshot.get("placementProfiles"), "placementProfiles");
        List<Object> placements = array(architecture.get("placements"), "placements");
        equal(profiles.size(), placements.size(), "placement count");
        Map<String, Map<String, Object>> byComponent = index(placements, "component", "placements");
        int totalVcpu = 0;
        int totalMemory = 0;
        long totalDiskGiB = 0;
        for (Object profileValue : profiles) {
            Map<String, Object> profile = object(profileValue, "placement profile");
            String component = text(profile.get("component"), "profile component");
            Map<String, Object> actual = byComponent.get(component);
            check(actual != null, "missing placement for " + component);
            compareKeys(profile, actual, List.of("component", "version", "nodeCount", "formFactor",
                    "vCpuPerNode", "memoryGiBPerNode", "diskGiBPerNode", "antiAffinity"),
                    "placement " + component);
            equal(text(targetFixture.get("domainId"), "fixture domainId"),
                    text(actual.get("domainId"), "placement domainId"), component + " domainId");
            equal(text(targetFixture.get("clusterId"), "fixture clusterId"),
                    text(actual.get("clusterId"), "placement clusterId"), component + " clusterId");
            check(booleanValue(actual.get("antiAffinity"), component + " antiAffinity"),
                    component + " must use anti-affinity");
            totalVcpu += integer(actual.get("nodeCount"), component + " nodeCount")
                    * integer(actual.get("vCpuPerNode"), component + " vCpuPerNode");
            totalMemory += integer(actual.get("nodeCount"), component + " nodeCount")
                    * integer(actual.get("memoryGiBPerNode"), component + " memoryGiBPerNode");
            totalDiskGiB += (long) integer(actual.get("nodeCount"), component + " nodeCount")
                    * integer(actual.get("diskGiBPerNode"), component + " diskGiBPerNode");
        }
        int survivingHosts = fixtureHosts - fixtureFtt;
        int availableCores = survivingHosts
                * integer(targetFixture.get("hostCpuCores"), "hostCpuCores");
        int availableMemory = survivingHosts
                * integer(targetFixture.get("hostMemoryGiB"), "hostMemoryGiB");
        check(totalVcpu <= availableCores, "placement vCPU exceeds capacity after tolerated failures");
        check(totalMemory <= availableMemory, "placement memory exceeds capacity after tolerated failures");
        long availableStorageGiB = (long) integer(targetFixture.get("usableStorageTiB"),
                "usableStorageTiB") * 1024;
        check(totalDiskGiB <= availableStorageGiB,
                "placement disk exceeds target-domain usable storage");
    }

    private static void verifyMigrations(List<Object> migrations, Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        List<Object> sources = array(inventory.get("sourceProducts"), "sourceProducts");
        List<Object> assertions = array(snapshot.get("compatibilityAssertions"),
                "compatibilityAssertions");
        equal(sources.size(), migrations.size(), "migration count versus inventory");
        equal(assertions.size(), migrations.size(), "migration count versus snapshot");
        Map<String, Map<String, Object>> expectedByProduct = index(assertions, "sourceProduct",
                "compatibility assertions");
        Map<String, Map<String, Object>> actualByProduct = index(migrations, "sourceProduct",
                "migrations");
        for (int i = 0; i < sources.size(); i++) {
            Map<String, Object> source = object(sources.get(i), "source product " + i);
            String product = text(source.get("sourceProduct"), "inventory sourceProduct");
            Map<String, Object> expected = expectedByProduct.get(product);
            Map<String, Object> actual = actualByProduct.get(product);
            check(expected != null, "missing compatibility assertion for " + product);
            check(actual != null, "missing migration for " + product);
            equal(text(source.get("sourceProduct"), "inventory sourceProduct"),
                    text(expected.get("sourceProduct"), "assertion sourceProduct"),
                    "inventory/assertion product " + i);
            equal(text(source.get("version"), "inventory source version"),
                    text(expected.get("sourceVersion"), "assertion sourceVersion"),
                    "inventory/assertion version " + i);
            compareKeys(expected, actual, List.of("sourceProduct", "sourceVersion", "targetComponent",
                    "targetVersion", "migrationMode", "requiredIntermediates", "carryForward",
                    "abandon", "eogsDate"), "migration " + i);
        }
    }

    private static void verifyResearch(List<Object> research) {
        check(!research.isEmpty(), "research must contain at least one consulted Broadcom page");
        for (int i = 0; i < research.size(); i++) {
            Map<String, Object> source = object(research.get(i), "research source " + i);
            String publisher = text(source.get("publisher"), "research publisher");
            nonBlank(publisher, "research publisher " + i);
            check(publisher.toLowerCase(Locale.ROOT).contains("broadcom"),
                    "research publisher must identify Broadcom for source " + i);
            nonBlank(text(source.get("title"), "research title"), "research title " + i);

            String url = text(source.get("url"), "research URL");
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException exception) {
                throw new AssertionError("research URL is not a valid URI: " + url, exception);
            }
            String host = uri.getHost();
            check("https".equalsIgnoreCase(uri.getScheme())
                            || "http".equalsIgnoreCase(uri.getScheme()),
                    "research URL must use HTTP or HTTPS: " + url);
            check(host != null && (host.equalsIgnoreCase("broadcom.com")
                            || host.toLowerCase(Locale.ROOT).endsWith(".broadcom.com")),
                    "research URL must be Broadcom-published: " + url);

            String accessedOn = text(source.get("accessedOn"), "research accessedOn");
            try {
                LocalDate.parse(accessedOn);
            } catch (RuntimeException exception) {
                throw new AssertionError(
                        "research accessedOn must use YYYY-MM-DD: " + accessedOn, exception);
            }

            List<Object> supports = array(source.get("supports"), "research supports");
            check(!supports.isEmpty(), "research supports must not be empty for " + url);
            for (Object support : supports) {
                nonBlank(text(support, "research support claim"),
                        "research support claim for " + url);
            }
        }
    }

    private static void verifySteps(List<Object> steps, Map<String, Object> snapshot) {
        List<Object> expectedSteps = array(snapshot.get("requiredSteps"), "requiredSteps");
        equal(expectedSteps.size(), steps.size(), "ordered step count");
        for (int i = 0; i < expectedSteps.size(); i++) {
            Map<String, Object> expected = object(expectedSteps.get(i), "required step " + i);
            Map<String, Object> actual = object(steps.get(i), "ordered step " + i);
            equal(i + 1, integer(actual.get("order"), "step order"), "step order " + i);
            equal(text(expected.get("id"), "required step id"), text(actual.get("id"), "step id"),
                    "step id " + i);
            nonBlank(text(actual.get("action"), "step action"), "step action " + i);
            String gate = text(actual.get("gate"), "step gate");
            nonBlank(gate, "step gate " + i);
            nonBlank(text(actual.get("outcome"), "step outcome"), "step outcome " + i);
            for (Object tokenValue : array(expected.get("gateMustMention"), "gateMustMention")) {
                String token = text(tokenValue, "gate token");
                check(gate.toLowerCase(Locale.ROOT).contains(token.toLowerCase(Locale.ROOT)),
                        "gate for " + actual.get("id") + " must mention: " + token);
            }
        }
    }

    private static void compareKeys(Map<String, Object> expected, Map<String, Object> actual,
            List<String> keys, String context) {
        for (String key : keys) {
            Object left = expected.get(key);
            Object right = actual.get(key);
            check(deepEquals(left, right), context + " has incorrect " + key
                    + " (expected " + left + ", got " + right + ")");
        }
    }

    private static boolean deepEquals(Object left, Object right) {
        if (left instanceof Number && right instanceof Number) {
            return ((Number) left).longValue() == ((Number) right).longValue();
        }
        return left == null ? right == null : left.equals(right);
    }

    private static Map<String, Map<String, Object>> index(List<Object> values, String key,
            String context) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, context + " item");
            String itemKey = text(item.get(key), context + " key");
            check(result.put(itemKey, item) == null, "duplicate " + key + " in " + context + ": " + itemKey);
        }
        return result;
    }

    private static Object parseFile(Path path) throws IOException {
        check(Files.isRegularFile(path), "missing required file: " + path);
        String raw = Files.readString(path, StandardCharsets.UTF_8);
        return new Json(raw).parse();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String context) {
        check(value instanceof Map<?, ?>, context + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String context) {
        check(value instanceof List<?>, context + " must be an array");
        return (List<Object>) value;
    }

    private static String text(Object value, String context) {
        check(value instanceof String, context + " must be a string");
        return (String) value;
    }

    private static int integer(Object value, String context) {
        check(value instanceof Number, context + " must be an integer");
        double numeric = ((Number) value).doubleValue();
        check(Double.isFinite(numeric) && numeric == Math.rint(numeric),
                context + " must be an integer");
        long number = ((Number) value).longValue();
        check(number >= Integer.MIN_VALUE && number <= Integer.MAX_VALUE,
                context + " is outside integer range");
        return (int) number;
    }

    private static boolean booleanValue(Object value, String context) {
        check(value instanceof Boolean, context + " must be a boolean");
        return (Boolean) value;
    }

    private static void nonBlank(String value, String context) {
        check(!value.isBlank(), context + " must not be blank");
    }

    private static void equal(Object expected, Object actual, String context) {
        check(expected.equals(actual), context + " mismatch (expected " + expected + ", got " + actual + ")");
    }

    private static void expectArgumentCountRejected(String[] args, String context) throws Exception {
        boolean rejected = false;
        try {
            MigrationPlanClient.main(args);
        } catch (Exception exception) {
            rejected = true;
        }
        check(rejected, "client must reject " + context);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class Json {
        private final String input;
        private int position;

        Json(String input) {
            this.input = input;
        }

        Object parse() {
            Object value = value();
            whitespace();
            if (position != input.length()) {
                fail("unexpected trailing content");
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (position >= input.length()) {
                fail("unexpected end of input");
            }
            char c = input.charAt(position);
            if (c == '{') return objectValue();
            if (c == '[') return arrayValue();
            if (c == '"') return stringValue();
            if (c == 't') return literal("true", Boolean.TRUE);
            if (c == 'f') return literal("false", Boolean.FALSE);
            if (c == 'n') return literal("null", null);
            if (c == '-' || Character.isDigit(c)) return numberValue();
            fail("unexpected character '" + c + "'");
            return null;
        }

        private Map<String, Object> objectValue() {
            expect('{');
            Map<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) return result;
            while (true) {
                whitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    fail("object key must be a string");
                }
                String key = stringValue();
                whitespace();
                expect(':');
                if (result.containsKey(key)) fail("duplicate key: " + key);
                result.put(key, value());
                whitespace();
                if (take('}')) return result;
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            expect('[');
            List<Object> result = new ArrayList<>();
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
                char c = input.charAt(position++);
                if (c == '"') return result.toString();
                if (c == '\\') {
                    if (position >= input.length()) fail("unterminated escape");
                    char escaped = input.charAt(position++);
                    switch (escaped) {
                        case '"': result.append('"'); break;
                        case '\\': result.append('\\'); break;
                        case '/': result.append('/'); break;
                        case 'b': result.append('\b'); break;
                        case 'f': result.append('\f'); break;
                        case 'n': result.append('\n'); break;
                        case 'r': result.append('\r'); break;
                        case 't': result.append('\t'); break;
                        case 'u': result.append(unicode()); break;
                        default: fail("invalid escape: \\" + escaped);
                    }
                } else {
                    if (c < 0x20) fail("control character in string");
                    result.append(c);
                }
            }
            fail("unterminated string");
            return null;
        }

        private char unicode() {
            if (position + 4 > input.length()) fail("short unicode escape");
            String hex = input.substring(position, position + 4);
            position += 4;
            try {
                return (char) Integer.parseInt(hex, 16);
            } catch (NumberFormatException ex) {
                fail("invalid unicode escape");
                return 0;
            }
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
            if (take('e') || take('E')) {
                decimal = true;
                if (!take('+')) take('-');
                digits();
            }
            String number = input.substring(start, position);
            try {
                if (decimal) return Double.valueOf(number);
                return Long.valueOf(number);
            } catch (NumberFormatException ex) {
                fail("invalid number");
                return null;
            }
        }

        private void digits() {
            int start = position;
            while (position < input.length() && Character.isDigit(input.charAt(position))) position++;
            if (start == position) fail("expected digit");
        }

        private Object literal(String literal, Object value) {
            if (!input.startsWith(literal, position)) fail("invalid literal");
            position += literal.length();
            return value;
        }

        private void whitespace() {
            while (position < input.length() && Character.isWhitespace(input.charAt(position))) position++;
        }

        private void expect(char expected) {
            whitespace();
            if (!take(expected)) fail("expected '" + expected + "'");
        }

        private boolean take(char expected) {
            if (position < input.length() && input.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void fail(String message) {
            throw new IllegalArgumentException(message + " at character " + position);
        }
    }
}
