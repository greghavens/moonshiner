import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.DateTimeException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.regex.PatternSyntaxException;

public final class TestMain {
    private static final Path INVENTORY = Path.of("fixtures/estate-inventory.json");
    private static final Path SNAPSHOT = Path.of(".protected/compatibility-snapshot.json");
    private static final Path PLAN_SCHEMA = Path.of("docs/migration-plan-schema.json");
    private static final Path INSTALLER_SPEC =
            Path.of("specifications/vcf-installer/vcf-installer-openapi.json");
    private static final Path ARTIFACT = Path.of("migration-plan.json");

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        VcfArchitectureClient.main(new String[] {
                INVENTORY.toString(), SNAPSHOT.toString(), ARTIFACT.toString()
        });

        Object artifactRoot = Json.parse(Files.readString(ARTIFACT, StandardCharsets.UTF_8));
        Map<String, Object> artifact = object(artifactRoot, "$artifact");
        Map<String, Object> targetArchitecture =
                object(artifact.get("targetArchitecture"), "$.targetArchitecture");
        Object sddcSpec = targetArchitecture.get("sddcSpec");

        // This is deliberately the first validation of the generated artifact.
        // All migration-plan, fixture, and compatibility assertions occur below it.
        Object installerRoot = Json.parse(Files.readString(INSTALLER_SPEC, StandardCharsets.UTF_8));
        Map<String, Object> installer = object(installerRoot, "$openapi");
        Object officialSddcSchema = pointer(installer, "#/components/schemas/SddcSpec");
        new SchemaValidator(installer).validate(officialSddcSchema, sddcSpec,
                "$.targetArchitecture.sddcSpec");
        System.out.println("PASS: target sddcSpec validates against the tagged installer SddcSpec");

        Object migrationSchemaRoot = Json.parse(Files.readString(PLAN_SCHEMA, StandardCharsets.UTF_8));
        new SchemaValidator(object(migrationSchemaRoot, "$migrationSchema"))
                .validate(migrationSchemaRoot, artifactRoot, "$artifact");
        verifyResearch(artifact);

        Map<String, Object> inventory = object(
                Json.parse(Files.readString(INVENTORY, StandardCharsets.UTF_8)), "$inventory");
        Map<String, Object> snapshot = object(
                Json.parse(Files.readString(SNAPSHOT, StandardCharsets.UTF_8)), "$snapshot");

