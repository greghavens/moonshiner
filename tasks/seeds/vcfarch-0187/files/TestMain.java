import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** Deterministic verifier. It deliberately contains no network access. */
public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");
    private static final Path INSTALLER_SPEC = Path.of("installer-spec.json");
    private static final String INVENTORY_SHA256 = "42b0c7a9dc95eeb8a06fac973f7e8a78a37c77699b543a26daed54f75d7a19b3";
    private static final String SNAPSHOT_SHA256 = "ce8d6d02ada8c52469570557483cd046b7b01403dfe9751e5c01fc4a48358840";
    private static final String INSTALLER_SPEC_SHA256 = "b2252f26671e3d40d378b51e838f884da682efdf844f3991b9df19de785bb3c2";

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        String inventoryText = Files.readString(INVENTORY);
        String snapshotText = Files.readString(SNAPSHOT);
        String artifactText = MigrationPlanClient.buildPlan(inventoryText, snapshotText);

        // Schema validation is intentionally the first verification phase.
        Object artifactValue = Json.parse(artifactText);
        String installerText = Files.readString(INSTALLER_SPEC);
        Map<String, Object> installer = object(Json.parse(installerText), "installer-spec");
        Map<String, Object> artifactSchema = object(installer.get("artifactSchema"), "installer-spec.artifactSchema");
        Schema.validate(artifactValue, artifactSchema);

        // No fixture or compatibility assertion is read until the artifact passes its own schema.
        protectedText(inventoryText, INVENTORY_SHA256, "estate inventory");
        protectedText(snapshotText, SNAPSHOT_SHA256, "compatibility snapshot");
        protectedText(installerText, INSTALLER_SPEC_SHA256, "installer specification");
        Map<String, Object> plan = object(artifactValue, "artifact");
        Map<String, Object> inventory = object(Json.parse(inventoryText), "inventory");
        Map<String, Object> snapshot = object(Json.parse(snapshotText), "snapshot");
        verify(plan, inventory, snapshot);
        System.out.println("PASS: migration architecture matches schema, inventory, and pinned compatibility snapshot");
    }

    private static void protectedText(String text, String expectedSha256, String label) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        StringBuilder actual = new StringBuilder(64);
        for (byte value : digest) actual.append(String.format("%02x", value & 0xff));
        eq(actual.toString(), expectedSha256, label + " integrity");
    }

    private static void verify(Map<String, Object> plan, Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        eq(plan.get("schemaVersion"), "1.0", "schemaVersion");
        eq(plan.get("estateId"), inventory.get("estateId"), "estateId");
        eq(plan.get("snapshotId"), snapshot.get("snapshotId"), "snapshotId");
        verifyResearchSources(plan);

        Map<String, Object> architecture = object(plan.get("architecture"), "architecture");
        eq(architecture.get("targetVersion"), snapshot.get("targetVersion"), "architecture.targetVersion");
        verifyLifecycleManager(architecture, inventory, snapshot);
        verifyManagementDomain(architecture, inventory, snapshot);
        verifyComponents(architecture, inventory, snapshot);

        eq(plan.get("lifecycleBoundaries"), snapshot.get("lifecycleBoundaries"),
                "lifecycleBoundaries must equal the pinned snapshot");
        eq(plan.get("migrationCompatibility"), snapshot.get("migrationCompatibility"),
                "migrationCompatibility must equal the pinned snapshot");
        eq(plan.get("contentDecisions"), snapshot.get("contentDecisions"),
                "contentDecisions must equal the pinned snapshot");

        verifyInventoryCoverage(plan, inventory);
        verifyGatesAndSteps(plan, snapshot);
    }

    private static void verifyResearchSources(Map<String, Object> plan) {
        Set<String> urls = new LinkedHashSet<>();
        StringBuilder claims = new StringBuilder();
        for (Object raw : array(plan.get("researchSources"), "researchSources")) {
            Map<String, Object> source = object(raw, "researchSource");
            String url = string(source.get("url"), "researchSource.url");
            check(urls.add(url), "research source URL repeated: " + url);
            check(url.startsWith("https://") &&
                            (url.matches("https://broadcom\\.com/.*") ||
                             url.matches("https://[^/]+\\.broadcom\\.com/.*")),
                    "research source must be an HTTPS Broadcom page: " + url);
            for (String claim : strings(source.get("claimsUsed"), "researchSource.claimsUsed")) {
                claims.append(' ').append(claim.toLowerCase(java.util.Locale.ROOT));
            }
        }
        String used = claims.toString();
        check(Pattern.compile("upgrade|migrat|greenfield|fresh deploy|fleet import|transition").matcher(used).find(),
                "research claims must cover supported migration paths");
        check(Pattern.compile("content|historical|integration|transfer|agent|dashboard|metric|configuration").matcher(used).find(),
                "research claims must cover content compatibility");
        check(Pattern.compile("sizing|capacity|node|placement|fault domain|witness|cluster|cpu|memory|disk").matcher(used).find(),
                "research claims must cover sizing or placement constraints");
        check(Pattern.compile("general support|eogs|support ends|end.of.support|lifecycle|out of support").matcher(used).find(),
                "research claims must cover lifecycle boundaries");
    }

    private static void verifyLifecycleManager(Map<String, Object> architecture,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        Map<String, Object> actual = object(architecture.get("lifecycleManager"), "architecture.lifecycleManager");
        Map<String, Object> required = object(snapshot.get("requiredLifecycleManager"), "requiredLifecycleManager");
        Map<String, Object> current = object(inventory.get("lifecycleManager"), "inventory.lifecycleManager");
        eq(actual.get("product"), required.get("product"), "lifecycleManager.product");
        eq(actual.get("sourceVersion"), required.get("sourceVersion"), "lifecycleManager.sourceVersion");
        eq(actual.get("sourceVersion"), current.get("version"), "lifecycleManager source inventory");
        eq(actual.get("requiredPatch"), required.get("requiredPatch"), "lifecycleManager.requiredPatch");
        assertSiteCompute(inventory, string(actual.get("placementSite"), "placementSite"),
                string(actual.get("compute"), "compute"), "lifecycleManager placement");
    }

    private static void verifyManagementDomain(Map<String, Object> architecture,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        Map<String, Object> actual = object(architecture.get("managementDomain"), "architecture.managementDomain");
        Map<String, Object> required = object(snapshot.get("managementDomainRequirement"), "managementDomainRequirement");
        Map<String, Object> source = object(inventory.get("managementDomain"), "inventory.managementDomain");
        eq(actual.get("topology"), required.get("topology"), "managementDomain.topology");
        eq(actual.get("preferredSite"), required.get("preferredSite"), "managementDomain.preferredSite");
        eq(actual.get("secondarySite"), required.get("secondarySite"), "managementDomain.secondarySite");
        eq(actual.get("stretchedDatastore"), source.get("stretchedDatastore"), "managementDomain.stretchedDatastore");
        eq(actual.get("witnessRolesDistinct"), required.get("witnessRolesMustBeDistinct"),
                "managementDomain.witnessRolesDistinct");

        Map<String, Object> vsan = object(actual.get("vSanWitness"), "managementDomain.vSanWitness");
        Map<String, Object> ops = object(actual.get("operationsCaWitness"), "managementDomain.operationsCaWitness");
        verifyWitness(vsan, required, "vSanWitnessAssetId", "vSAN-voting-witness");
        verifyWitness(ops, required, "operationsCaWitnessAssetId", "operations-continuous-availability-witness");
        check(!Objects.equals(vsan.get("assetId"), ops.get("assetId")), "witness assets must be distinct");
        assertSiteCompute(inventory, string(vsan.get("site"), "vSanWitness.site"),
                string(vsan.get("compute"), "vSanWitness.compute"), "vSAN witness placement");
        assertSiteCompute(inventory, string(ops.get("site"), "operationsCaWitness.site"),
                string(ops.get("compute"), "operationsCaWitness.compute"), "Operations CA witness placement");
    }

    private static void verifyWitness(Map<String, Object> actual, Map<String, Object> required,
            String assetKey, String role) {
        eq(actual.get("assetId"), required.get(assetKey), assetKey);
        eq(actual.get("site"), required.get("witnessSite"), assetKey + ".site");
        eq(actual.get("compute"), required.get("witnessCompute"), assetKey + ".compute");
        eq(actual.get("role"), role, assetKey + ".role");
        eq(actual.get("outsideStretchedCluster"), required.get("vSanWitnessMustBeOutsideStretchedCluster"),
                assetKey + ".outsideStretchedCluster");
    }

    private static void verifyComponents(Map<String, Object> architecture,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        List<Object> actual = array(architecture.get("components"), "architecture.components");
        List<Object> expected = array(snapshot.get("componentRequirements"), "componentRequirements");
        eq(actual, expected, "architecture.components must equal the pinned sizing and placement snapshot");

        for (Object raw : actual) {
            Map<String, Object> component = object(raw, "component");
            long placed = 0;
            for (Object placementRaw : array(component.get("placements"), "component.placements")) {
                Map<String, Object> placement = object(placementRaw, "placement");
                assertSiteCompute(inventory, string(placement.get("site"), "placement.site"),
                        string(placement.get("compute"), "placement.compute"), "component placement");
                placed += integer(placement.get("count"), "placement.count");
            }
            eq(placed, integer(component.get("nodeCount"), "component.nodeCount"),
                    component.get("id") + " placement count");
        }

        Map<String, Object> opsSource = sourceById(inventory, "ops-prod");
        Map<String, Object> autoSource = sourceById(inventory, "automation-prod");
        Map<String, Object> logsSource = sourceById(inventory, "logs-prod");
        Map<String, Object> opsCapacity = capacity(componentById(actual, "vcf-operations"));
        Map<String, Object> autoCapacity = capacity(componentById(actual, "vcf-automation"));
        Map<String, Object> logsCapacity = capacity(componentById(actual, "vcf-operations-for-logs"));
        check(integer(opsCapacity.get("objects"), "ops capacity objects") >= integer(opsSource.get("objects"), "ops objects"),
                "VCF Operations object capacity is undersized");
        check(integer(opsCapacity.get("collectedMetrics"), "ops capacity metrics") >= integer(opsSource.get("collectedMetrics"), "ops metrics"),
                "VCF Operations metric capacity is undersized");
        check(integer(autoCapacity.get("concurrentDeployments"), "automation capacity") >= integer(autoSource.get("concurrentDeployments"), "automation demand"),
                "VCF Automation is undersized");
        check(integer(logsCapacity.get("ingestionGbPerDay"), "logs capacity") >= integer(logsSource.get("ingestionGbPerDay"), "logs ingestion"),
                "VCF Operations for Logs is undersized");
    }

    private static Map<String, Object> capacity(Map<String, Object> component) {
        return object(component.get("capacity"), component.get("id") + ".capacity");
    }

    private static void verifyInventoryCoverage(Map<String, Object> plan, Map<String, Object> inventory) {
        Set<String> inventoryContent = new LinkedHashSet<>();
        Set<String> inventorySources = new LinkedHashSet<>();
        for (Object rawSource : array(inventory.get("sources"), "inventory.sources")) {
            Map<String, Object> source = object(rawSource, "source");
            String sourceId = string(source.get("id"), "source.id");
            inventorySources.add(sourceId);
            for (Object rawContent : array(source.get("content"), "source.content")) {
                String contentId = string(object(rawContent, "content").get("id"), "content.id");
                check(inventoryContent.add(contentId), "duplicate inventory content id " + contentId);
            }
        }

        Set<String> decided = new LinkedHashSet<>();
        Map<String, String> dispositions = new LinkedHashMap<>();
        for (Object rawDecision : array(plan.get("contentDecisions"), "contentDecisions")) {
            Map<String, Object> decision = object(rawDecision, "contentDecision");
            String sourceId = string(decision.get("sourceId"), "contentDecision.sourceId");
            String contentId = string(decision.get("contentId"), "contentDecision.contentId");
            check(inventorySources.contains(sourceId), "decision uses unknown source " + sourceId);
            check(decided.add(contentId), "content decided more than once: " + contentId);
            dispositions.put(contentId, string(decision.get("disposition"), "contentDecision.disposition"));
        }
        eq(decided, inventoryContent, "every and only inventoried content item must have one decision");

        Map<String, Set<String>> references = new LinkedHashMap<>();
        references.put("carry", new LinkedHashSet<>());
        references.put("recreate", new LinkedHashSet<>());
        references.put("retain-temporarily", new LinkedHashSet<>());
        references.put("abandon", new LinkedHashSet<>());
        for (Object rawStep : array(plan.get("steps"), "steps")) {
            Map<String, Object> step = object(rawStep, "step");
            addUnique(references.get("carry"), strings(step.get("carriedContentIds"), "carriedContentIds"),
                    "carried content");
            addUnique(references.get("recreate"), strings(step.get("recreatedContentIds"), "recreatedContentIds"),
                    "recreated content");
            addUnique(references.get("retain-temporarily"),
                    strings(step.get("retainedTemporarilyContentIds"), "retainedTemporarilyContentIds"),
                    "temporarily retained content");
            addUnique(references.get("abandon"), strings(step.get("abandonedContentIds"), "abandonedContentIds"),
                    "abandoned content");
        }
        Set<String> referenced = new LinkedHashSet<>();
        for (Map.Entry<String, Set<String>> group : references.entrySet()) {
            for (String id : group.getValue()) {
                check(referenced.add(id), "content has conflicting step dispositions: " + id);
            }
        }
        for (Map.Entry<String, String> decision : dispositions.entrySet()) {
            String id = decision.getKey();
            String disposition = decision.getValue();
            check(references.get(disposition).contains(id),
                    id + " must be referenced by a step as " + disposition);
        }
        eq(referenced, inventoryContent, "step content references");
    }

    private static void verifyGatesAndSteps(Map<String, Object> plan, Map<String, Object> snapshot) {
        List<Object> expectedGates = array(snapshot.get("requiredGates"), "requiredGates");
        List<Object> actualGates = array(plan.get("gates"), "gates");
        eq(actualGates.size(), expectedGates.size(), "gate count");
        Map<String, Map<String, Object>> gatesById = new LinkedHashMap<>();
        for (Object raw : actualGates) {
            Map<String, Object> gate = object(raw, "gate");
            String id = string(gate.get("id"), "gate.id");
            check(gatesById.put(id, gate) == null, "duplicate gate " + id);
        }
        for (Object raw : expectedGates) {
            Map<String, Object> expected = object(raw, "expected gate");
            String id = string(expected.get("id"), "expected gate.id");
            Map<String, Object> actual = gatesById.get(id);
            check(actual != null, "missing gate " + id);
            eq(actual.get("type"), expected.get("type"), id + ".type");
        }

        List<Object> expectedSteps = array(snapshot.get("requiredStepSequence"), "requiredStepSequence");
        List<Object> actualSteps = array(plan.get("steps"), "steps");
        eq(actualSteps.size(), expectedSteps.size(), "step count");
        Set<String> prior = new LinkedHashSet<>();
        for (int i = 0; i < expectedSteps.size(); i++) {
            Map<String, Object> expected = object(expectedSteps.get(i), "expected step");
            Map<String, Object> actual = object(actualSteps.get(i), "step");
            eq(actual.get("order"), Long.valueOf(i + 1L), "step order " + (i + 1));
            eq(actual.get("id"), expected.get("id"), "step id at order " + (i + 1));

            Map<String, Object> source = object(actual.get("source"), "step.source");
            eq(source.get("id"), expected.get("sourceId"), actual.get("id") + ".source.id");
            eq(source.get("product"), expected.get("sourceProduct"), actual.get("id") + ".source.product");
            eq(source.get("version"), expected.get("sourceVersion"), actual.get("id") + ".source.version");
            Map<String, Object> target = object(actual.get("target"), "step.target");
            eq(target.get("product"), expected.get("targetComponent"), actual.get("id") + ".target.product");
            eq(target.get("version"), expected.get("targetVersion"), actual.get("id") + ".target.version");
            eq(actual.get("dependsOn"), expected.get("dependsOn"), actual.get("id") + ".dependsOn");
            eq(actual.get("gates"), expected.get("requiredGates"), actual.get("id") + ".gates");

            for (String dependency : strings(actual.get("dependsOn"), "dependsOn")) {
                check(prior.contains(dependency), actual.get("id") + " dependency is not earlier: " + dependency);
            }
            for (String gate : strings(actual.get("gates"), "step.gates")) {
                check(gatesById.containsKey(gate), actual.get("id") + " references unknown gate " + gate);
            }
            prior.add(string(actual.get("id"), "step.id"));
        }
    }

    private static void assertSiteCompute(Map<String, Object> inventory, String siteId,
            String compute, String label) {
        Map<String, Object> management = object(inventory.get("managementDomain"), "inventory.managementDomain");
        for (Object raw : array(management.get("sites"), "managementDomain.sites")) {
            Map<String, Object> site = object(raw, "site");
            if (Objects.equals(site.get("id"), siteId)) {
                eq(site.get("compute"), compute, label + " compute");
                return;
            }
        }
        fail(label + " uses unknown site " + siteId);
    }

    private static Map<String, Object> sourceById(Map<String, Object> inventory, String id) {
        for (Object raw : array(inventory.get("sources"), "inventory.sources")) {
            Map<String, Object> source = object(raw, "source");
            if (Objects.equals(source.get("id"), id)) return source;
        }
        throw new AssertionError("missing source " + id);
    }

    private static Map<String, Object> componentById(List<Object> components, String id) {
        for (Object raw : components) {
            Map<String, Object> component = object(raw, "component");
            if (Objects.equals(component.get("id"), id)) return component;
        }
        throw new AssertionError("missing component " + id);
    }

    private static void addUnique(Set<String> destination, List<String> values, String label) {
        for (String value : values) check(destination.add(value), label + " referenced more than once: " + value);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?>)) fail(label + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        if (!(value instanceof List<?>)) fail(label + " must be an array");
        return (List<Object>) value;
    }

    private static List<String> strings(Object value, String label) {
        List<String> result = new ArrayList<>();
        for (Object item : array(value, label)) result.add(string(item, label + " item"));
        return result;
    }

    private static String string(Object value, String label) {
        if (!(value instanceof String text)) throw new AssertionError(label + " must be a string");
        return text;
    }

    private static long integer(Object value, String label) {
        if (!(value instanceof Long number)) throw new AssertionError(label + " must be an integer");
        return number;
    }

    private static void eq(Object actual, Object expected, String label) {
        if (!Objects.equals(actual, expected)) {
            throw new AssertionError(label + ": expected " + expected + " but was " + actual);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    private static final class Schema {
        private Schema() {}

        static void validate(Object instance, Map<String, Object> rootSchema) {
            validateAt(instance, rootSchema, rootSchema, "$");
        }

        private static void validateAt(Object instance, Map<String, Object> schema,
                Map<String, Object> rootSchema, String path) {
            if (schema.containsKey("$ref")) {
                validateAt(instance, resolve(rootSchema, string(schema.get("$ref"), path + ".$ref")), rootSchema, path);
                return;
            }
            if (schema.containsKey("const")) {
                check(Objects.equals(instance, schema.get("const")), path + " must equal " + schema.get("const"));
            }
            if (schema.containsKey("enum")) {
                check(array(schema.get("enum"), path + ".enum").contains(instance), path + " is not in enum");
            }
            if (schema.containsKey("type")) {
                String type = string(schema.get("type"), path + ".type");
                check(matchesType(instance, type), path + " must be " + type);
            }

            if (instance instanceof Map<?, ?> rawMap) {
                @SuppressWarnings("unchecked")
                Map<String, Object> map = (Map<String, Object>) rawMap;
                if (schema.containsKey("minProperties")) {
                    check(map.size() >= integer(schema.get("minProperties"), path + ".minProperties"),
                            path + " has too few properties");
                }
                if (schema.containsKey("required")) {
                    for (String required : strings(schema.get("required"), path + ".required")) {
                        check(map.containsKey(required), path + " missing required property " + required);
                    }
                }
                Map<String, Object> properties = schema.containsKey("properties")
                        ? object(schema.get("properties"), path + ".properties") : Map.of();
                Object additional = schema.get("additionalProperties");
                for (Map.Entry<String, Object> entry : map.entrySet()) {
                    if (properties.containsKey(entry.getKey())) {
                        validateAt(entry.getValue(), object(properties.get(entry.getKey()), path + ".properties." + entry.getKey()),
                                rootSchema, path + "." + entry.getKey());
                    } else if (Boolean.FALSE.equals(additional)) {
                        fail(path + " has unexpected property " + entry.getKey());
                    } else if (additional instanceof Map<?, ?>) {
                        validateAt(entry.getValue(), object(additional, path + ".additionalProperties"), rootSchema,
                                path + "." + entry.getKey());
                    }
                }
            }

            if (instance instanceof List<?> list) {
                if (schema.containsKey("minItems")) {
                    check(list.size() >= integer(schema.get("minItems"), path + ".minItems"), path + " has too few items");
                }
                if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                    check(new HashSet<>(list).size() == list.size(), path + " items must be unique");
                }
                if (schema.containsKey("items")) {
                    Map<String, Object> itemSchema = object(schema.get("items"), path + ".items");
                    for (int i = 0; i < list.size(); i++) validateAt(list.get(i), itemSchema, rootSchema, path + "[" + i + "]");
                }
            }

            if (instance instanceof String text) {
                if (schema.containsKey("minLength")) {
                    check(text.length() >= integer(schema.get("minLength"), path + ".minLength"), path + " is too short");
                }
                if (schema.containsKey("pattern")) {
                    check(Pattern.compile(string(schema.get("pattern"), path + ".pattern")).matcher(text).find(),
                            path + " does not match its pattern");
                }
            }
            if (instance instanceof Long number && schema.containsKey("minimum")) {
                check(number >= integer(schema.get("minimum"), path + ".minimum"), path + " is below minimum");
            }
        }

        private static boolean matchesType(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "integer" -> value instanceof Long;
                case "number" -> value instanceof Number;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported schema type " + type);
            };
        }

        private static Map<String, Object> resolve(Map<String, Object> root, String ref) {
            check(ref.startsWith("#/"), "only local schema refs are supported: " + ref);
            Object current = root;
            for (String raw : ref.substring(2).split("/")) {
                String token = raw.replace("~1", "/").replace("~0", "~");
                current = object(current, "schema ref " + ref).get(token);
                check(current != null, "unresolved schema ref " + ref);
            }
            return object(current, "schema ref " + ref);
        }
    }

    private static final class Json {
        private final String text;
        private int index;

        private Json(String text) {
            this.text = Objects.requireNonNull(text, "JSON text");
        }

        static Object parse(String text) {
            Json parser = new Json(text);
            Object result = parser.value();
            parser.ws();
            if (parser.index != text.length()) parser.error("trailing content");
            return result;
        }

        private Object value() {
            ws();
            if (index >= text.length()) error("expected value");
            return switch (text.charAt(index)) {
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
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            expect('{');
            ws();
            if (take('}')) return result;
            while (true) {
                ws();
                String key = stringValue();
                ws();
                expect(':');
                check(!result.containsKey(key), "duplicate JSON key " + key);
                result.put(key, value());
                ws();
                if (take('}')) return result;
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            ArrayList<Object> result = new ArrayList<>();
            expect('[');
            ws();
            if (take(']')) return result;
            while (true) {
                result.add(value());
                ws();
                if (take(']')) return result;
                expect(',');
            }
        }

        private String stringValue() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < text.length()) {
                char c = text.charAt(index++);
                if (c == '"') return result.toString();
                if (c == '\\') {
                    if (index >= text.length()) error("unfinished escape");
                    char escape = text.charAt(index++);
                    switch (escape) {
                        case '"', '\\', '/' -> result.append(escape);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> {
                            if (index + 4 > text.length()) error("short unicode escape");
                            try {
                                result.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                            } catch (NumberFormatException ex) {
                                error("bad unicode escape");
                            }
                            index += 4;
                        }
                        default -> error("bad escape " + escape);
                    }
                } else {
                    if (c < 0x20) error("control character in string");
                    result.append(c);
                }
            }
            error("unterminated string");
            return null;
        }

        private Object numberValue() {
            int start = index;
            if (take('-')) {}
            digits();
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                digits();
            }
            if (take('e') || take('E')) {
                decimal = true;
                if (take('+') || take('-')) {}
                digits();
            }
            String number = text.substring(start, index);
            try {
                if (decimal) return Double.valueOf(number);
                return Long.valueOf(number);
            } catch (NumberFormatException ex) {
                error("bad number");
                return null;
            }
        }

        private void digits() {
            int start = index;
            while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
            if (start == index) error("expected digit");
        }

        private Object literal(String expected, Object value) {
            if (!text.startsWith(expected, index)) error("expected " + expected);
            index += expected.length();
            return value;
        }

        private void ws() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) index++;
        }

        private boolean take(char expected) {
            if (index < text.length() && text.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) error("expected '" + expected + "'");
        }

        private void error(String message) {
            throw new AssertionError("invalid JSON at offset " + index + ": " + message);
        }
    }
}
