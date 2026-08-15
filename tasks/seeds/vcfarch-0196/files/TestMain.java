import java.math.BigDecimal;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {
    private static final String INVENTORY_SHA256 =
            "1f6dfd311d4eb2881446e7276c5b8c06890cff8b60e9d3dae9c7c1402d278c94";
    private static final String SNAPSHOT_SHA256 =
            "94d6301e3090f8f24d18f9eb0dbf4de2bba409a48b1ca321f3d7fc3cf590de53";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        Path inventoryPath = args.length > 0 ? Path.of(args[0]) : Path.of("estate-inventory.json");
        Path snapshotPath = args.length > 1 ? Path.of(args[1]) : Path.of("compatibility-snapshot.json");

        require(INVENTORY_SHA256.equals(sha256(inventoryPath)), "estate inventory fixture was modified");
        require(SNAPSHOT_SHA256.equals(sha256(snapshotPath)), "compatibility snapshot fixture was modified");

        Map<String, Object> inventory = object(Json.parse(Files.readString(inventoryPath)), "inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(snapshotPath)), "snapshot");

        String first = ArchitectureClient.build(inventoryPath, snapshotPath);
        String second = ArchitectureClient.build(inventoryPath, snapshotPath);
        require(first != null && !first.isBlank(), "client returned an empty artifact");
        require(first.equals(second), "client output is not deterministic");

        Map<String, Object> artifact = object(Json.parse(first), "artifact");
        equal("1.0", string(artifact, "schemaVersion"), "schemaVersion");
        equal(string(inventory, "estateId"), string(artifact, "estateId"), "estateId");

        verifyManagementDomain(artifact, inventory, snapshot);
        verifyMigrationPlan(artifact, inventory, snapshot);
        verifySources(artifact);

        System.out.println("PASS: VCF migration architecture is valid");
    }

    private static void verifySources(Map<String, Object> artifact) {
        List<Object> values = array(artifact.get("sourcesConsulted"), "artifact sourcesConsulted");
        require(!values.isEmpty(), "sourcesConsulted must contain at least one fetched publication");

        Set<String> seenUrls = new LinkedHashSet<>();
        for (Object value : values) {
            Map<String, Object> source = object(value, "consulted source");
            string(source, "title");
            equal("Broadcom", string(source, "publisher"), "source publisher");

            String url = string(source, "url");
            require(seenUrls.add(url), "duplicate consulted source URL " + url);
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException exception) {
                throw new AssertionError("source URL is not a valid URI: " + url, exception);
            }
            String host = uri.getHost();
            require("https".equalsIgnoreCase(uri.getScheme()), "source URL must use HTTPS: " + url);
            require(host != null
                            && (host.equalsIgnoreCase("broadcom.com")
                                    || host.toLowerCase().endsWith(".broadcom.com")),
                    "source URL must be Broadcom-published: " + url);

            String accessedOn = string(source, "accessedOn");
            try {
                LocalDate.parse(accessedOn);
            } catch (RuntimeException exception) {
                throw new AssertionError("accessedOn must use YYYY-MM-DD: " + accessedOn, exception);
            }

            List<String> claims = strings(source.get("claims"), "source claims");
            require(!claims.isEmpty(), "source claims must not be empty for " + url);
            for (String claim : claims) {
                require(!claim.isBlank(), "source claims must contain only nonempty strings for " + url);
            }
        }
    }

    private static void verifyManagementDomain(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> actual = object(
                object(artifact.get("architecture"), "architecture").get("managementDomain"),
                "architecture.managementDomain");
        Map<String, Object> expected = object(inventory.get("managementDomain"), "inventory.managementDomain");
        Map<String, Object> rules = object(snapshot.get("managementDomainRules"), "snapshot.managementDomainRules");

        equal(string(expected, "cluster"), string(actual, "cluster"), "management cluster");
        equal(string(expected, "topology"), string(actual, "topology"), "management topology");
        equal(string(expected, "storagePolicy"), string(actual, "storagePolicy"), "management storage policy");

        long declaredLocalFtt = integer(actual, "localHostFailuresToTolerate");
        long declaredSiteFtt = integer(actual, "siteFailuresToTolerate");
        require(declaredLocalFtt >= 0, "localHostFailuresToTolerate must be non-negative");
        require(declaredSiteFtt >= 0, "siteFailuresToTolerate must be non-negative");

        List<Object> expectedSites = array(expected.get("sites"), "inventory sites");
        List<Object> actualSites = array(actual.get("dataSites"), "artifact dataSites");
        require(actualSites.size() == expectedSites.size(), "dataSites count does not match the inventory");
        require(actualSites.size() >= integer(rules, "minimumDataSites"), "too few data sites");

        Map<String, Long> inventoryHosts = new LinkedHashMap<>();
        for (Object value : expectedSites) {
            Map<String, Object> site = object(value, "inventory site");
            inventoryHosts.put(string(site, "id"), integer(site, "hosts"));
        }

        long raid1Minimum = Math.addExact(Math.multiplyExact(2L, declaredLocalFtt), 1L);
        long minimumHosts = Math.max(integer(rules, "vcfMinimumHostsPerDataSite"), raid1Minimum);
        Set<String> seen = new LinkedHashSet<>();
        for (Object value : actualSites) {
            Map<String, Object> site = object(value, "artifact data site");
            String id = string(site, "id");
            long hostCount = integer(site, "hostCount");
            require(seen.add(id), "duplicate data site " + id);
            require(inventoryHosts.containsKey(id), "unknown data site " + id);
            equal(inventoryHosts.get(id), hostCount, "host count for " + id);
            require(hostCount >= minimumHosts,
                    "host count contradicts localHostFailuresToTolerate at " + id
                            + ": declared FTT=" + declaredLocalFtt
                            + " requires at least " + minimumHosts + " hosts but found " + hostCount);
        }
        require(seen.equals(inventoryHosts.keySet()), "artifact does not name both inventory data sites");

        equal(integer(expected, "siteFailuresToTolerate"), declaredSiteFtt,
                "siteFailuresToTolerate");
        equal(integer(rules, "requiredSiteFailuresToTolerate"), declaredSiteFtt,
                "snapshot siteFailuresToTolerate");
        equal(integer(expected, "localHostFailuresToTolerate"), declaredLocalFtt,
                "localHostFailuresToTolerate");
        equal(integer(rules, "requiredLocalHostFailuresToTolerate"), declaredLocalFtt,
                "snapshot localHostFailuresToTolerate");

        Map<String, Object> candidate = object(expected.get("witnessCandidate"), "inventory witnessCandidate");
        Map<String, Object> witness = object(actual.get("witness"), "artifact witness");
        String witnessSite = string(witness, "site");
        equal(string(candidate, "site"), witnessSite, "witness site");
        require(!inventoryHosts.containsKey(witnessSite), "witness must be at a third site");
        equal(string(candidate, "inventoryPlacement"), string(witness, "inventoryPlacement"),
                "witness inventory placement");
        equal(bool(candidate, "clusterMember"), bool(witness, "clusterMember"),
                "witness cluster membership");
        require(!bool(witness, "clusterMember"), "witness must be standalone and outside every cluster");
        equal(string(candidate, "datastore"), string(witness, "datastore"), "witness datastore");
        require(!string(witness, "datastore").equals(string(expected, "cluster")),
                "witness must not use the stretched management datastore");
        equal(bool(candidate, "layer3ToDataSites"), bool(witness, "layer3ToDataSites"),
                "witness Layer 3 connectivity");
        require(bool(witness, "layer3ToDataSites"), "witness traffic to data sites must use Layer 3");
    }

    private static void verifyMigrationPlan(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        List<Object> actualPlan = array(artifact.get("migrationPlan"), "artifact migrationPlan");
        List<Object> expectedPlan = array(snapshot.get("migrationSteps"), "snapshot migrationSteps");
        require(actualPlan.size() == expectedPlan.size(), "migrationPlan must contain exactly three steps");

        Map<String, Map<String, Object>> products = indexBy(
                array(inventory.get("products"), "inventory products"), "inventoryId", "inventory product");
        Map<String, Map<String, Object>> profiles = indexBy(
                array(object(snapshot.get("sizing"), "snapshot sizing").get("profiles"), "sizing profiles"),
                "id", "sizing profile");
        Set<String> migratedProducts = new LinkedHashSet<>();

        for (int i = 0; i < expectedPlan.size(); i++) {
            Map<String, Object> expectedStep = object(expectedPlan.get(i), "snapshot migration step");
            Map<String, Object> actualStep = object(actualPlan.get(i), "artifact migration step");
            long expectedOrder = integer(expectedStep, "order");
            equal(expectedOrder, integer(actualStep, "order"), "migration step order");
            equal((long) i + 1L, expectedOrder, "snapshot migration order");

            String inventoryId = string(expectedStep, "inventoryId");
            require(migratedProducts.add(inventoryId), "duplicate migration for " + inventoryId);
            Map<String, Object> product = products.get(inventoryId);
            require(product != null, "snapshot references unknown product " + inventoryId);

            Map<String, Object> source = object(actualStep.get("source"), "step source");
            equal(inventoryId, string(source, "inventoryId"), "source inventoryId");
            equal(string(product, "product"), string(source, "product"), "source product");
            equal(string(product, "version"), string(source, "version"), "source version");

            Map<String, Object> target = object(actualStep.get("target"), "step target");
            equal(string(expectedStep, "targetComponent"), string(target, "component"), "target component");
            equal(string(expectedStep, "targetVersion"), string(target, "version"), "target version");
            equal(string(expectedStep, "migrationMode"), string(target, "migrationMode"), "migration mode");

            Map<String, Object> support = object(actualStep.get("support"), "step support");
            equal(string(expectedStep, "sourceEndOfGeneralSupport"),
                    string(support, "sourceEndOfGeneralSupport"), "source EOGS boundary");
            equal(string(expectedStep, "statusOnPlanningDate"),
                    string(support, "statusOnPlanningDate"), "source planning-date support status");
            equal(string(expectedStep, "minimumSupportedTargetVersion"),
                    string(support, "minimumSupportedTargetVersion"), "minimum target version");
            equal(strings(expectedStep.get("unsupportedTargetVersions"), "expected unsupported targets"),
                    strings(support.get("unsupportedTargetVersions"), "unsupported targets"),
                    "unsupported target versions");
            equal(strings(expectedStep.get("unsupportedMigrationModes"), "expected unsupported modes"),
                    strings(support.get("unsupportedMigrationModes"), "unsupported modes"),
                    "unsupported migration modes");

            equal(strings(expectedStep.get("carryForward"), "expected carryForward"),
                    strings(actualStep.get("carryForward"), "carryForward"), "carryForward for " + inventoryId);
            equal(strings(expectedStep.get("abandon"), "expected abandon"),
                    strings(actualStep.get("abandon"), "abandon"), "abandon for " + inventoryId);
            equal(strings(expectedStep.get("gates"), "expected gates"),
                    strings(actualStep.get("gates"), "gates"), "ordered gates for " + inventoryId);

            Map<String, Object> placement = object(actualStep.get("placement"), "step placement");
            Map<String, Object> management = object(inventory.get("managementDomain"), "inventory managementDomain");
            equal(string(management, "cluster"), string(placement, "cluster"), "placement cluster");
            equal(string(management, "storagePolicy"), string(placement, "storagePolicy"),
                    "placement storage policy");
            equal(string(expectedStep, "recovery"), string(placement, "recovery"), "recovery policy");

            Map<String, Map<String, Object>> actualRoles = verifyRoles(
                    array(placement.get("roles"), "placement roles"),
                    array(expectedStep.get("roles"), "expected roles"), profiles, management);
            verifySizing(inventoryId, actualRoles, profiles, inventory, snapshot);
        }

        require(migratedProducts.equals(products.keySet()), "not every inventory product is migrated exactly once");
    }

    private static Map<String, Map<String, Object>> verifyRoles(
            List<Object> actualRoleValues,
            List<Object> expectedRoleValues,
            Map<String, Map<String, Object>> profiles,
            Map<String, Object> management) {
        Map<String, Map<String, Object>> actualRoles = indexBy(actualRoleValues, "role", "placement role");
        Map<String, Map<String, Object>> expectedRoles = indexBy(expectedRoleValues, "role", "expected role");
        require(actualRoles.keySet().equals(expectedRoles.keySet()), "placement roles do not match snapshot");

        Set<String> dataSiteIds = new LinkedHashSet<>();
        for (Object siteValue : array(management.get("sites"), "management sites")) {
            dataSiteIds.add(string(object(siteValue, "management site"), "id"));
        }

        for (Map.Entry<String, Map<String, Object>> entry : expectedRoles.entrySet()) {
            String roleName = entry.getKey();
            Map<String, Object> expected = entry.getValue();
            Map<String, Object> actual = actualRoles.get(roleName);
            String profileId = string(expected, "profile");
            Map<String, Object> profile = profiles.get(profileId);
            require(profile != null, "unknown profile " + profileId);

            equal(profileId, string(actual, "profile"), "profile for role " + roleName);
            equal(integer(expected, "nodes"), integer(actual, "nodes"), "node count for role " + roleName);
            equal(integer(profile, "vCpu"), integer(actual, "vCpuPerNode"), "vCPU for role " + roleName);
            equal(integer(profile, "memoryGiB"), integer(actual, "memoryGiBPerNode"),
                    "memory for role " + roleName);
            equal(integer(profile, "storageGiB"), integer(actual, "storageGiBPerNode"),
                    "storage for role " + roleName);

            Map<String, Object> expectedCounts = object(expected.get("siteCounts"), "expected siteCounts");
            Map<String, Object> actualCounts = object(actual.get("siteCounts"), "actual siteCounts");
            require(actualCounts.keySet().equals(dataSiteIds), "siteCounts must name exactly both data sites");
            require(actualCounts.keySet().equals(expectedCounts.keySet()), "siteCounts keys do not match snapshot");
            long total = 0;
            for (String site : dataSiteIds) {
                long count = integer(actualCounts, site);
                require(count >= 0, "negative site count for role " + roleName);
                equal(integer(expectedCounts, site), count, "site count for " + roleName + " at " + site);
                total += count;
            }
            equal(integer(actual, "nodes"), total, "siteCounts sum for role " + roleName);
        }
        return actualRoles;
    }

    private static void verifySizing(
            String inventoryId,
            Map<String, Map<String, Object>> roles,
            Map<String, Map<String, Object>> profiles,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> capacity = object(inventory.get("capacityRequirements"), "capacity requirements");
        long headroom = integer(object(snapshot.get("sizing"), "sizing"), "headroomPercent");
        if (inventoryId.equals("ops-prod")) {
            Map<String, Object> role = roles.get("analytics");
            Map<String, Object> profile = profiles.get(string(role, "profile"));
            long available = Math.multiplyExact(integer(role, "nodes"), integer(profile, "objectCapacityPerNode"));
            long required = withHeadroom(integer(capacity, "operationsMonitoredObjects"), headroom);
            require(available >= required,
                    "VCF Operations sizing lacks monitored-object headroom: " + available + " < " + required);
        } else if (inventoryId.equals("automation-prod")) {
            Map<String, Object> role = roles.get("automation-appliance");
            Map<String, Object> profile = profiles.get(string(role, "profile"));
            long available = Math.multiplyExact(
                    integer(role, "nodes"), integer(profile, "concurrentRequestCapacityPerNode"));
            long required = withHeadroom(integer(capacity, "automationConcurrentRequests"), headroom);
            require(available >= required,
                    "VCF Automation sizing lacks concurrency headroom: " + available + " < " + required);
        } else if (inventoryId.equals("logs-prod")) {
            Map<String, Object> role = roles.get("logs-data");
            Map<String, Object> profile = profiles.get(string(role, "profile"));
            long available = Math.multiplyExact(integer(role, "nodes"), integer(profile, "usableStorageGiBPerNode"));
            long retained = Math.multiplyExact(
                    integer(capacity, "logsDailyIngestGiB"), integer(capacity, "logsTargetRetentionDays"));
            long required = withHeadroom(retained, headroom);
            require(available >= required,
                    "VCF Operations for Logs sizing lacks retention headroom: " + available + " < " + required);
        } else {
            fail("no sizing rule for inventory product " + inventoryId);
        }
    }

    private static long withHeadroom(long base, long percent) {
        return Math.floorDiv(Math.addExact(Math.multiplyExact(base, 100L + percent), 99L), 100L);
    }

    private static Map<String, Map<String, Object>> indexBy(
            List<Object> values, String key, String label) {
        Map<String, Map<String, Object>> indexed = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, label);
            String id = string(item, key);
            require(indexed.put(id, item) == null, "duplicate " + label + " key " + id);
        }
        return indexed;
    }

    private static String sha256(Path path) throws Exception {
        byte[] bytes = Files.readAllBytes(path);
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
        StringBuilder out = new StringBuilder();
        for (byte value : digest) {
            out.append(String.format("%02x", value & 0xff));
        }
        return out.toString();
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

    private static List<String> strings(Object value, String label) {
        List<Object> values = array(value, label);
        List<String> result = new ArrayList<>();
        for (Object item : values) {
            require(item instanceof String, label + " must contain only strings");
            result.add((String) item);
        }
        return result;
    }

    private static String string(Map<String, Object> object, String key) {
        Object value = object.get(key);
        require(value instanceof String && !((String) value).isBlank(), key + " must be a nonempty string");
        return (String) value;
    }

    private static long integer(Map<String, Object> object, String key) {
        Object value = object.get(key);
        require(value instanceof Long, key + " must be an integer");
        return (Long) value;
    }

    private static boolean bool(Map<String, Object> object, String key) {
        Object value = object.get(key);
        require(value instanceof Boolean, key + " must be a boolean");
        return (Boolean) value;
    }

    private static void equal(Object expected, Object actual, String label) {
        require(expected.equals(actual), label + " mismatch: expected " + expected + " but found " + actual);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            fail(message);
        }
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    private static final class Json {
        private final String text;
        private int position;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            Json parser = new Json(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != text.length()) {
                parser.error("trailing content");
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (position >= text.length()) {
                return error("unexpected end of input");
            }
            char current = text.charAt(position);
            if (current == '{') {
                return objectValue();
            }
            if (current == '[') {
                return arrayValue();
            }
            if (current == '"') {
                return stringValue();
            }
            if (current == 't') {
                literal("true");
                return Boolean.TRUE;
            }
            if (current == 'f') {
                literal("false");
                return Boolean.FALSE;
            }
            if (current == 'n') {
                literal("null");
                return null;
            }
            return numberValue();
        }

        private Map<String, Object> objectValue() {
            expect('{');
            Map<String, Object> values = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return values;
            }
            while (true) {
                whitespace();
                String key = stringValue();
                whitespace();
                expect(':');
                if (values.containsKey(key)) {
                    error("duplicate object key " + key);
                }
                values.put(key, value());
                whitespace();
                if (take('}')) {
                    return values;
                }
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            expect('[');
            List<Object> values = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return values;
            }
            while (true) {
                values.add(value());
                whitespace();
                if (take(']')) {
                    return values;
                }
                expect(',');
            }
        }

        private String stringValue() {
            expect('"');
            StringBuilder value = new StringBuilder();
            while (position < text.length()) {
                char current = text.charAt(position++);
                if (current == '"') {
                    return value.toString();
                }
                if (current == '\\') {
                    if (position >= text.length()) {
                        return error("unterminated escape");
                    }
                    char escaped = text.charAt(position++);
                    switch (escaped) {
                        case '"': value.append('"'); break;
                        case '\\': value.append('\\'); break;
                        case '/': value.append('/'); break;
                        case 'b': value.append('\b'); break;
                        case 'f': value.append('\f'); break;
                        case 'n': value.append('\n'); break;
                        case 'r': value.append('\r'); break;
                        case 't': value.append('\t'); break;
                        case 'u':
                            if (position + 4 > text.length()) {
                                return error("short unicode escape");
                            }
                            try {
                                value.append((char) Integer.parseInt(text.substring(position, position + 4), 16));
                            } catch (NumberFormatException exception) {
                                return error("invalid unicode escape");
                            }
                            position += 4;
                            break;
                        default: return error("invalid escape");
                    }
                } else {
                    if (current < 0x20) {
                        return error("control character in string");
                    }
                    value.append(current);
                }
            }
            return error("unterminated string");
        }

        private Long numberValue() {
            int start = position;
            if (take('-') && position >= text.length()) {
                return error("invalid number");
            }
            if (take('0')) {
                // A single leading zero is valid.
            } else {
                digits();
            }
            if (position < text.length()
                    && (text.charAt(position) == '.' || text.charAt(position) == 'e' || text.charAt(position) == 'E')) {
                return error("only integer JSON numbers are accepted by this harness");
            }
            try {
                return new BigDecimal(text.substring(start, position)).longValueExact();
            } catch (RuntimeException exception) {
                return error("invalid integer");
            }
        }

        private void digits() {
            int start = position;
            while (position < text.length() && Character.isDigit(text.charAt(position))) {
                position++;
            }
            if (start == position) {
                error("expected digit");
            }
        }

        private void literal(String expected) {
            if (!text.startsWith(expected, position)) {
                error("expected " + expected);
            }
            position += expected.length();
        }

        private void expect(char expected) {
            whitespace();
            if (!take(expected)) {
                error("expected '" + expected + "'");
            }
        }

        private boolean take(char expected) {
            if (position < text.length() && text.charAt(position) == expected) {
                position++;
                return true;
            }
            return false;
        }

        private void whitespace() {
            while (position < text.length() && Character.isWhitespace(text.charAt(position))) {
                position++;
            }
        }

        private <T> T error(String message) {
            throw new IllegalArgumentException("invalid JSON at character " + position + ": " + message);
        }
    }
}