        verifyReleasePath(artifact, inventory, snapshot);
        verifyArchitecture(artifact, inventory, snapshot);
        verifyMigrationSteps(artifact, inventory, snapshot);
        System.out.println("PASS: migration architecture matches the estate and pinned compatibility snapshot");
    }

    private static void verifyReleasePath(Map<String, Object> artifact,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        equal(text(artifact.get("schemaVersion"), "$.schemaVersion"),
                text(snapshot.get("planSchemaVersion"), "$snapshot.planSchemaVersion"),
                "schema version");
        equal(text(artifact.get("estateId"), "$.estateId"),
                text(inventory.get("estateId"), "$inventory.estateId"), "estate id");

        List<Object> path = array(artifact.get("releasePath"), "$.releasePath");
        List<Object> supported = array(snapshot.get("supportedReleaseTransitions"),
                "$snapshot.supportedReleaseTransitions");
        check(!path.isEmpty(), "releasePath must not be empty");
        equal(text(object(path.get(0), "$.releasePath[0]").get("from"),
                        "$.releasePath[0].from"),
                text(inventory.get("vcfVersion"), "$inventory.vcfVersion"),
                "release path source");
        equal(text(object(path.get(path.size() - 1), "$.releasePath[last]").get("to"),
                        "$.releasePath[last].to"),
                text(snapshot.get("targetVersion"), "$snapshot.targetVersion"),
                "release path target");

        String previousTarget = null;
        for (int i = 0; i < path.size(); i++) {
            Map<String, Object> edge = object(path.get(i), "$.releasePath[" + i + "]");
            if (previousTarget != null) {
                equal(text(edge.get("from"), "release edge from"), previousTarget,
                        "release path continuity");
            }
            boolean allowed = supported.stream().map(value -> object(value, "supported edge"))
                    .anyMatch(candidate -> sameFields(edge, candidate,
                            List.of("from", "to", "method")));
            check(allowed, "unsupported release hop: " + edge);
            previousTarget = text(edge.get("to"), "release edge to");
        }
        equal(path.size(), 1, "the supported source uses one direct LCM transition");
    }

    private static void verifyArchitecture(Map<String, Object> artifact,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        Map<String, Object> architecture = object(artifact.get("targetArchitecture"),
                "$.targetArchitecture");
        equal(integer(architecture.get("siteCount"), "$.targetArchitecture.siteCount"),
                integer(inventory.get("siteCount"), "$inventory.siteCount"), "site count");
        equal(text(architecture.get("topology"), "$.targetArchitecture.topology"),
                text(inventory.get("topology"), "$inventory.topology"), "topology");

        int hostCount = integer(architecture.get("hostCount"),
                "$.targetArchitecture.hostCount");
        int fixtureHostCount = integer(object(inventory.get("managementDomain"),
                "$inventory.managementDomain").get("hostCount"), "inventory hostCount");
        int minimum = integer(object(snapshot.get("minimumHostCounts"),
                "$snapshot.minimumHostCounts").get("CONSOLIDATED"), "minimum host count");
        equal(hostCount, fixtureHostCount, "fixture host count");
        equal(hostCount, minimum, "minimum consolidated host count");

        Map<String, Object> spec = object(architecture.get("sddcSpec"),
                "$.targetArchitecture.sddcSpec");
        equal(text(spec.get("sddcId"), "sddcId"), text(inventory.get("estateId"), "estateId"),
                "SddcSpec id");
        equal(text(spec.get("workflowType"), "workflowType"), "VCF", "workflow type");
        equal(text(spec.get("version"), "SddcSpec version"),
                text(snapshot.get("targetVersion"), "target version"), "SddcSpec version");

        Map<String, Object> domain = object(inventory.get("managementDomain"),
                "$inventory.managementDomain");
        List<String> expectedHosts = strings(array(domain.get("hosts"), "inventory hosts"));
        List<Object> hostSpecs = array(spec.get("hostSpecs"), "sddcSpec.hostSpecs");
        List<String> actualHosts = new ArrayList<>();
        for (Object value : hostSpecs) {
            actualHosts.add(text(object(value, "hostSpec").get("hostname"), "hostSpec.hostname"));
        }
        equal(actualHosts, expectedHosts, "the four carried host names");
        equal(new HashSet<>(actualHosts).size(), actualHosts.size(), "unique host names");

        Map<String, Object> appliances = object(inventory.get("appliances"),
                "$inventory.appliances");
        Map<String, Object> secrets = object(inventory.get("fixtureSecrets"),
                "$inventory.fixtureSecrets");
        Map<String, Object> sourceVcenter = object(appliances.get("vcenter"), "inventory vcenter");
        Map<String, Object> targetVcenter = object(spec.get("vcenterSpec"), "vcenterSpec");
        applianceField(targetVcenter, sourceVcenter, "vcenterHostname", "hostname");
        equal(targetVcenter.get("rootVcenterPassword"), secrets.get("vcenterRootPassword"),
                "fixture vCenter password");
        equal(targetVcenter.get("sslThumbprint"), sourceVcenter.get("sslThumbprint"),
                "vCenter thumbprint");
        equal(targetVcenter.get("useExistingDeployment"), Boolean.TRUE,
                "existing vCenter flag");
        equal(targetVcenter.get("version"), snapshot.get("targetVersion"),
                "vCenter target version");

        Map<String, Object> sourceSddc = object(appliances.get("sddcManager"),
                "inventory SDDC Manager");
        Map<String, Object> targetSddc = object(spec.get("sddcManagerSpec"), "sddcManagerSpec");
        applianceField(targetSddc, sourceSddc, "hostname", "hostname");
        equal(targetSddc.get("rootPassword"), secrets.get("sddcManagerRootPassword"),
                "fixture SDDC Manager password");
        equal(targetSddc.get("sslThumbprint"), sourceSddc.get("sslThumbprint"),
                "SDDC Manager thumbprint");
        equal(targetSddc.get("useExistingDeployment"), Boolean.TRUE,
                "existing SDDC Manager flag");
        equal(targetSddc.get("version"), snapshot.get("targetVersion"),
                "SDDC Manager target version");

        Map<String, Object> sourceNsx = object(appliances.get("nsx"), "inventory NSX");
        Map<String, Object> targetNsx = object(spec.get("nsxtSpec"), "nsxtSpec");
        equal(targetNsx.get("vipFqdn"), sourceNsx.get("vipFqdn"), "NSX VIP");
        equal(targetNsx.get("sslThumbprint"), sourceNsx.get("sslThumbprint"),
                "NSX thumbprint");
        equal(targetNsx.get("useExistingDeployment"), Boolean.TRUE, "existing NSX flag");
        equal(targetNsx.get("version"), snapshot.get("targetVersion"), "NSX target version");
        List<String> nsxManagers = new ArrayList<>();
        for (Object value : array(targetNsx.get("nsxtManagers"), "nsxtManagers")) {
            nsxManagers.add(text(object(value, "nsxtManager").get("hostname"),
                    "nsxtManager.hostname"));
        }
        equal(nsxManagers, strings(array(sourceNsx.get("managerHostnames"),
                "inventory NSX managers")), "NSX manager hosts");

        Map<String, Object> sourceOperations = object(appliances.get("operations"),
                "inventory operations");
        Map<String, Object> targetOperations = object(spec.get("vcfOperationsSpec"),
                "vcfOperationsSpec");
        List<Object> nodes = array(targetOperations.get("nodes"), "operations nodes");
        equal(nodes.size(), 1, "operations node count");
        Map<String, Object> operationNode = object(nodes.get(0), "operations node");
        equal(operationNode.get("hostname"), sourceOperations.get("hostname"),
                "operations hostname");
        equal(operationNode.get("sslThumbprint"), sourceOperations.get("sslThumbprint"),
                "operations thumbprint");
        equal(targetOperations.get("adminUserPassword"), secrets.get("operationsAdminPassword"),
                "fixture operations password");
        equal(targetOperations.get("useExistingDeployment"), Boolean.TRUE,
                "existing operations flag");
        equal(targetOperations.get("version"), snapshot.get("targetVersion"),
                "operations target version");

        Map<String, Object> reserved = object(inventory.get("reservedTargetInputs"),
                "$inventory.reservedTargetInputs");
        Map<String, Object> reservedServices = object(reserved.get("managementServices"),
                "$inventory.reservedTargetInputs.managementServices");
        Map<String, Object> vsp = object(spec.get("vspClusterSpec"), "vspClusterSpec");
        equal(vsp.get("platformFqdn"), reservedServices.get("platformFqdn"),
                "management services platform FQDN");
        equal(vsp.get("instanceFqdn"), reservedServices.get("instanceFqdn"),
                "management services instance FQDN");
        equal(vsp.get("fleetFqdn"), reservedServices.get("fleetFqdn"),
                "management services fleet FQDN");
        equal(vsp.get("internalClusterCidrIpv4"),
                reservedServices.get("internalClusterCidrIpv4"),
                "management services internal CIDR");
        Map<String, Object> ipv4Pool = object(vsp.get("ipv4Pool"), "vspClusterSpec.ipv4Pool");
        equal(ipv4Pool.get("addresses"), reservedServices.get("ipv4Addresses"),
                "reserved management services addresses");
        equal(array(ipv4Pool.get("addresses"), "management services addresses").size(), 12,
                "management services reserved address count");
        equal(vsp.get("version"), snapshot.get("targetVersion"),
                "management services target version");
        equal(vsp.get("useExistingDeployment"), Boolean.FALSE,
                "new management services deployment flag");

        Map<String, Object> licenseServer = object(spec.get("licenseServerSpec"),
                "licenseServerSpec");
        equal(licenseServer.get("hostname"), reserved.get("licenseServerHostname"),
                "license server FQDN");
        equal(licenseServer.get("version"), snapshot.get("targetVersion"),
                "license server target version");
        equal(licenseServer.get("useExistingDeployment"), Boolean.FALSE,
                "new license server deployment flag");

        equal(spec.get("dnsSpec"), inventory.get("dns"), "DNS specification");
        equal(spec.get("ntpServers"), inventory.get("ntpServers"), "NTP servers");
        equal(spec.get("networkSpecs"), inventory.get("networks"), "network specifications");
        equal(object(spec.get("datastoreSpec"), "datastoreSpec").get("existingDatastoreName"),
                domain.get("datastoreName"), "existing datastore");
        Map<String, Object> cluster = object(spec.get("clusterSpec"), "clusterSpec");
        equal(cluster.get("clusterName"), domain.get("clusterName"), "cluster name");
        equal(cluster.get("resourcePoolSpecs"), domain.get("resourcePools"),
                "consolidated resource pools");

        assertLowerCase(actualHosts, "ESXi host names");
        assertLowerCase(List.of(text(targetVcenter.get("vcenterHostname"), "vCenter hostname"),
                text(targetSddc.get("hostname"), "SDDC Manager hostname"),
                text(targetNsx.get("vipFqdn"), "NSX VIP"),
                text(operationNode.get("hostname"), "Operations hostname"),
                text(vsp.get("platformFqdn"), "management services platform FQDN"),
                text(vsp.get("instanceFqdn"), "management services instance FQDN"),
                text(vsp.get("fleetFqdn"), "management services fleet FQDN"),
                text(licenseServer.get("hostname"), "license server FQDN")), "appliance FQDNs");
        assertLowerCase(nsxManagers, "NSX Manager FQDNs");
    }

    private static void verifyMigrationSteps(Map<String, Object> artifact,
            Map<String, Object> inventory, Map<String, Object> snapshot) {
        List<Object> actual = array(artifact.get("migrationPlan"), "$.migrationPlan");
        List<Object> expected = array(snapshot.get("componentTransitions"),
                "$snapshot.componentTransitions");
        equal(actual.size(), expected.size(), "component transition count");

        Set<String> completed = new LinkedHashSet<>();
        Set<String> actualIds = new LinkedHashSet<>();
        for (int i = 0; i < expected.size(); i++) {
            Map<String, Object> step = object(actual.get(i), "$.migrationPlan[" + i + "]");
            Map<String, Object> authority = object(expected.get(i),
                    "$snapshot.componentTransitions[" + i + "]");
            for (String field : List.of("order", "stepId", "componentId", "component",
                    "fromVersion", "targetComponent", "toVersion", "action", "execution")) {
                equal(step.get(field), authority.get(field), "transition " + (i + 1) + " " + field);
            }
            Map<String, Object> gates = object(step.get("gates"), "step gates");
            equal(gates.get("requiresCompletedSteps"), authority.get("requiresCompletedSteps"),
                    "transition " + (i + 1) + " completed-step gates");
            equal(gates.get("preconditions"), authority.get("preconditions"),
                    "transition " + (i + 1) + " technical preconditions");

            for (String dependency : strings(array(gates.get("requiresCompletedSteps"),
                    "step dependencies"))) {
                check(completed.contains(dependency),
                        "step " + step.get("stepId") + " depends on a later or missing step "
                                + dependency);
            }
            String stepId = text(step.get("stepId"), "stepId");
            check(completed.add(stepId), "duplicate step id " + stepId);
            String componentId = text(step.get("componentId"), "componentId");
            check(actualIds.add(componentId), "duplicate component transition " + componentId);

            if (authority.containsKey("minimumHostsOnline")) {
                equal(step.get("minimumHostsOnline"), authority.get("minimumHostsOnline"),
                        "rolling minimum online hosts");
            } else {
                check(!step.containsKey("minimumHostsOnline"),
                        "minimumHostsOnline belongs only to the host/vSAN step");
            }
        }

        Map<String, Map<String, Object>> fixtureComponents = new LinkedHashMap<>();
        for (Object value : array(inventory.get("components"), "$inventory.components")) {
            Map<String, Object> component = object(value, "inventory component");
            fixtureComponents.put(text(component.get("id"), "component id"), component);
        }
        Set<String> plannedExisting = new LinkedHashSet<>();
        for (Object value : actual) {
            Map<String, Object> step = object(value, "migration step");
            String componentId = text(step.get("componentId"), "componentId");
            Map<String, Object> source = fixtureComponents.get(componentId);
            if (source != null) {
                equal(step.get("component"), source.get("name"), componentId + " source name");
                equal(step.get("fromVersion"), source.get("version"),
                        componentId + " source version");
                check(plannedExisting.add(componentId),
                        "inventory component appears more than once: " + componentId);
            } else {
                equal(step.get("fromVersion"), "ABSENT",
                        componentId + " must be an explicit target-only deployment");
            }
        }
        equal(plannedExisting, fixtureComponents.keySet(),
                "every inventory component accounted for exactly once");
    }

    private static void verifyResearch(Map<String, Object> artifact) {
        List<Object> sources = array(artifact.get("researchConsulted"),
                "$.researchConsulted");
        Set<String> urls = new LinkedHashSet<>();
        for (int i = 0; i < sources.size(); i++) {
            Map<String, Object> source = object(sources.get(i),
                    "$.researchConsulted[" + i + "]");
            String title = text(source.get("title"), "research title");
            check(!title.isBlank(), "research title must not be blank");

            String url = text(source.get("url"), "research URL");
            check(url.equals(url.trim()), "research URL must not contain surrounding whitespace");
            check(urls.add(url), "research URLs must be unique: " + url);
            URI uri;
            try {
                uri = new URI(url);
            } catch (URISyntaxException error) {
                throw new AssertionError("research URL is not a valid URI: " + url, error);
            }
            equal(uri.getScheme(), "https", "research URL scheme");
            check(uri.getHost() != null, "research URL must have a host: " + url);
            check(uri.getUserInfo() == null, "research URL must not contain user information");
            check(isBroadcomPublished(uri),
                    "research URL must be published by Broadcom or VMware: " + url);

            String consultedOn = text(source.get("consultedOn"), "research consultation date");
            try {
                LocalDate.parse(consultedOn);
            } catch (DateTimeException error) {
                throw new AssertionError(
                        "research consultation date must be a valid YYYY-MM-DD date: "
                                + consultedOn,
                        error);
            }
        }
    }

    private static boolean isBroadcomPublished(URI uri) {
        String host = uri.getHost().toLowerCase(java.util.Locale.ROOT);
        if (host.equals("broadcom.com") || host.endsWith(".broadcom.com")
                || host.equals("vmware.com") || host.endsWith(".vmware.com")
                || host.equals("vmware.github.io")) {
            return true;
        }
        return host.equals("github.com") && uri.getPath() != null
                && uri.getPath().startsWith("/vmware/");
    }

    private static boolean sameFields(Map<String, Object> left, Map<String, Object> right,
            List<String> fields) {
        for (String field : fields) {
            if (!Objects.equals(left.get(field), right.get(field))) {
                return false;
            }
        }
        return true;
    }

    private static void applianceField(Map<String, Object> target, Map<String, Object> source,
            String targetField, String sourceField) {
        equal(target.get(targetField), source.get(sourceField), targetField);
    }

    private static void assertLowerCase(List<String> values, String label) {
        for (String value : values) {
            equal(value, value.toLowerCase(java.util.Locale.ROOT), label + " must be lower-case");
        }
    }

    private static Map<String, Object> object(Object value, String path) {
        if (!(value instanceof Map<?, ?> raw)) {
            throw new AssertionError(path + " must be an object");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : raw.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new AssertionError(path + " has a non-string key");
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    private static List<Object> array(Object value, String path) {
        if (!(value instanceof List<?> raw)) {
            throw new AssertionError(path + " must be an array");
        }
        return new ArrayList<>(raw);
    }

    private static List<String> strings(List<Object> values) {
        List<String> result = new ArrayList<>();
        for (int i = 0; i < values.size(); i++) {
            result.add(text(values.get(i), "string array item " + i));
        }
        return result;
    }

    private static String text(Object value, String path) {
        if (!(value instanceof String result)) {
            throw new AssertionError(path + " must be a string");
        }
        return result;
    }

    private static int integer(Object value, String path) {
        if (!(value instanceof BigDecimal number) || number.stripTrailingZeros().scale() > 0) {
            throw new AssertionError(path + " must be an integer");
        }
        return number.intValueExact();
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void equal(Object actual, Object expected, String label) {
        if (!Objects.equals(actual, expected)) {
            throw new AssertionError(label + ": expected " + expected + ", got " + actual);
        }
    }

    private static Object pointer(Map<String, Object> root, String reference) {
        check(reference.startsWith("#/"), "only local JSON pointers are supported: " + reference);
        Object current = root;
        for (String encoded : reference.substring(2).split("/", -1)) {
            String token = encoded.replace("~1", "/").replace("~0", "~");
            current = object(current, reference).get(token);
            check(current != null, "unresolved JSON pointer " + reference);
        }
        return current;
    }

    private static final class SchemaValidator {
        private final Map<String, Object> root;

        private SchemaValidator(Map<String, Object> root) {
            this.root = root;
        }

        void validate(Object rawSchema, Object value, String path) {
            Map<String, Object> schema = object(rawSchema, path + " schema");
            if (schema.containsKey("$ref")) {
                validate(pointer(root, text(schema.get("$ref"), path + " $ref")), value, path);
                return;
            }
            if (Boolean.TRUE.equals(schema.get("nullable")) && value == null) {
                return;
            }
            if (schema.containsKey("const")) {
                equal(value, schema.get("const"), path + " const");
            }
            if (schema.containsKey("enum")) {
                check(array(schema.get("enum"), path + " enum").contains(value),
                        path + " is not one of the schema enum values");
            }
            validateCombiners(schema, value, path);

            Object typeValue = schema.get("type");
            if (typeValue == null) {
                return;
            }
            if (typeValue instanceof List<?>) {
                boolean matched = false;
                for (Object candidate : array(typeValue, path + " types")) {
                    if (hasType(value, text(candidate, path + " type"))) {
                        matched = true;
                    }
                }
                check(matched, path + " does not match any permitted type");
                return;
            }
            String type = text(typeValue, path + " type");
            check(hasType(value, type), path + " must be " + type);
            switch (type) {
                case "object" -> validateObject(schema, object(value, path), path);
                case "array" -> validateArray(schema, array(value, path), path);
                case "string" -> validateString(schema, text(value, path), path);
                case "integer", "number" -> validateNumber(schema, (BigDecimal) value, path);
                default -> {
                    // boolean and null need no additional keyword handling here.
                }
            }
        }

        private void validateCombiners(Map<String, Object> schema, Object value, String path) {
            if (schema.containsKey("allOf")) {
                for (Object child : array(schema.get("allOf"), path + " allOf")) {
                    validate(child, value, path);
                }
            }
            if (schema.containsKey("anyOf")) {
                int matches = countMatches(array(schema.get("anyOf"), path + " anyOf"), value, path);
                check(matches >= 1, path + " matches no anyOf branch");
            }
            if (schema.containsKey("oneOf")) {
                int matches = countMatches(array(schema.get("oneOf"), path + " oneOf"), value, path);
                equal(matches, 1, path + " oneOf match count");
            }
        }

        private int countMatches(List<Object> branches, Object value, String path) {
            int matches = 0;
            for (Object branch : branches) {
                try {
                    validate(branch, value, path);
                    matches++;
                } catch (AssertionError ignored) {
                    // A non-matching schema branch is expected here.
                }
            }
            return matches;
        }

        private boolean hasType(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "integer" -> value instanceof BigDecimal number
                        && number.stripTrailingZeros().scale() <= 0;
                case "number" -> value instanceof BigDecimal;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported schema type " + type);
            };
        }

        private void validateObject(Map<String, Object> schema, Map<String, Object> value,
                String path) {
            if (schema.containsKey("required")) {
                for (String key : strings(array(schema.get("required"), path + " required"))) {
                    check(value.containsKey(key), path + " is missing required property " + key);
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), path + " properties") : Map.of();
            for (Map.Entry<String, Object> entry : value.entrySet()) {
                Object childSchema = properties.get(entry.getKey());
                if (childSchema != null) {
                    validate(childSchema, entry.getValue(), path + "." + entry.getKey());
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    throw new AssertionError(path + " has unknown property " + entry.getKey());
                } else if (schema.get("additionalProperties") instanceof Map<?, ?>) {
                    validate(schema.get("additionalProperties"), entry.getValue(),
                            path + "." + entry.getKey());
                }
            }
            numericLimit(schema, "minProperties", value.size(), path);
            numericMaximum(schema, "maxProperties", value.size(), path);
        }

        private void validateArray(Map<String, Object> schema, List<Object> value, String path) {
            numericLimit(schema, "minItems", value.size(), path);
            numericMaximum(schema, "maxItems", value.size(), path);
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                equal(new HashSet<>(value).size(), value.size(), path + " uniqueItems");
            }
            if (schema.containsKey("items")) {
                for (int i = 0; i < value.size(); i++) {
                    validate(schema.get("items"), value.get(i), path + "[" + i + "]");
                }
            }
        }

        private void validateString(Map<String, Object> schema, String value, String path) {
            int length = value.codePointCount(0, value.length());
            numericLimit(schema, "minLength", length, path);
            numericMaximum(schema, "maxLength", length, path);
            if (schema.containsKey("pattern")) {
                String expression = text(schema.get("pattern"), path + " pattern");
                try {
                    check(Pattern.compile(expression).matcher(value).find(),
                            path + " does not match pattern " + expression);
                } catch (PatternSyntaxException error) {
                    throw new AssertionError(path + " has invalid schema pattern", error);
                }
            }
        }

        private void validateNumber(Map<String, Object> schema, BigDecimal value, String path) {
            if (schema.get("minimum") instanceof BigDecimal minimum) {
                check(value.compareTo(minimum) >= 0, path + " is below minimum " + minimum);
            }
            if (schema.get("maximum") instanceof BigDecimal maximum) {
                check(value.compareTo(maximum) <= 0, path + " is above maximum " + maximum);
            }
        }

        private void numericLimit(Map<String, Object> schema, String keyword, int actual,
                String path) {
            if (schema.get(keyword) instanceof BigDecimal limit) {
                check(actual >= limit.intValueExact(), path + " violates " + keyword);
            }
        }

        private void numericMaximum(Map<String, Object> schema, String keyword, int actual,
                String path) {
            if (schema.get(keyword) instanceof BigDecimal limit) {
                check(actual <= limit.intValueExact(), path + " violates " + keyword);
            }
        }
    }

    private static final class Json {
        private final String input;
        private int index;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) {
            Json parser = new Json(input);
            Object value = parser.value();
            parser.space();
            if (parser.index != input.length()) {
                parser.fail("trailing data");
            }
            return value;
        }

        private Object value() {
            space();
            if (index >= input.length()) {
                fail("expected a value");
            }
            return switch (input.charAt(index)) {
                case '{' -> objectValue();
                case '[' -> arrayValue();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> objectValue() {
            expect('{');
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            space();
            if (take('}')) {
                return result;
            }
            while (true) {
                space();
                if (index >= input.length() || input.charAt(index) != '"') {
                    fail("expected object key");
                }
                String key = string();
                check(!result.containsKey(key), "duplicate JSON key " + key);
                space();
                expect(':');
                result.put(key, value());
                space();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            expect('[');
            ArrayList<Object> result = new ArrayList<>();
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
                expect(',');
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < input.length()) {
                char c = input.charAt(index++);
                if (c == '"') {
                    return result.toString();
                }
                if (c == '\\') {
                    if (index >= input.length()) {
                        fail("unterminated escape");
                    }
                    char escaped = input.charAt(index++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> appendUnicode(result);
                        default -> fail("invalid escape");
                    }
                } else {
                    if (c < 0x20) {
                        fail("unescaped control character");
                    }
                    result.append(c);
                }
            }
            fail("unterminated string");
            return null;
        }

        private void appendUnicode(StringBuilder result) {
            if (index + 4 > input.length()) {
                fail("short unicode escape");
            }
            try {
                result.append((char) Integer.parseInt(input.substring(index, index + 4), 16));
                index += 4;
            } catch (NumberFormatException error) {
                fail("invalid unicode escape");
            }
        }

        private BigDecimal number() {
            int start = index;
            if (take('-')) {
                // sign consumed
            }
            if (take('0')) {
                // zero is the complete integer portion
            } else {
                digits();
            }
            if (take('.')) {
                digits();
            }
            if (take('e') || take('E')) {
                take('+');
                take('-');
                digits();
            }
            try {
                return new BigDecimal(input.substring(start, index));
            } catch (NumberFormatException error) {
                fail("invalid number");
                return null;
            }
        }

        private void digits() {
            int start = index;
            while (index < input.length() && Character.isDigit(input.charAt(index))) {
                index++;
            }
            if (start == index) {
                fail("expected digit");
            }
        }

        private Object literal(String spelling, Object value) {
            if (!input.startsWith(spelling, index)) {
                fail("invalid literal");
            }
            index += spelling.length();
            return value;
        }

        private void space() {
            while (index < input.length()) {
                char c = input.charAt(index);
                if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
                    index++;
                } else {
                    return;
                }
            }
        }

        private boolean take(char expected) {
            if (index < input.length() && input.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                fail("expected '" + expected + "'");
            }
        }

        private void fail(String message) {
            throw new AssertionError("invalid JSON at offset " + index + ": " + message);
        }
    }
}
