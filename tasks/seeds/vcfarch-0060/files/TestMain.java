import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.math.BigDecimal;
import java.net.URI;
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
import java.util.regex.Pattern;

/** Protected, dependency-free acceptance harness. */
public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        // Deliberate ordering: obtain only the greenfield artifact, then validate
        // it against the upstream installer schema before opening any grading
        // fixture or performing any architecture-specific assertion.
        String greenfieldText = captureMain("greenfield");
        Object greenfield = Json.parse(greenfieldText);
        Object installerDocument = Json.parse(Files.readString(
                Path.of("specifications/vcf-installer/vcf-installer-openapi.json")));
        Map<String, Object> installer = object(installerDocument, "installer document");
        Map<String, Object> sddcSchema = object(path(installer, "components", "schemas", "SddcSpec"),
                "SddcSpec schema");
        new SchemaValidator(installer).validate(greenfield, sddcSchema, "$greenfield");

        // Check the recorded live research without putting network access into
        // deterministic acceptance. The trace itself establishes consultation.
        String researchText = captureMain("research");
        checkResearch(object(Json.parse(researchText), "research record"));

        // Nothing below this line runs unless the installer schema accepted the artifact.
        Map<String, Object> requirements = object(Json.parse(Files.readString(
                Path.of("fixtures/greenfield-requirements.json"))), "requirements");
        checkGreenfield(object(greenfield, "greenfield artifact"), requirements);

        String inventoryText = Files.readString(Path.of("fixtures/estate-inventory.json"));
        String snapshotText = Files.readString(Path.of("fixtures/compatibility-snapshot.json"));
        Map<String, Object> inventory = object(Json.parse(inventoryText), "inventory");
        Map<String, Object> snapshot = object(Json.parse(snapshotText), "compatibility snapshot");

        String migrationText = captureMain("migration", "fixtures/estate-inventory.json",
                "fixtures/compatibility-snapshot.json");
        Object migration = Json.parse(migrationText);
        Map<String, Object> migrationSchema = object(Json.parse(Files.readString(
                Path.of("schemas/migration-plan-schema.json"))), "migration schema");
        new SchemaValidator(migrationSchema).validate(migration, migrationSchema, "$migration");
        checkMigration(object(migration, "migration plan"), inventory, snapshot);

        expectInvalidCli("unsupported");
        expectInvalidCli("greenfield", "unexpected");
        expectInvalidCli("migration", "fixtures/estate-inventory.json");

        System.out.println("all checks passed");
    }

    private static String captureMain(String... args) throws Exception {
        PrintStream original = System.out;
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        try (PrintStream captured = new PrintStream(bytes, true, StandardCharsets.UTF_8)) {
            System.setOut(captured);
            ArchitectureClient.main(args);
        } finally {
            System.setOut(original);
        }
        String output = bytes.toString(StandardCharsets.UTF_8).trim();
        require(!output.isEmpty(), "CLI emitted no JSON for " + args[0]);
        return output;
    }

    private static void expectInvalidCli(String... args) throws Exception {
        try {
            captureMain(args);
        } catch (IllegalArgumentException error) {
            require(error.getMessage() != null && !error.getMessage().isBlank(),
                    "invalid CLI arguments must produce a useful error");
            return;
        }
        throw new AssertionError("invalid CLI arguments were accepted");
    }

    private static void checkResearch(Map<String, Object> record) {
        List<Object> sources = array(record.get("sources"), "research sources");
        require(!sources.isEmpty(), "research sources must not be empty");
        for (Object value : sources) {
            Map<String, Object> source = object(value, "research source");
            String title = nonBlankString(source.get("title"), "research title");
            String url = nonBlankString(source.get("url"), "research URL");
            String consultedOn = nonBlankString(source.get("consultedOn"),
                    "research consultation date");
            nonBlankString(source.get("usedFor"), "research purpose");

            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException error) {
                throw new AssertionError(title + " has an invalid research URL", error);
            }
            require("https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null
                            && uri.getUserInfo() == null,
                    title + " must use an absolute HTTPS research URL");
            String host = uri.getHost().toLowerCase(java.util.Locale.ROOT);
            require(host.equals("broadcom.com") || host.endsWith(".broadcom.com")
                            || host.equals("vmware.com") || host.endsWith(".vmware.com"),
                    title + " is not a Broadcom-published VMware source");
            try {
                LocalDate.parse(consultedOn);
            } catch (DateTimeParseException error) {
                throw new AssertionError(title + " has a non-ISO consultation date", error);
            }
        }
    }

    private static String nonBlankString(Object value, String label) {
        String result = string(value, label);
        require(!result.isBlank(), label + " must not be blank");
        return result;
    }

    private static void checkGreenfield(Map<String, Object> spec, Map<String, Object> req) {
        equal(spec.get("sddcId"), req.get("designId"), "sddcId");
        equal(spec.get("vcfInstanceName"), req.get("vcfInstanceName"), "vcfInstanceName");
        equal(spec.get("version"), req.get("targetVersion"), "VCF version");
        equal(spec.get("workflowType"), req.get("workflowType"), "workflowType");
        equal(spec.get("ntpServers"), req.get("ntpServers"), "NTP servers");

        Map<String, Object> reqDns = object(req.get("dns"), "required DNS");
        Map<String, Object> dns = object(spec.get("dnsSpec"), "dnsSpec");
        equal(dns.get("subdomain"), reqDns.get("subdomain"), "DNS subdomain");
        equal(dns.get("nameservers"), reqDns.get("nameservers"), "DNS servers");

        Map<String, Object> appliances = object(req.get("appliances"), "appliances");
        Map<String, Object> vcenter = object(spec.get("vcenterSpec"), "vcenterSpec");
        equal(vcenter.get("vcenterHostname"), appliances.get("vcenterHostname"), "vCenter hostname");
        equal(vcenter.get("version"), req.get("targetVersion"), "vCenter version");
        equal(vcenter.get("useExistingDeployment"), Boolean.FALSE, "greenfield vCenter mode");

        Map<String, Object> manager = object(spec.get("sddcManagerSpec"), "sddcManagerSpec");
        equal(manager.get("hostname"), appliances.get("sddcManagerHostname"), "SDDC Manager hostname");
        equal(manager.get("version"), req.get("targetVersion"), "SDDC Manager version");
        equal(manager.get("useExistingDeployment"), Boolean.FALSE, "greenfield SDDC Manager mode");

        Map<String, Object> nsx = object(spec.get("nsxtSpec"), "nsxtSpec");
        equal(nsx.get("vipFqdn"), appliances.get("nsxVipFqdn"), "NSX VIP");
        equal(nsx.get("version"), req.get("targetVersion"), "NSX version");
        equal(nsx.get("useExistingDeployment"), Boolean.FALSE, "greenfield NSX mode");
        List<Object> expectedNsxNames = array(appliances.get("nsxManagerHostnames"), "required NSX managers");
        List<Object> nsxManagers = array(nsx.get("nsxtManagers"), "NSX managers");
        require(nsxManagers.size() == expectedNsxNames.size(), "wrong NSX manager count");
        Set<Object> actualNsxNames = new LinkedHashSet<>();
        for (Object value : nsxManagers) {
            actualNsxNames.add(object(value, "NSX manager").get("hostname"));
        }
        equal(actualNsxNames, new LinkedHashSet<>(expectedNsxNames), "NSX manager hostnames");

        Map<String, Object> requiredCluster = object(req.get("cluster"), "required cluster");
        Map<String, Object> cluster = object(spec.get("clusterSpec"), "clusterSpec");
        equal(cluster.get("datacenterName"), requiredCluster.get("datacenterName"), "datacenter name");
        equal(cluster.get("clusterName"), requiredCluster.get("clusterName"), "cluster name");
        List<Object> dvsSpecs = array(spec.get("dvsSpecs"), "dvsSpecs");
        require(dvsSpecs.size() == 1, "exactly one management DVS is required");
        Map<String, Object> dvs = object(dvsSpecs.get(0), "management DVS");
        equal(dvs.get("dvsName"), requiredCluster.get("dvsName"), "DVS name");
        Set<Object> dvsNetworks = new LinkedHashSet<>(array(dvs.get("networks"), "DVS networks"));
        equal(dvsNetworks, Set.of("MANAGEMENT", "VMOTION", "VSAN"), "DVS traffic types");
        require(array(dvs.get("vmnicsToUplinks"), "DVS uplinks").size() >= 2,
                "DVS requires at least two physical uplinks");

        Map<String, Object> datastore = object(spec.get("datastoreSpec"), "datastoreSpec");
        Map<String, Object> vsan = object(datastore.get("vsanSpec"), "vsanSpec");
        equal(vsan.get("datastoreName"), requiredCluster.get("datastoreName"), "vSAN datastore name");
        equal(vsan.get("failuresToTolerate"), requiredCluster.get("failuresToTolerate"),
                "vSAN failuresToTolerate");
        Map<String, Object> esa = object(vsan.get("esaConfig"), "vSAN ESA config");
        equal(esa.get("enabled"), requiredCluster.get("vsanEsaEnabled"), "vSAN ESA setting");

        Map<String, Map<String, Object>> requiredNetworks = keyedObjects(
                array(req.get("networks"), "required networks"), "networkType");
        Map<String, Map<String, Object>> actualNetworks = keyedObjects(
                array(spec.get("networkSpecs"), "networkSpecs"), "networkType");
        equal(actualNetworks.keySet(), requiredNetworks.keySet(), "network types");
        for (Map.Entry<String, Map<String, Object>> entry : requiredNetworks.entrySet()) {
            Map<String, Object> expected = entry.getValue();
            Map<String, Object> actual = actualNetworks.get(entry.getKey());
            for (String field : List.of("vlanId", "subnet", "gateway", "mtu")) {
                equal(actual.get(field), expected.get(field), entry.getKey() + " " + field);
            }
            List<Object> ranges = array(actual.get("includeIpAddressRanges"),
                    entry.getKey() + " address ranges");
            require(ranges.size() == 1, entry.getKey() + " must have one address range");
            Map<String, Object> range = object(ranges.get(0), entry.getKey() + " range");
            equal(range.get("startIpAddress"), expected.get("startIpAddress"), "range start");
            equal(range.get("endIpAddress"), expected.get("endIpAddress"), "range end");
        }

        List<Object> hostSpecs = array(spec.get("hostSpecs"), "hostSpecs");
        Set<String> specHosts = new LinkedHashSet<>();
        for (Object value : hostSpecs) {
            String hostname = string(object(value, "host spec").get("hostname"), "host hostname");
            require(specHosts.add(hostname), "duplicate management host " + hostname);
        }

        Map<String, Object> architecture = object(spec.get("architecture"), "architecture extension");
        equal(architecture.get("deploymentModel"), "STRETCHED_MANAGEMENT_DOMAIN", "deployment model");
        equal(architecture.get("siteFailuresToTolerate"), number(1), "site failure tolerance");
        List<Object> requiredSites = array(req.get("dataSites"), "required data sites");
        List<Object> actualSites = array(architecture.get("dataSites"), "architecture data sites");
        require(actualSites.size() == 2, "stretched management domain requires exactly two data sites");
        Map<String, Map<String, Object>> sitesById = keyedObjects(actualSites, "siteId");
        Set<String> fixtureHosts = new LinkedHashSet<>();
        Map<String, Object> hostProfile = object(req.get("hostProfile"), "host profile");
        Map<String, Object> requiredCapacity = object(req.get("requiredSurvivingCapacity"),
                "required surviving capacity");
        for (Object value : requiredSites) {
            Map<String, Object> expected = object(value, "required site");
            String siteId = string(expected.get("siteId"), "required site id");
            Map<String, Object> actual = sitesById.get(siteId);
            require(actual != null, "missing data site " + siteId);
            equal(actual.get("faultDomain"), expected.get("faultDomain"), siteId + " fault domain");
            equal(actual.get("preferred"), expected.get("preferred"), siteId + " preferred flag");
            List<Object> hosts = array(actual.get("hosts"), siteId + " hosts");
            equal(hosts, expected.get("hosts"), siteId + " host placement");
            require(hosts.size() == 4, siteId + " must contain four management hosts");
            for (Object host : hosts) {
                require(fixtureHosts.add(string(host, "site host")), "host appears in both sites: " + host);
            }
            assertCapacity(siteId, hosts.size(), hostProfile, requiredCapacity);
        }
        equal(specHosts, fixtureHosts, "SddcSpec hosts versus stretched-site placement");
        require(specHosts.size() == 8, "management domain must have eight data hosts");

        Map<String, Object> requiredWitness = object(req.get("witness"), "required witness");
        Map<String, Object> witness = object(architecture.get("witness"), "architecture witness");
        for (String field : List.of("siteId", "faultDomain", "hostname", "vsanIp", "vsanCidr",
                "lifecycleManagedBy")) {
            equal(witness.get(field), requiredWitness.get(field), "witness " + field);
        }
        equal(witness.get("role"), "VSAN_WITNESS", "witness role");
        equal(witness.get("witnessOnly"), Boolean.TRUE, "witness-only placement");
        equal(witness.get("runsWorkloads"), Boolean.FALSE, "witness workload placement");
        String witnessSite = string(witness.get("siteId"), "witness site");
        require(!sitesById.containsKey(witnessSite), "witness must be at a third site");
        require(!specHosts.contains(string(witness.get("hostname"), "witness hostname")),
                "witness must not be a management-cluster host");

        Map<String, Object> assessment = object(architecture.get("capacityAssessment"),
                "capacityAssessment");
        equal(assessment.get("basis"), "PER_DATA_SITE_AFTER_PEER_SITE_FAILURE", "capacity basis");
        equal(assessment.get("required"), requiredCapacity, "capacity requirement record");
        equal(assessment.get("meetsRequirement"), Boolean.TRUE, "capacity outcome");
        Map<String, Object> provided = object(assessment.get("providedPerDataSite"),
                "providedPerDataSite");
        equal(provided.get("physicalCores"), multiply(hostProfile, "physicalCores", 4),
                "surviving physical cores");
        equal(provided.get("memoryGiB"), multiply(hostProfile, "memoryGiB", 4),
                "surviving memory");
        equal(provided.get("rawStorageTiB"), multiply(hostProfile, "rawStorageTiB", 4),
                "surviving raw storage");
    }

    private static void assertCapacity(String siteId, int hosts, Map<String, Object> profile,
            Map<String, Object> required) {
        for (String field : List.of("physicalCores", "memoryGiB", "rawStorageTiB")) {
            BigDecimal available = decimal(profile.get(field), field).multiply(BigDecimal.valueOf(hosts));
            BigDecimal needed = decimal(required.get(field), field);
            require(available.compareTo(needed) >= 0,
                    siteId + " cannot meet surviving " + field + " requirement");
        }
    }

    private static BigDecimal multiply(Map<String, Object> values, String field, int multiplier) {
        return decimal(values.get(field), field).multiply(BigDecimal.valueOf(multiplier));
    }

    private static void checkMigration(Map<String, Object> plan, Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        equal(plan.get("schemaVersion"), "1.0", "migration schemaVersion");
        equal(plan.get("estateId"), inventory.get("estateId"), "migration estateId");
        equal(plan.get("targetVcfVersion"), snapshot.get("targetVcfVersion"), "migration target");
        equal(plan.get("compatibilitySnapshotId"), snapshot.get("snapshotId"), "snapshot identity");

        List<Object> components = array(inventory.get("components"), "inventory components");
        List<Object> steps = array(plan.get("steps"), "migration steps");
        require(steps.size() == components.size(), "every inventory component must have one step");
        Map<String, Map<String, Object>> inventoryById = keyedObjects(components, "id");
        Map<String, Object> paths = object(snapshot.get("upgradePaths"), "upgrade paths");
        List<Object> phases = array(snapshot.get("phaseOrder"), "phase order");
        Map<String, Integer> phaseIndex = new HashMap<>();
        for (int i = 0; i < phases.size(); i++) {
            phaseIndex.put(string(phases.get(i), "phase"), i);
        }

        Set<String> seenComponents = new HashSet<>();
        Set<String> seenStepIds = new HashSet<>();
        Map<String, Set<String>> stepIdsByPhase = new LinkedHashMap<>();
        int priorPhase = -1;
        for (int i = 0; i < steps.size(); i++) {
            Map<String, Object> step = object(steps.get(i), "migration step " + (i + 1));
            String expectedStepId = String.format("step-%02d", i + 1);
            equal(step.get("stepId"), expectedStepId, "step id at order " + (i + 1));
            equal(step.get("order"), number(i + 1), "step order");
            String stepId = string(step.get("stepId"), "stepId");
            require(seenStepIds.add(stepId), "duplicate step id " + stepId);
            String componentId = string(step.get("componentId"), "componentId");
            require(seenComponents.add(componentId), "duplicate component step " + componentId);
            Map<String, Object> component = inventoryById.get(componentId);
            require(component != null, "unknown component " + componentId);
            equal(step.get("currentProduct"), component.get("product"), componentId + " current product");
            equal(step.get("currentVersion"), component.get("version"), componentId + " current version");

            String product = string(component.get("product"), "product");
            String version = string(component.get("version"), "version");
            Map<String, Object> upgrade = object(paths.get(product + "@" + version),
                    "upgrade path for " + componentId);
            equal(step.get("targetProduct"), upgrade.get("targetProduct"), componentId + " target product");
            equal(step.get("targetVersion"), upgrade.get("targetVersion"), componentId + " target version");
            equal(step.get("action"), upgrade.get("action"), componentId + " action");
            equal(step.get("phase"), product, componentId + " phase");
            Integer currentPhase = phaseIndex.get(product);
            require(currentPhase != null, "component product has no phase: " + product);
            require(currentPhase >= priorPhase, "migration phases are out of order at " + componentId);
            priorPhase = currentPhase;

            Set<String> gates = stringSet(array(step.get("gatedBy"), componentId + " gates"));
            if (currentPhase == 0) {
                require(gates.isEmpty(), "first phase must not invent a technical predecessor");
            } else {
                String predecessor = string(phases.get(currentPhase - 1), "predecessor phase");
                Set<String> requiredGates = stepIdsByPhase.getOrDefault(predecessor, Set.of());
                require(!requiredGates.isEmpty(), componentId + " has no completed predecessor phase");
                equal(gates, requiredGates, componentId + " technical gates");
                require(seenStepIds.containsAll(gates), componentId + " references a future gate");
            }
            stepIdsByPhase.computeIfAbsent(product, ignored -> new LinkedHashSet<>()).add(stepId);
        }
        equal(seenComponents, inventoryById.keySet(), "planned inventory coverage");
    }

    private static Set<String> stringSet(List<Object> values) {
        Set<String> result = new LinkedHashSet<>();
        for (Object value : values) {
            result.add(string(value, "string array item"));
        }
        return result;
    }

    private static Map<String, Map<String, Object>> keyedObjects(List<Object> values, String key) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : values) {
            Map<String, Object> item = object(value, "array object");
            String id = string(item.get(key), key);
            require(result.put(id, item) == null, "duplicate " + key + " " + id);
        }
        return result;
    }

    private static Object path(Map<String, Object> root, String... names) {
        Object current = root;
        for (String name : names) {
            current = object(current, "path segment " + name).get(name);
            require(current != null, "missing path segment " + name);
        }
        return current;
    }

    private static void equal(Object actual, Object expected, String label) {
        require(actual != null && actual.equals(expected),
                label + " mismatch: expected " + expected + ", got " + actual);
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
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

    private static String string(Object value, String label) {
        require(value instanceof String, label + " must be a string");
        return (String) value;
    }

    private static BigDecimal decimal(Object value, String label) {
        require(value instanceof BigDecimal, label + " must be numeric");
        return (BigDecimal) value;
    }

    private static BigDecimal number(long value) {
        return BigDecimal.valueOf(value);
    }

    private static final class SchemaValidator {
        private final Map<String, Object> document;

        SchemaValidator(Map<String, Object> document) {
            this.document = document;
        }

        void validate(Object value, Map<String, Object> schema, String at) {
            Object ref = schema.get("$ref");
            if (ref != null) {
                validate(value, resolve(string(ref, "$ref")), at);
                return;
            }
            if (Boolean.TRUE.equals(schema.get("nullable")) && value == null) {
                return;
            }
            if (schema.containsKey("const")) {
                equal(value, schema.get("const"), at + " const");
            }
            if (schema.containsKey("enum")) {
                require(array(schema.get("enum"), at + " enum").contains(value),
                        at + " is not one of " + schema.get("enum"));
            }
            Object type = schema.get("type");
            if (type != null) {
                switch (string(type, at + " type")) {
                    case "object" -> validateObject(value, schema, at);
                    case "array" -> validateArray(value, schema, at);
                    case "string" -> validateString(value, schema, at);
                    case "integer" -> validateNumber(value, schema, at, true);
                    case "number" -> validateNumber(value, schema, at, false);
                    case "boolean" -> require(value instanceof Boolean, at + " must be boolean");
                    case "null" -> require(value == null, at + " must be null");
                    default -> throw new AssertionError(at + " has unsupported schema type " + type);
                }
            }
            if (schema.containsKey("allOf")) {
                for (Object part : array(schema.get("allOf"), at + " allOf")) {
                    validate(value, object(part, at + " allOf member"), at);
                }
            }
            if (schema.containsKey("oneOf")) {
                int accepted = 0;
                for (Object part : array(schema.get("oneOf"), at + " oneOf")) {
                    try {
                        validate(value, object(part, at + " oneOf member"), at);
                        accepted++;
                    } catch (AssertionError ignored) {
                        // Count only successful alternatives.
                    }
                }
                require(accepted == 1, at + " must satisfy exactly one oneOf alternative");
            }
        }

        private void validateObject(Object value, Map<String, Object> schema, String at) {
            Map<String, Object> candidate = object(value, at);
            Set<String> required = new HashSet<>();
            if (schema.containsKey("required")) {
                for (Object item : array(schema.get("required"), at + " required")) {
                    required.add(string(item, "required property"));
                }
            }
            for (String name : required) {
                require(candidate.containsKey(name), at + " missing required property " + name);
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), at + " properties") : Map.of();
            for (Map.Entry<String, Object> entry : candidate.entrySet()) {
                Object propertySchema = properties.get(entry.getKey());
                if (propertySchema != null) {
                    validate(entry.getValue(), object(propertySchema, at + "." + entry.getKey() + " schema"),
                            at + "." + entry.getKey());
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    throw new AssertionError(at + " has unexpected property " + entry.getKey());
                } else if (schema.get("additionalProperties") instanceof Map<?, ?> additional) {
                    validate(entry.getValue(), object(additional, at + " additionalProperties"),
                            at + "." + entry.getKey());
                }
            }
        }

        private void validateArray(Object value, Map<String, Object> schema, String at) {
            List<Object> candidate = array(value, at);
            checkBound(candidate.size(), schema.get("minItems"), schema.get("maxItems"), at + " items");
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                require(new HashSet<>(candidate).size() == candidate.size(), at + " items must be unique");
            }
            if (schema.containsKey("items")) {
                Map<String, Object> itemSchema = object(schema.get("items"), at + " item schema");
                for (int i = 0; i < candidate.size(); i++) {
                    validate(candidate.get(i), itemSchema, at + "[" + i + "]");
                }
            }
        }

        private void validateString(Object value, Map<String, Object> schema, String at) {
            String candidate = string(value, at);
            checkBound(candidate.length(), schema.get("minLength"), schema.get("maxLength"), at + " length");
            if (schema.containsKey("pattern")) {
                String expression = string(schema.get("pattern"), at + " pattern");
                require(Pattern.compile(expression).matcher(candidate).find(),
                        at + " does not match pattern " + expression);
            }
        }

        private void validateNumber(Object value, Map<String, Object> schema, String at, boolean integer) {
            BigDecimal candidate = decimal(value, at);
            if (integer) {
                require(candidate.stripTrailingZeros().scale() <= 0, at + " must be an integer");
            }
            if (schema.containsKey("minimum")) {
                require(candidate.compareTo(decimal(schema.get("minimum"), at + " minimum")) >= 0,
                        at + " is below minimum");
            }
            if (schema.containsKey("maximum")) {
                require(candidate.compareTo(decimal(schema.get("maximum"), at + " maximum")) <= 0,
                        at + " is above maximum");
            }
        }

        private void checkBound(int actual, Object minimum, Object maximum, String at) {
            if (minimum != null) {
                require(BigDecimal.valueOf(actual).compareTo(decimal(minimum, at + " minimum")) >= 0,
                        at + " below minimum");
            }
            if (maximum != null) {
                require(BigDecimal.valueOf(actual).compareTo(decimal(maximum, at + " maximum")) <= 0,
                        at + " above maximum");
            }
        }

        private Map<String, Object> resolve(String ref) {
            require(ref.startsWith("#/"), "only local schema references are supported: " + ref);
            Object current = document;
            for (String encoded : ref.substring(2).split("/")) {
                String name = encoded.replace("~1", "/").replace("~0", "~");
                current = object(current, "schema reference " + ref).get(name);
                require(current != null, "unresolved schema reference " + ref);
            }
            return object(current, "resolved schema " + ref);
        }
    }

    private static final class Json {
        private final String text;
        private int offset;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            Json parser = new Json(text);
            Object value = parser.value();
            parser.space();
            require(parser.offset == text.length(), "trailing JSON content at offset " + parser.offset);
            return value;
        }

        private Object value() {
            space();
            require(offset < text.length(), "unexpected end of JSON");
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
            offset++;
            Map<String, Object> result = new LinkedHashMap<>();
            space();
            if (take('}')) {
                return result;
            }
            do {
                space();
                require(offset < text.length() && text.charAt(offset) == '"',
                        "object key must be a string at offset " + offset);
                String key = stringValue();
                space();
                require(take(':'), "missing ':' at offset " + offset);
                require(!result.containsKey(key), "duplicate JSON key " + key);
                result.put(key, value());
                space();
            } while (take(','));
            require(take('}'), "missing '}' at offset " + offset);
            return result;
        }

        private List<Object> arrayValue() {
            offset++;
            List<Object> result = new ArrayList<>();
            space();
            if (take(']')) {
                return result;
            }
            do {
                result.add(value());
                space();
            } while (take(','));
            require(take(']'), "missing ']' at offset " + offset);
            return result;
        }

        private String stringValue() {
            require(take('"'), "missing string quote at offset " + offset);
            StringBuilder result = new StringBuilder();
            while (offset < text.length()) {
                char ch = text.charAt(offset++);
                if (ch == '"') {
                    return result.toString();
                }
                if (ch != '\\') {
                    require(ch >= 0x20, "unescaped control character in JSON string");
                    result.append(ch);
                    continue;
                }
                require(offset < text.length(), "unfinished JSON escape");
                char escaped = text.charAt(offset++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        require(offset + 4 <= text.length(), "short unicode escape");
                        String hex = text.substring(offset, offset + 4);
                        try {
                            result.append((char) Integer.parseInt(hex, 16));
                        } catch (NumberFormatException error) {
                            throw new AssertionError("bad unicode escape " + hex, error);
                        }
                        offset += 4;
                    }
                    default -> throw new AssertionError("bad JSON escape \\" + escaped);
                }
            }
            throw new AssertionError("unterminated JSON string");
        }

        private Object numberValue() {
            int start = offset;
            if (take('-')) {
                require(offset < text.length(), "unfinished JSON number");
            }
            if (take('0')) {
                require(offset >= text.length() || !Character.isDigit(text.charAt(offset)),
                        "leading zero in JSON number");
            } else {
                digits();
            }
            if (take('.')) {
                digits();
            }
            if (offset < text.length() && (text.charAt(offset) == 'e' || text.charAt(offset) == 'E')) {
                offset++;
                if (offset < text.length() && (text.charAt(offset) == '+' || text.charAt(offset) == '-')) {
                    offset++;
                }
                digits();
            }
            require(offset > start, "invalid JSON value at offset " + start);
            try {
                return new BigDecimal(text.substring(start, offset));
            } catch (NumberFormatException error) {
                throw new AssertionError("invalid JSON number at offset " + start, error);
            }
        }

        private void digits() {
            int start = offset;
            while (offset < text.length() && Character.isDigit(text.charAt(offset))) {
                offset++;
            }
            require(offset > start, "digit expected at offset " + start);
        }

        private Object literal(String literal, Object value) {
            require(text.startsWith(literal, offset), "bad JSON literal at offset " + offset);
            offset += literal.length();
            return value;
        }

        private void space() {
            while (offset < text.length()) {
                char ch = text.charAt(offset);
                if (ch != ' ' && ch != '\n' && ch != '\r' && ch != '\t') {
                    return;
                }
                offset++;
            }
        }

        private boolean take(char expected) {
            if (offset < text.length() && text.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }
    }
}
