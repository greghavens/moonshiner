import java.math.BigDecimal;
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
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Deterministic verifier for the VCF architecture artifact. */
public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");
    private static final Path ARCHITECTURE_SCHEMA = Path.of("architecture-schema.json");
    private static final Path INSTALLER_SPEC =
            Path.of("specifications", "vcf-installer", "vcf-installer-openapi.json");
    private static final String INSTALLER_SPEC_SHA256 =
            "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d";
    private static final String INVENTORY_SHA256 =
            "e9cf81039a3c09b95649876532f639d32245dd30b9ba4c2930da1d452018e525";
    private static final String SNAPSHOT_SHA256 =
            "7db5775aef0fa79c5a7b5ccce7ff9e115f8a11b3532db6c5d06388ebee9ea149";
    private static final String ARCHITECTURE_SCHEMA_SHA256 =
            "aa355a77ad5756221ba11e259aeee93189263b4482e3dd3c6a11b7537bc32cb7";
    private static final Pattern RESEARCH_ENTRY = Pattern.compile(
            "(?m)^- Title: (\\S(?:.*\\S)?)\\R"
                    + "  URL: (https://\\S+)\\R"
                    + "  Accessed: (\\d{4}-\\d{2}-\\d{2})\\R"
                    + "  Decision: (\\S(?:.*\\S)?)$");

    private TestMain() {}

    public static void main(String[] args) throws Exception {
        Path outputDir = Files.createTempDirectory("vcfarch-0131-");
        Path architecturePath = outputDir.resolve("architecture.json");
        Path researchPath = outputDir.resolve("research.md");

        ArchitectureClient.main(new String[] {
            INVENTORY.toString(), SNAPSHOT.toString(), architecturePath.toString(), researchPath.toString()
        });

        verify(architecturePath, researchPath);
        System.out.println(
                "PASS: installer SddcSpec, architecture schema, research audit, estate, and pinned compatibility plan");
    }

    private static void verify(Path architecturePath, Path researchPath) throws Exception {
        // The first validation uses only the produced artifact and the installer document.
        Map<String, Object> artifact = object(Json.parse(Files.readString(architecturePath)), "$artifact");
        Map<String, Object> openApi = object(Json.parse(Files.readString(INSTALLER_SPEC)), "$openapi");
        Object greenfieldSpec = require(artifact, "greenfieldSpec", "$artifact");
        Object sddcSchema = pointer(openApi, "#/components/schemas/SddcSpec");
        new SchemaValidator(openApi).validate(sddcSchema, greenfieldSpec, "$.greenfieldSpec");

        // Only after SddcSpec succeeds may any other authority be checked or loaded.
        check(INSTALLER_SPEC_SHA256.equals(sha256(INSTALLER_SPEC)),
                "the pinned VCF Installer OpenAPI document was modified");
        check(INVENTORY_SHA256.equals(sha256(INVENTORY)), "the estate inventory fixture was modified");
        check(SNAPSHOT_SHA256.equals(sha256(SNAPSHOT)),
                "the pinned compatibility snapshot was modified");
        check(ARCHITECTURE_SCHEMA_SHA256.equals(sha256(ARCHITECTURE_SCHEMA)),
                "the architecture artifact schema was modified");
        Map<String, Object> architectureSchema =
                object(Json.parse(Files.readString(ARCHITECTURE_SCHEMA)), "$architectureSchema");
        new SchemaValidator(architectureSchema).validate(architectureSchema, artifact, "$artifact");

        Map<String, Object> inventory = object(Json.parse(Files.readString(INVENTORY)), "$inventory");
        Map<String, Object> snapshot = object(Json.parse(Files.readString(SNAPSHOT)), "$snapshot");
        verifyGreenfield(artifact, inventory);
        verifyPlan(artifact, inventory, snapshot);
        verifyResearch(researchPath);
    }

    private static void verifyResearch(Path researchPath) throws Exception {
        check(Files.isRegularFile(researchPath), "research.md was not produced");
        String research = Files.readString(researchPath, StandardCharsets.UTF_8);
        check(!research.isBlank(), "research.md is empty");

        Matcher entries = RESEARCH_ENTRY.matcher(research);
        Set<String> urls = new LinkedHashSet<>();
        StringBuilder decisions = new StringBuilder();
        int entryCount = 0;
        while (entries.find()) {
            entryCount++;
            String url = entries.group(2);
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException error) {
                throw new AssertionError("research URL is invalid: " + url, error);
            }
            String host = uri.getHost();
            check("https".equalsIgnoreCase(uri.getScheme()),
                    "research URL must use HTTPS: " + url);
            check(host != null
                            && (host.equalsIgnoreCase("broadcom.com")
                                    || host.toLowerCase().endsWith(".broadcom.com")),
                    "research URL must be Broadcom-published: " + url);
            check(urls.add(url), "research.md contains duplicate URL " + url);
            try {
                LocalDate.parse(entries.group(3));
            } catch (DateTimeParseException error) {
                throw new AssertionError(
                        "research access date is not a valid YYYY-MM-DD date: " + entries.group(3),
                        error);
            }
            decisions.append(' ').append(entries.group(4).toLowerCase());
        }
        check(entryCount >= 2, "research.md must record the Broadcom pages consulted");

        String decisionText = decisions.toString();
        check(decisionText.contains("interoperab") || decisionText.contains("compatib"),
                "research decisions must cover compatibility or interoperability");
        check((decisionText.contains("newer") || decisionText.contains("back-in-time"))
                        && decisionText.contains("9.0.2") && decisionText.contains("9.1"),
                "research decisions must explain the requested-bundle forward-build choice");
        check(decisionText.contains("9.0.4")
                        && decisionText.contains("9.1")
                        && (decisionText.contains("recovery") || decisionText.contains("replication")),
                "research decisions must cover recovery-appliance convergence through 9.0.4");
    }

    private static void verifyGreenfield(
            Map<String, Object> artifact, Map<String, Object> inventory) {
        Map<String, Object> input = object(require(inventory, "greenfieldDesign", "$inventory"),
                "$inventory.greenfieldDesign");
        Map<String, Object> spec = object(require(artifact, "greenfieldSpec", "$artifact"),
                "$artifact.greenfieldSpec");

        equal(spec.get("sddcId"), input.get("sddcId"), "greenfield sddcId");
        equal(spec.get("workflowType"), input.get("workflowType"), "greenfield workflowType");
        equal(spec.get("version"), input.get("version"), "greenfield version");
        equal(spec.get("managementPoolName"), input.get("managementPoolName"),
                "greenfield managementPoolName");

        Map<String, Object> vcenter = object(require(spec, "vcenterSpec", "$.greenfieldSpec"),
                "$.greenfieldSpec.vcenterSpec");
        equal(vcenter.get("vcenterHostname"), input.get("vcenterHostname"), "vCenter hostname");
        equal(vcenter.get("rootVcenterPassword"), input.get("rootVcenterPassword"),
                "vCenter root password fixture value");
        equal(vcenter.get("useExistingDeployment"), Boolean.FALSE,
                "greenfield vCenter must be a new deployment");

        Map<String, Object> dns = object(require(spec, "dnsSpec", "$.greenfieldSpec"),
                "$.greenfieldSpec.dnsSpec");
        equal(dns.get("subdomain"), input.get("dnsSubdomain"), "DNS subdomain");
        equal(dns.get("nameservers"), input.get("nameservers"), "DNS nameservers");
        equal(spec.get("ntpServers"), input.get("ntpServers"), "NTP servers");

        List<String> expectedHosts = strings(require(input, "hostnames", "$greenfieldInput"),
                "$greenfieldInput.hostnames");
        List<Object> hostSpecs = list(require(spec, "hostSpecs", "$.greenfieldSpec"),
                "$.greenfieldSpec.hostSpecs");
        List<String> actualHosts = new ArrayList<>();
        for (int i = 0; i < hostSpecs.size(); i++) {
            actualHosts.add(string(require(object(hostSpecs.get(i), "hostSpec"), "hostname", "hostSpec"),
                    "hostSpec.hostname"));
        }
        equal(actualHosts, expectedHosts, "greenfield host inventory");

        List<Object> expectedNetworks = list(require(input, "networks", "$greenfieldInput"),
                "$greenfieldInput.networks");
        equal(spec.get("networkSpecs"), expectedNetworks, "greenfield network specifications");

        Map<String, Object> cluster = object(require(spec, "clusterSpec", "$.greenfieldSpec"),
                "$.greenfieldSpec.clusterSpec");
        equal(cluster.get("datacenterName"), input.get("datacenterName"), "datacenter name");
        equal(cluster.get("clusterName"), input.get("clusterName"), "cluster name");

        Map<String, Object> datastore = object(require(spec, "datastoreSpec", "$.greenfieldSpec"),
                "$.greenfieldSpec.datastoreSpec");
        Map<String, Object> vsan = object(require(datastore, "vsanSpec", "datastoreSpec"),
                "datastoreSpec.vsanSpec");
        equal(vsan.get("datastoreName"), input.get("datastoreName"), "vSAN datastore name");
        equal(vsan.get("failuresToTolerate"), input.get("failuresToTolerate"),
                "vSAN failuresToTolerate");

        Map<String, Object> manager = object(require(spec, "sddcManagerSpec", "$.greenfieldSpec"),
                "$.greenfieldSpec.sddcManagerSpec");
        equal(manager.get("hostname"), input.get("sddcManagerHostname"), "SDDC Manager hostname");
        equal(manager.get("useExistingDeployment"), Boolean.FALSE,
                "greenfield SDDC Manager must be a new deployment");

        Map<String, Object> nsx = object(require(spec, "nsxtSpec", "$.greenfieldSpec"),
                "$.greenfieldSpec.nsxtSpec");
        equal(nsx.get("vipFqdn"), input.get("nsxVipFqdn"), "NSX VIP FQDN");
        equal(nsx.get("transportVlanId"), input.get("transportVlanId"), "NSX transport VLAN");
        equal(nsx.get("useExistingDeployment"), Boolean.FALSE,
                "greenfield NSX must be a new deployment");
        List<String> expectedManagers = strings(input.get("nsxManagerHostnames"), "NSX managers");
        List<Object> managerSpecs = list(require(nsx, "nsxtManagers", "nsxtSpec"),
                "nsxtSpec.nsxtManagers");
        List<String> actualManagers = new ArrayList<>();
        for (Object value : managerSpecs) {
            actualManagers.add(string(require(object(value, "NSX manager"), "hostname", "NSX manager"),
                    "NSX manager hostname"));
        }
        equal(actualManagers, expectedManagers, "NSX manager hostnames");
    }

    private static void verifyPlan(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> plan = object(require(artifact, "migrationPlan", "$artifact"),
                "$artifact.migrationPlan");
        equal(plan.get("estateId"), inventory.get("estateId"), "estateId");
        equal(plan.get("fleetId"), inventory.get("fleetId"), "fleetId");
        equal(artifact.get("selectedBundle"), snapshot.get("selectedBundle"), "selected VCF bundle");

        Map<String, Object> rejection = object(require(artifact, "rejectedBundle", "$artifact"),
                "$artifact.rejectedBundle");
        List<Object> rejected = list(require(snapshot, "rejectedBundles", "$snapshot"),
                "$snapshot.rejectedBundles");
        Map<String, Object> expectedRejection = null;
        for (Object item : rejected) {
            Map<String, Object> candidate = object(item, "rejected bundle");
            if (Objects.equals(candidate.get("bundle"), inventory.get("requestedBundle"))) {
                expectedRejection = candidate;
            }
        }
        check(expectedRejection != null, "requested bundle is not classified by snapshot");
        equal(rejection, expectedRejection, "requested bundle rejection");

        Map<String, Map<String, Object>> inventoryById = index(
                list(require(inventory, "components", "$inventory"), "$inventory.components"),
                "componentId", "inventory components");
        Map<String, Map<String, Object>> compatibilityById = index(
                list(require(snapshot, "componentCompatibility", "$snapshot"),
                        "$snapshot.componentCompatibility"),
                "componentId", "component compatibility");
        equal(inventoryById.keySet(), compatibilityById.keySet(),
                "snapshot component coverage of inventory");

        List<Object> componentRecords = list(require(plan, "components", "$plan"),
                "$plan.components");
        Map<String, Map<String, Object>> outputById = index(componentRecords, "componentId",
                "architecture component records");
        equal(outputById.keySet(), inventoryById.keySet(),
                "architecture component coverage of inventory");

        for (String componentId : inventoryById.keySet()) {
            Map<String, Object> source = inventoryById.get(componentId);
            Map<String, Object> compatibility = compatibilityById.get(componentId);
            Map<String, Object> actual = outputById.get(componentId);
            equal(actual.get("product"), source.get("product"), componentId + " product");
            equal(actual.get("currentVersion"), source.get("version"), componentId + " current version");
            equal(actual.get("targetVersion"), compatibility.get("targetVersion"),
                    componentId + " target version");
            equal(asSet(actual.get("gateIds"), componentId + " gateIds"),
                    asSet(compatibility.get("gateIds"), componentId + " snapshot gateIds"),
                    componentId + " gates");
            Set<String> expectedTransitionIds = new LinkedHashSet<>();
            for (Object edgeValue : list(compatibility.get("supportedPath"), "supportedPath")) {
                expectedTransitionIds.add(string(object(edgeValue, "path edge").get("transitionId"),
                        "transitionId"));
            }
            equal(asSet(actual.get("transitionIds"), componentId + " transitionIds"),
                    expectedTransitionIds, componentId + " transition IDs");
        }

        Map<String, Map<String, Object>> expectedGates = index(
                list(require(snapshot, "gateCatalog", "$snapshot"), "$snapshot.gateCatalog"),
                "gateId", "snapshot gates");
        Map<String, Map<String, Object>> actualGates = index(
                list(require(plan, "gates", "$plan"), "$plan.gates"),
                "gateId", "architecture gates");
        equal(actualGates, expectedGates, "migration gate catalog");

        Map<String, Map<String, Object>> expectedTransitions = new LinkedHashMap<>();
        Map<String, Object> estateTransition = object(
                require(snapshot, "estateTransition", "$snapshot"), "$snapshot.estateTransition");
        putUnique(expectedTransitions, estateTransition, "transitionId", "estate transition");
        for (Map<String, Object> compatibility : compatibilityById.values()) {
            for (Object value : list(compatibility.get("supportedPath"), "supportedPath")) {
                Map<String, Object> edge = object(value, "supported path edge");
                Map<String, Object> expanded = new LinkedHashMap<>(edge);
                String componentId = string(compatibility.get("componentId"), "componentId");
                expanded.put("componentId", componentId);
                expanded.put("product", inventoryById.get(componentId).get("product"));
                putUnique(expectedTransitions, expanded, "transitionId", "supported transition");
            }
        }

        Map<String, Set<String>> requiredDependencies = new LinkedHashMap<>();
        for (String transitionId : expectedTransitions.keySet()) {
            requiredDependencies.put(transitionId, new LinkedHashSet<>());
        }
        for (Object value : list(require(snapshot, "precedence", "$snapshot"), "$snapshot.precedence")) {
            Map<String, Object> rule = object(value, "precedence rule");
            String before = string(rule.get("before"), "precedence before");
            String after = string(rule.get("after"), "precedence after");
            check(expectedTransitions.containsKey(before), "unknown precedence predecessor " + before);
            check(expectedTransitions.containsKey(after), "unknown precedence successor " + after);
            requiredDependencies.get(after).add(before);
        }

        List<Object> steps = list(require(plan, "steps", "$plan"), "$plan.steps");
        check(steps.size() == expectedTransitions.size(),
                "plan must contain every supported transition exactly once");
        Map<String, Integer> orderByTransition = new LinkedHashMap<>();
        Set<String> observed = new LinkedHashSet<>();
        for (int i = 0; i < steps.size(); i++) {
            Map<String, Object> step = object(steps.get(i), "migration step " + (i + 1));
            int order = integer(step.get("order"), "step order");
            check(order == i + 1, "step order must be contiguous and match array order");
            String transitionId = string(step.get("transitionId"), "step transitionId");
            check(observed.add(transitionId), "duplicate transition " + transitionId);
            Map<String, Object> expected = expectedTransitions.get(transitionId);
            check(expected != null, "unsupported transition " + transitionId);
            for (String field : List.of(
                    "componentId", "product", "fromVersion", "toVersion", "action")) {
                equal(step.get(field), expected.get(field), transitionId + " " + field);
            }
            equal(asSet(step.get("gateIds"), transitionId + " gateIds"),
                    asSet(expected.get("gateIds"), transitionId + " expected gateIds"),
                    transitionId + " transition gates");
            Set<String> actualDependencies = asSet(step.get("dependsOn"), transitionId + " dependsOn");
            equal(actualDependencies, requiredDependencies.get(transitionId),
                    transitionId + " dependencies");
            for (String predecessor : actualDependencies) {
                check(orderByTransition.containsKey(predecessor),
                        transitionId + " occurs before dependency " + predecessor);
            }
            orderByTransition.put(transitionId, order);
        }
        equal(observed, expectedTransitions.keySet(), "migration transition coverage");

        for (Object value : list(require(snapshot, "blockedTransitions", "$snapshot"),
                "$snapshot.blockedTransitions")) {
            Map<String, Object> blocked = object(value, "blocked transition");
            for (Object stepValue : steps) {
                Map<String, Object> step = object(stepValue, "migration step");
                boolean same = Objects.equals(step.get("componentId"), blocked.get("componentId"))
                        && Objects.equals(step.get("fromVersion"), blocked.get("fromVersion"))
                        && Objects.equals(step.get("toVersion"), blocked.get("toVersion"));
                check(!same, "plan contains blocked transition for " + blocked.get("componentId"));
            }
        }

        List<String> finalMembers = strings(require(plan, "finalFleetMembers", "$plan"),
                "$plan.finalFleetMembers");
        check(finalMembers.size() == new LinkedHashSet<>(finalMembers).size(),
                "finalFleetMembers contains duplicates");
        equal(new LinkedHashSet<>(finalMembers), inventoryById.keySet(),
                "final fleet membership");
    }

    private static Map<String, Map<String, Object>> index(
            List<Object> values, String key, String label) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, label + " item");
            putUnique(result, item, key, label);
        }
        return result;
    }

    private static void putUnique(
            Map<String, Map<String, Object>> target,
            Map<String, Object> item,
            String key,
            String label) {
        String id = string(require(item, key, label), label + "." + key);
        check(target.put(id, item) == null, "duplicate " + label + " key " + id);
    }

    private static String sha256(Path path) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));
        StringBuilder out = new StringBuilder();
        for (byte value : digest) {
            out.append(String.format("%02x", value & 0xff));
        }
        return out.toString();
    }

    private static Object pointer(Object root, String ref) {
        check(ref.startsWith("#/"), "only local JSON pointers are supported: " + ref);
        Object current = root;
        for (String raw : ref.substring(2).split("/")) {
            String token = raw.replace("~1", "/").replace("~0", "~");
            current = require(object(current, "JSON pointer " + ref), token, "JSON pointer " + ref);
        }
        return current;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        check(value instanceof Map<?, ?>, label + " must be an object");
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> list(Object value, String label) {
        check(value instanceof List<?>, label + " must be an array");
        return (List<Object>) value;
    }

    private static List<String> strings(Object value, String label) {
        List<String> result = new ArrayList<>();
        for (Object item : list(value, label)) {
            result.add(string(item, label + " item"));
        }
        return result;
    }

    private static Set<String> asSet(Object value, String label) {
        List<String> strings = strings(value, label);
        Set<String> result = new LinkedHashSet<>(strings);
        check(result.size() == strings.size(), label + " contains duplicates");
        return result;
    }

    private static String string(Object value, String label) {
        check(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static int integer(Object value, String label) {
        check(value instanceof BigDecimal, label + " must be an integer");
        try {
            return ((BigDecimal) value).intValueExact();
        } catch (ArithmeticException error) {
            throw new AssertionError(label + " must be an exact integer", error);
        }
    }

    private static Object require(Map<String, Object> object, String key, String label) {
        check(object.containsKey(key), label + " is missing " + key);
        return object.get(key);
    }

    private static void equal(Object actual, Object expected, String label) {
        check(Objects.equals(actual, expected),
                label + " mismatch; expected " + expected + " but got " + actual);
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class SchemaValidator {
        private final Object root;

        SchemaValidator(Object root) {
            this.root = root;
        }

        void validate(Object schemaValue, Object instance, String path) {
            if (Boolean.TRUE.equals(schemaValue)) {
                return;
            }
            if (Boolean.FALSE.equals(schemaValue)) {
                throw new AssertionError(path + " is rejected by schema");
            }
            Map<String, Object> schema = object(schemaValue, "schema at " + path);
            if (schema.containsKey("$ref")) {
                validate(pointer(root, string(schema.get("$ref"), "$ref")), instance, path);
                return;
            }
            if (Boolean.TRUE.equals(schema.get("nullable")) && instance == null) {
                return;
            }
            if (schema.containsKey("allOf")) {
                for (Object branch : list(schema.get("allOf"), path + " allOf")) {
                    validate(branch, instance, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                checkBranchCount(schema.get("anyOf"), instance, path, false);
            }
            if (schema.containsKey("oneOf")) {
                checkBranchCount(schema.get("oneOf"), instance, path, true);
            }
            if (schema.containsKey("const")) {
                check(Objects.equals(instance, schema.get("const")), path + " violates const");
            }
            if (schema.containsKey("enum")) {
                check(list(schema.get("enum"), path + " enum").contains(instance),
                        path + " is not an allowed enum value");
            }

            if (schema.containsKey("type")) {
                check(matchesType(instance, schema.get("type")),
                        path + " has wrong type; expected " + schema.get("type"));
            }

            if (instance instanceof Map<?, ?>) {
                validateObject(schema, object(instance, path), path);
            } else if (instance instanceof List<?>) {
                validateArray(schema, list(instance, path), path);
            } else if (instance instanceof String) {
                validateString(schema, (String) instance, path);
            } else if (instance instanceof BigDecimal) {
                validateNumber(schema, (BigDecimal) instance, path);
            }
        }

        private void checkBranchCount(Object branchesValue, Object instance, String path, boolean exactlyOne) {
            int matches = 0;
            for (Object branch : list(branchesValue, path + " alternatives")) {
                try {
                    validate(branch, instance, path);
                    matches++;
                } catch (AssertionError ignored) {
                    // A nonmatching branch is expected while evaluating alternatives.
                }
            }
            check(exactlyOne ? matches == 1 : matches >= 1,
                    path + (exactlyOne ? " must match exactly one schema" : " must match a schema"));
        }

        private boolean matchesType(Object value, Object typeValue) {
            if (typeValue instanceof List<?>) {
                for (Object type : list(typeValue, "schema type")) {
                    if (matchesType(value, type)) {
                        return true;
                    }
                }
                return false;
            }
            String type = string(typeValue, "schema type");
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "integer" -> value instanceof BigDecimal
                        && ((BigDecimal) value).stripTrailingZeros().scale() <= 0;
                case "number" -> value instanceof BigDecimal;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported schema type " + type);
            };
        }

        private void validateObject(
                Map<String, Object> schema, Map<String, Object> instance, String path) {
            if (schema.containsKey("required")) {
                for (Object required : list(schema.get("required"), path + " required")) {
                    String key = string(required, "required property");
                    check(instance.containsKey(key), path + " is missing required property " + key);
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), path + " properties")
                    : Map.of();
            for (Map.Entry<String, Object> entry : instance.entrySet()) {
                if (properties.containsKey(entry.getKey())) {
                    validate(properties.get(entry.getKey()), entry.getValue(), path + "." + entry.getKey());
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    throw new AssertionError(path + " contains additional property " + entry.getKey());
                } else if (schema.get("additionalProperties") instanceof Map<?, ?>) {
                    validate(schema.get("additionalProperties"), entry.getValue(),
                            path + "." + entry.getKey());
                }
            }
            size(schema, "minProperties", "maxProperties", instance.size(), path + " properties");
        }

        private void validateArray(Map<String, Object> schema, List<Object> instance, String path) {
            if (schema.containsKey("items")) {
                for (int i = 0; i < instance.size(); i++) {
                    validate(schema.get("items"), instance.get(i), path + "[" + i + "]");
                }
            }
            size(schema, "minItems", "maxItems", instance.size(), path + " items");
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                check(new HashSet<>(instance).size() == instance.size(), path + " items must be unique");
            }
        }

        private void validateString(Map<String, Object> schema, String instance, String path) {
            int length = instance.codePointCount(0, instance.length());
            size(schema, "minLength", "maxLength", length, path + " length");
            if (schema.containsKey("pattern")) {
                String regex = string(schema.get("pattern"), path + " pattern");
                check(Pattern.compile(regex).matcher(instance).find(), path + " does not match pattern");
            }
        }

        private void validateNumber(Map<String, Object> schema, BigDecimal instance, String path) {
            if (schema.containsKey("minimum")) {
                check(instance.compareTo(decimal(schema.get("minimum"), "minimum")) >= 0,
                        path + " is below minimum");
            }
            if (schema.containsKey("maximum")) {
                check(instance.compareTo(decimal(schema.get("maximum"), "maximum")) <= 0,
                        path + " exceeds maximum");
            }
        }

        private void size(
                Map<String, Object> schema, String minimum, String maximum, int size, String path) {
            if (schema.containsKey(minimum)) {
                check(size >= integer(schema.get(minimum), minimum), path + " is below " + minimum);
            }
            if (schema.containsKey(maximum)) {
                check(size <= integer(schema.get(maximum), maximum), path + " exceeds " + maximum);
            }
        }

        private BigDecimal decimal(Object value, String label) {
            check(value instanceof BigDecimal, label + " must be numeric");
            return (BigDecimal) value;
        }
    }

    private static final class Json {
        static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.value();
            parser.space();
            check(parser.atEnd(), "trailing JSON content at offset " + parser.index);
            return value;
        }

        private static final class Parser {
            private final String text;
            private int index;

            Parser(String text) {
                this.text = text;
            }

            boolean atEnd() {
                return index == text.length();
            }

            void space() {
                while (!atEnd() && Character.isWhitespace(text.charAt(index))) {
                    index++;
                }
            }

            Object value() {
                space();
                check(!atEnd(), "unexpected end of JSON");
                char c = text.charAt(index);
                if (c == '{') return object();
                if (c == '[') return array();
                if (c == '"') return string();
                if (c == 't') return literal("true", Boolean.TRUE);
                if (c == 'f') return literal("false", Boolean.FALSE);
                if (c == 'n') return literal("null", null);
                if (c == '-' || Character.isDigit(c)) return number();
                throw new AssertionError("unexpected JSON token at offset " + index);
            }

            Map<String, Object> object() {
                expect('{');
                Map<String, Object> result = new LinkedHashMap<>();
                space();
                if (take('}')) return result;
                while (true) {
                    space();
                    String key = string();
                    expect(':');
                    check(!result.containsKey(key), "duplicate JSON key " + key);
                    result.put(key, value());
                    space();
                    if (take('}')) return result;
                    expect(',');
                }
            }

            List<Object> array() {
                expect('[');
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
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!atEnd()) {
                    char c = text.charAt(index++);
                    if (c == '"') return result.toString();
                    if (c == '\\') {
                        check(!atEnd(), "unterminated JSON escape");
                        char escape = text.charAt(index++);
                        switch (escape) {
                            case '"', '\\', '/' -> result.append(escape);
                            case 'b' -> result.append('\b');
                            case 'f' -> result.append('\f');
                            case 'n' -> result.append('\n');
                            case 'r' -> result.append('\r');
                            case 't' -> result.append('\t');
                            case 'u' -> {
                                check(index + 4 <= text.length(), "short unicode escape");
                                result.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                                index += 4;
                            }
                            default -> throw new AssertionError("bad JSON escape at offset " + index);
                        }
                    } else {
                        check(c >= 0x20, "control character in JSON string");
                        result.append(c);
                    }
                }
                throw new AssertionError("unterminated JSON string");
            }

            Object literal(String token, Object value) {
                check(text.startsWith(token, index), "invalid JSON token at offset " + index);
                index += token.length();
                return value;
            }

            BigDecimal number() {
                int start = index;
                if (text.charAt(index) == '-') index++;
                check(index < text.length(), "short JSON number");
                if (text.charAt(index) == '0') {
                    index++;
                } else {
                    check(Character.isDigit(text.charAt(index)), "invalid JSON number");
                    while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
                }
                if (index < text.length() && text.charAt(index) == '.') {
                    index++;
                    check(index < text.length() && Character.isDigit(text.charAt(index)),
                            "invalid JSON fraction");
                    while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
                }
                if (index < text.length() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                    index++;
                    if (index < text.length() && (text.charAt(index) == '+' || text.charAt(index) == '-')) index++;
                    check(index < text.length() && Character.isDigit(text.charAt(index)),
                            "invalid JSON exponent");
                    while (index < text.length() && Character.isDigit(text.charAt(index))) index++;
                }
                return new BigDecimal(text.substring(start, index));
            }

            boolean take(char c) {
                if (!atEnd() && text.charAt(index) == c) {
                    index++;
                    return true;
                }
                return false;
            }

            void expect(char c) {
                space();
                check(take(c), "expected '" + c + "' at offset " + index);
            }
        }
    }
}
