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

/** Deterministic verifier for the ArchitectureClient deliverable. */
public final class TestMain {
    private static final Path INVENTORY_PATH = Path.of("fixtures/estate-inventory.json");
    private static final Path SNAPSHOT_PATH = Path.of("fixtures/compatibility-snapshot.json");
    private static final Path MIGRATION_SCHEMA_PATH = Path.of("schema/migration-architecture-schema.json");
    private static final Path INSTALLER_SPEC_PATH =
            Path.of("specifications/vcf-installer/vcf-installer-openapi.json");

    private static final String INVENTORY_SHA256 =
            "e7d1b1c2e683bd60f064111fedfd320b873fe3dcbea3d538a8a090f20ccf912a";
    private static final String SNAPSHOT_SHA256 =
            "6a8e05de969441da3188b3d23b240912f4789fb2efb6a690aba57cac60553c16";
    private static final String MIGRATION_SCHEMA_SHA256 =
            "01acd9c24456d27dff7006fc731891bf95fba67036961b2b6b980140aac6d654";
    private static final String INSTALLER_SPEC_SHA256 =
            "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        String inventoryText = Files.readString(INVENTORY_PATH, StandardCharsets.UTF_8);
        String snapshotText = Files.readString(SNAPSHOT_PATH, StandardCharsets.UTF_8);
        String installerText = Files.readString(INSTALLER_SPEC_PATH, StandardCharsets.UTF_8);

        Object artifact = Json.parse(ArchitectureClient.design(inventoryText, snapshotText));
        Object installer = Json.parse(installerText);
        Map<String, Object> installerRoot = object(installer, "installer OpenAPI root");
        Map<String, Object> sddcSchema = object(
                object(object(installerRoot.get("components"), "OpenAPI components").get("schemas"),
                        "OpenAPI schemas").get("SddcSpec"),
                "OpenAPI SddcSpec");

        // This is deliberately the first semantic verification of the artifact.
        List<String> installerErrors = new ArrayList<>();
        SchemaValidator.validate(artifact, sddcSchema, installerRoot, "$", installerErrors);
        if (!installerErrors.isEmpty()) {
            throw new AssertionError("SddcSpec validation failed first: " + String.join("; ", installerErrors));
        }

        assertSha(INSTALLER_SPEC_PATH, INSTALLER_SPEC_SHA256);
        assertSha(INVENTORY_PATH, INVENTORY_SHA256);
        assertSha(SNAPSHOT_PATH, SNAPSHOT_SHA256);
        assertSha(MIGRATION_SCHEMA_PATH, MIGRATION_SCHEMA_SHA256);

        Object migrationSchema = Json.parse(
                Files.readString(MIGRATION_SCHEMA_PATH, StandardCharsets.UTF_8));
        List<String> migrationErrors = new ArrayList<>();
        SchemaValidator.validate(
                artifact,
                object(migrationSchema, "migration schema"),
                object(migrationSchema, "migration schema"),
                "$",
                migrationErrors);
        if (!migrationErrors.isEmpty()) {
            throw new AssertionError("Migration architecture schema failed: "
                    + String.join("; ", migrationErrors));
        }

        verifyArchitecture(
                object(artifact, "architecture artifact"),
                object(Json.parse(inventoryText), "inventory"),
                object(Json.parse(snapshotText), "compatibility snapshot"));

        System.out.println("PASS: SddcSpec and brownfield migration architecture are valid");
    }

    private static void verifyArchitecture(
            Map<String, Object> artifact,
            Map<String, Object> inventory,
            Map<String, Object> snapshot) {
        Map<String, Object> fleet = object(inventory.get("existingFleet"), "existingFleet");
        Map<String, Object> management = object(fleet.get("managementDomain"), "managementDomain");
        Map<String, Object> workload =
                object(inventory.get("candidateWorkloadDomain"), "candidateWorkloadDomain");
        Map<String, Object> targetRelease = object(snapshot.get("targetRelease"), "targetRelease");

        equal(artifact.get("sddcId"), workload.get("sddcId"), "sddcId");
        equal(artifact.get("workflowType"), targetRelease.get("installerWorkflowType"), "workflowType");
        equal(artifact.get("version"), targetRelease.get("vcfVersion"), "version");
        equal(artifact.get("vcfInstanceName"), fleet.get("fleetId"), "vcfInstanceName");
        Map<String, Object> installerValidation =
                object(workload.get("installerValidation"), "installerValidation");
        equal(artifact.get("skipEsxThumbprintValidation"),
                installerValidation.get("skipEsxThumbprintValidation"),
                "skipEsxThumbprintValidation");
        equal(artifact.get("skipGatewayPingValidation"),
                installerValidation.get("skipGatewayPingValidation"),
                "skipGatewayPingValidation");
        equal(artifact.get("xArchitectureType"), "BROWNFIELD_WORKLOAD_DOMAIN_CONVERGENCE",
                "xArchitectureType");
        equal(artifact.get("xFleetId"), fleet.get("fleetId"), "xFleetId");

        Map<String, Object> artifactManagement =
                object(artifact.get("xManagementDomain"), "xManagementDomain");
        equal(artifactManagement.get("domainId"), management.get("domainId"),
                "xManagementDomain.domainId");
        equal(artifactManagement.get("action"), management.get("requiredAction"),
                "xManagementDomain.action");

        Map<String, Map<String, Object>> componentsByType = new LinkedHashMap<>();
        Map<String, Map<String, Object>> componentsById = new LinkedHashMap<>();
        for (Object value : array(workload.get("components"), "workload components")) {
            Map<String, Object> component = object(value, "workload component");
            componentsByType.put(string(component.get("componentType"), "componentType"), component);
            componentsById.put(string(component.get("componentId"), "componentId"), component);
        }

        verifyInstallerSpec(artifact, workload, fleet, componentsByType);
        verifyPlan(artifact, snapshot, targetRelease, componentsByType, componentsById, management);
    }

    private static void verifyInstallerSpec(
            Map<String, Object> artifact,
            Map<String, Object> workload,
            Map<String, Object> fleet,
            Map<String, Map<String, Object>> components) {
        Map<String, Object> vc = components.get("VCENTER");
        Map<String, Object> vcSpec = object(artifact.get("vcenterSpec"), "vcenterSpec");
        equal(vcSpec.get("vcenterHostname"), vc.get("hostname"), "vcenterSpec.vcenterHostname");
        equal(vcSpec.get("rootVcenterPassword"), vc.get("rootPassword"),
                "vcenterSpec.rootVcenterPassword");
        equal(vcSpec.get("version"), vc.get("version"), "vcenterSpec.version");
        equal(vcSpec.get("useExistingDeployment"), Boolean.TRUE,
                "vcenterSpec.useExistingDeployment");
        equal(vcSpec.get("sslThumbprint"), vc.get("sslThumbprint"),
                "vcenterSpec.sslThumbprint");

        Map<String, Object> nsx = components.get("NSX");
        Map<String, Object> nsxSpec = object(artifact.get("nsxtSpec"), "nsxtSpec");
        equal(nsxSpec.get("vipFqdn"), nsx.get("vipFqdn"), "nsxtSpec.vipFqdn");
        equal(nsxSpec.get("rootNsxtManagerPassword"), nsx.get("rootPassword"),
                "nsxtSpec.rootNsxtManagerPassword");
        equal(nsxSpec.get("nsxtAdminPassword"), nsx.get("adminPassword"),
                "nsxtSpec.nsxtAdminPassword");
        equal(nsxSpec.get("nsxtAuditPassword"), nsx.get("auditPassword"),
                "nsxtSpec.nsxtAuditPassword");
        equal(nsxSpec.get("transportVlanId"), nsx.get("transportVlanId"),
                "nsxtSpec.transportVlanId");
        equal(nsxSpec.get("version"), nsx.get("version"), "nsxtSpec.version");
        equal(nsxSpec.get("useExistingDeployment"), Boolean.TRUE,
                "nsxtSpec.useExistingDeployment");
        equal(nsxSpec.get("sslThumbprint"), nsx.get("sslThumbprint"),
                "nsxtSpec.sslThumbprint");

        List<Object> expectedManagers = array(nsx.get("managerHostnames"), "managerHostnames");
        List<Object> actualManagers = array(nsxSpec.get("nsxtManagers"), "nsxtManagers");
        equal(actualManagers.size(), expectedManagers.size(), "nsxtManagers size");
        for (int i = 0; i < expectedManagers.size(); i++) {
            equal(object(actualManagers.get(i), "nsxtManager").get("hostname"),
                    expectedManagers.get(i), "nsxtManagers[" + i + "].hostname");
        }

        Map<String, Object> esxi = components.get("ESXI");
        List<Object> expectedHosts = array(esxi.get("hosts"), "inventory hosts");
        List<Object> actualHosts = array(artifact.get("hostSpecs"), "hostSpecs");
        equal(actualHosts.size(), expectedHosts.size(), "hostSpecs size");
        for (int i = 0; i < expectedHosts.size(); i++) {
            Map<String, Object> expected = object(expectedHosts.get(i), "inventory host");
            Map<String, Object> actual = object(actualHosts.get(i), "hostSpec");
            equal(actual.get("hostname"), expected.get("shortName"),
                    "hostSpecs[" + i + "].hostname");
            equal(actual.get("sslThumbprint"), expected.get("sslThumbprint"),
                    "hostSpecs[" + i + "].sslThumbprint");
        }

        Map<String, Object> dns = object(artifact.get("dnsSpec"), "dnsSpec");
        equal(dns, workload.get("dns"), "dnsSpec");
        equal(artifact.get("ntpServers"), workload.get("ntpServers"), "ntpServers");
        equal(artifact.get("networkSpecs"), workload.get("networks"), "networkSpecs");

        Map<String, Object> cluster = object(artifact.get("clusterSpec"), "clusterSpec");
        equal(cluster.get("datacenterName"), workload.get("datacenterName"),
                "clusterSpec.datacenterName");
        equal(cluster.get("clusterName"), workload.get("clusterName"),
                "clusterSpec.clusterName");

        Map<String, Object> datastore = object(artifact.get("datastoreSpec"), "datastoreSpec");
        equal(datastore.get("existingDatastoreName"), components.get("VSAN").get("datastoreName"),
                "datastoreSpec.existingDatastoreName");
        equal(artifact.get("managementPoolName"), workload.get("managementPoolName"),
                "managementPoolName");
        equal(artifact.get("vcfInstanceName"), fleet.get("fleetId"), "vcfInstanceName");
    }

    private static void verifyPlan(
            Map<String, Object> artifact,
            Map<String, Object> snapshot,
            Map<String, Object> targetRelease,
            Map<String, Map<String, Object>> inventoryByType,
            Map<String, Map<String, Object>> inventoryById,
            Map<String, Object> management) {
        List<Object> plan = array(artifact.get("xMigrationPlan"), "xMigrationPlan");
        Map<String, Object> rules = object(snapshot.get("planRules"), "planRules");
        List<Object> lifecycleRules = array(rules.get("lifecycleOrder"), "lifecycleOrder");
        equal(plan.size(), 2 + lifecycleRules.size(), "migration step count");

        Set<String> stepIds = new HashSet<>();
        for (int i = 0; i < plan.size(); i++) {
            Map<String, Object> step = object(plan.get(i), "migration step");
            equal(step.get("order"), Long.valueOf(i + 1L), "contiguous migration order");
            String stepId = string(step.get("stepId"), "stepId");
            check(stepIds.add(stepId), "stepId must be unique: " + stepId);
        }

        Map<String, Map<String, Object>> compatibility = new LinkedHashMap<>();
        for (Object value : array(snapshot.get("componentCompatibility"),
                "componentCompatibility")) {
            Map<String, Object> row = object(value, "compatibility row");
            compatibility.put(string(row.get("componentType"), "compatibility componentType"), row);
        }
        equal(compatibility.keySet(), inventoryByType.keySet(),
                "compatibility snapshot component coverage");

        Map<String, Object> recoveryRule =
                object(rules.get("recoveryConvergence"), "recoveryConvergence");
        verifyTransitionStep(
                object(plan.get(0), "recovery step"),
                groupTypes(compatibility, string(recoveryRule.get("lifecycleGroup"), "lifecycleGroup")),
                inventoryByType,
                compatibility,
                string(recoveryRule.get("method"), "recovery method"),
                array(recoveryRule.get("technicalGates"), "recovery gates"),
                string(targetRelease.get("targetManagementState"), "targetManagementState"));

        Map<String, Object> onboardingRule = object(rules.get("onboarding"), "onboarding");
        check(inventoryByType.containsKey("NSX"), "snapshot onboarding condition NSX_PRESENT not met");
        verifyOnboardingStep(
                object(plan.get(1), "onboarding step"),
                stringList(onboardingRule.get("componentTypes"), "onboarding componentTypes"),
                inventoryByType,
                string(onboardingRule.get("method"), "onboarding method"),
                array(onboardingRule.get("technicalGates"), "onboarding gates"),
                string(targetRelease.get("targetManagementState"), "targetManagementState"));

        for (int i = 0; i < lifecycleRules.size(); i++) {
            Map<String, Object> rule = object(lifecycleRules.get(i), "lifecycle rule");
            String group = string(rule.get("lifecycleGroup"), "lifecycleGroup");
            verifyTransitionStep(
                    object(plan.get(i + 2), "lifecycle step"),
                    groupTypes(compatibility, group),
                    inventoryByType,
                    compatibility,
                    string(rule.get("method"), "lifecycle method"),
                    array(rule.get("technicalGates"), "lifecycle gates"),
                    string(targetRelease.get("targetManagementState"), "targetManagementState"));
        }

        Set<String> finalCoverage = new HashSet<>();
        collectIds(object(plan.get(0), "recovery step"), finalCoverage);
        for (int i = 2; i < plan.size(); i++) {
            collectIds(object(plan.get(i), "lifecycle step"), finalCoverage);
        }
        equal(finalCoverage, inventoryById.keySet(), "final target coverage");

        Set<String> managementIds = new HashSet<>();
        for (Object value : array(management.get("components"), "management components")) {
            managementIds.add(string(object(value, "management component").get("componentId"),
                    "management componentId"));
        }
        for (Object stepValue : plan) {
            for (Object componentValue : array(object(stepValue, "step").get("components"),
                    "step components")) {
                String id = string(object(componentValue, "planned component").get("componentId"),
                        "planned componentId");
                check(!managementIds.contains(id), "management-domain component is in plan: " + id);
            }
        }
    }

    private static void verifyTransitionStep(
            Map<String, Object> step,
            Set<String> expectedTypes,
            Map<String, Map<String, Object>> inventory,
            Map<String, Map<String, Object>> compatibility,
            String expectedMethod,
            List<Object> expectedGates,
            String targetManagementState) {
        equal(step.get("method"), expectedMethod, "migration method");
        equalSet(step.get("gates"), expectedGates, "technical gates");
        Map<String, Map<String, Object>> planned = componentsById(step);
        Set<String> expectedIds = new HashSet<>();
        for (String type : expectedTypes) {
            Map<String, Object> source = inventory.get(type);
            Map<String, Object> row = compatibility.get(type);
            String id = string(source.get("componentId"), "componentId");
            expectedIds.add(id);
            Map<String, Object> actual = planned.get(id);
            check(actual != null, "missing planned component " + id);
            equal(actual.get("sourceProduct"), row.get("sourceProduct"), id + " sourceProduct");
            equal(actual.get("sourceVersion"), row.get("sourceVersion"), id + " sourceVersion");
            equal(actual.get("targetProduct"), row.get("targetProduct"), id + " targetProduct");
            equal(actual.get("targetVersion"), row.get("targetVersion"), id + " targetVersion");
            List<Object> actualPath = array(actual.get("upgradePath"), id + " upgradePath");
            check(array(row.get("allowedUpgradePaths"), "allowedUpgradePaths").contains(actualPath),
                    id + " upgradePath is not in the pinned compatibility snapshot");
            equal(actual.get("targetManagementState"), targetManagementState,
                    id + " targetManagementState");
        }
        equal(planned.keySet(), expectedIds, "step component group");
    }

    private static void verifyOnboardingStep(
            Map<String, Object> step,
            List<String> expectedTypes,
            Map<String, Map<String, Object>> inventory,
            String expectedMethod,
            List<Object> expectedGates,
            String targetManagementState) {
        equal(step.get("method"), expectedMethod, "onboarding method");
        equalSet(step.get("gates"), expectedGates, "onboarding gates");
        Map<String, Map<String, Object>> planned = componentsById(step);
        Set<String> expectedIds = new HashSet<>();
        for (String type : expectedTypes) {
            Map<String, Object> source = inventory.get(type);
            String id = string(source.get("componentId"), "componentId");
            expectedIds.add(id);
            Map<String, Object> actual = planned.get(id);
            check(actual != null, "onboarding missing component " + id);
            equal(actual.get("sourceProduct"), source.get("product"), id + " onboarding sourceProduct");
            equal(actual.get("targetProduct"), source.get("product"), id + " onboarding targetProduct");
            equal(actual.get("sourceVersion"), source.get("version"), id + " onboarding sourceVersion");
            equal(actual.get("targetVersion"), source.get("version"), id + " onboarding targetVersion");
            equal(actual.get("upgradePath"), List.of(source.get("version")),
                    id + " onboarding upgradePath");
            equal(actual.get("targetManagementState"), targetManagementState,
                    id + " onboarding targetManagementState");
        }
        equal(planned.keySet(), expectedIds, "onboarding component group");
    }

    private static Set<String> groupTypes(
            Map<String, Map<String, Object>> compatibility, String group) {
        Set<String> result = new HashSet<>();
        for (Map.Entry<String, Map<String, Object>> entry : compatibility.entrySet()) {
            if (group.equals(entry.getValue().get("lifecycleGroup"))) {
                result.add(entry.getKey());
            }
        }
        check(!result.isEmpty(), "empty lifecycle group " + group);
        return result;
    }

    private static Map<String, Map<String, Object>> componentsById(Map<String, Object> step) {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        for (Object value : array(step.get("components"), "step components")) {
            Map<String, Object> component = object(value, "planned component");
            String id = string(component.get("componentId"), "planned componentId");
            check(result.put(id, component) == null, "duplicate component in step: " + id);
        }
        return result;
    }

    private static void collectIds(Map<String, Object> step, Set<String> ids) {
        for (String id : componentsById(step).keySet()) {
            check(ids.add(id), "component has more than one final transition: " + id);
        }
    }

    private static List<String> stringList(Object value, String label) {
        List<String> result = new ArrayList<>();
        for (Object item : array(value, label)) {
            result.add(string(item, label + " item"));
        }
        return result;
    }

    private static void equalSet(Object actualValue, List<Object> expected, String label) {
        List<Object> actual = array(actualValue, label);
        equal(new HashSet<>(actual), new HashSet<>(expected), label);
        equal(actual.size(), expected.size(), label + " cardinality");
    }

    private static void assertSha(Path path, String expected) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(path));
        StringBuilder hex = new StringBuilder();
        for (byte value : digest) {
            hex.append(String.format("%02x", value));
        }
        equal(hex.toString(), expected, path + " SHA-256");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String label) {
        if (!(value instanceof Map<?, ?>)) {
            throw new AssertionError(label + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String label) {
        if (!(value instanceof List<?>)) {
            throw new AssertionError(label + " must be an array");
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String label) {
        if (!(value instanceof String)) {
            throw new AssertionError(label + " must be a string");
        }
        return (String) value;
    }

    private static void equal(Object actual, Object expected, String label) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(label + " expected " + expected + " but was " + actual);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final class SchemaValidator {
        private SchemaValidator() {
        }

        static void validate(
                Object instance,
                Map<String, Object> schema,
                Map<String, Object> root,
                String path,
                List<String> errors) {
            if (schema.containsKey("$ref")) {
                validate(instance, resolve(root, string(schema.get("$ref"), "$ref")), root, path, errors);
                return;
            }
            if (schema.containsKey("allOf")) {
                for (Object part : array(schema.get("allOf"), "allOf")) {
                    validate(instance, object(part, "allOf schema"), root, path, errors);
                }
            }
            if (schema.containsKey("const") && !schema.get("const").equals(instance)) {
                errors.add(path + " must equal " + schema.get("const"));
            }
            if (schema.containsKey("enum")
                    && !array(schema.get("enum"), "enum").contains(instance)) {
                errors.add(path + " is not an allowed enum value");
            }

            String type = schema.get("type") instanceof String ? (String) schema.get("type") : null;
            if (type != null && !matchesType(instance, type)) {
                errors.add(path + " must be " + type);
                return;
            }

            if (instance instanceof Map<?, ?>) {
                Map<String, Object> value = object(instance, path);
                if (schema.containsKey("required")) {
                    for (Object keyValue : array(schema.get("required"), "required")) {
                        String key = string(keyValue, "required key");
                        if (!value.containsKey(key)) {
                            errors.add(path + " is missing required property " + key);
                        }
                    }
                }
                Map<String, Object> properties = schema.get("properties") instanceof Map<?, ?>
                        ? object(schema.get("properties"), "properties") : Map.of();
                for (Map.Entry<String, Object> entry : properties.entrySet()) {
                    if (value.containsKey(entry.getKey())) {
                        validate(value.get(entry.getKey()), object(entry.getValue(), "property schema"),
                                root, path + "." + entry.getKey(), errors);
                    }
                }
                if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    for (String key : value.keySet()) {
                        if (!properties.containsKey(key)) {
                            errors.add(path + " has unexpected property " + key);
                        }
                    }
                }
            }

            if (instance instanceof List<?>) {
                List<Object> value = array(instance, path);
                if (schema.containsKey("minItems")
                        && value.size() < number(schema.get("minItems")).intValue()) {
                    errors.add(path + " has fewer than minItems");
                }
                if (schema.containsKey("maxItems")
                        && value.size() > number(schema.get("maxItems")).intValue()) {
                    errors.add(path + " has more than maxItems");
                }
                if (Boolean.TRUE.equals(schema.get("uniqueItems"))
                        && new HashSet<>(value).size() != value.size()) {
                    errors.add(path + " items must be unique");
                }
                if (schema.get("items") instanceof Map<?, ?>) {
                    Map<String, Object> itemSchema = object(schema.get("items"), "items schema");
                    for (int i = 0; i < value.size(); i++) {
                        validate(value.get(i), itemSchema, root, path + "[" + i + "]", errors);
                    }
                }
            }

            if (instance instanceof String) {
                String value = (String) instance;
                if (schema.containsKey("minLength")
                        && value.length() < number(schema.get("minLength")).intValue()) {
                    errors.add(path + " is shorter than minLength");
                }
                if (schema.containsKey("maxLength")
                        && value.length() > number(schema.get("maxLength")).intValue()) {
                    errors.add(path + " is longer than maxLength");
                }
                if (schema.containsKey("pattern")
                        && !Pattern.compile(string(schema.get("pattern"), "pattern"))
                                .matcher(value).find()) {
                    errors.add(path + " does not match pattern");
                }
            }

            if (instance instanceof Number) {
                BigDecimal value = new BigDecimal(instance.toString());
                if (schema.containsKey("minimum")
                        && value.compareTo(number(schema.get("minimum"))) < 0) {
                    errors.add(path + " is less than minimum");
                }
                if (schema.containsKey("maximum")
                        && value.compareTo(number(schema.get("maximum"))) > 0) {
                    errors.add(path + " is greater than maximum");
                }
            }
        }

        private static boolean matchesType(Object value, String type) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "integer" -> value instanceof Long || value instanceof Integer;
                case "number" -> value instanceof Number;
                case "boolean" -> value instanceof Boolean;
                case "null" -> value == null;
                default -> true;
            };
        }

        private static BigDecimal number(Object value) {
            if (!(value instanceof Number)) {
                throw new AssertionError("schema numeric keyword must be a number");
            }
            return new BigDecimal(value.toString());
        }

        private static Map<String, Object> resolve(Map<String, Object> root, String ref) {
            if (!ref.startsWith("#/")) {
                throw new AssertionError("only local schema refs are supported: " + ref);
            }
            Object value = root;
            for (String raw : ref.substring(2).split("/")) {
                String token = raw.replace("~1", "/").replace("~0", "~");
                value = object(value, "schema ref segment").get(token);
                if (value == null) {
                    throw new AssertionError("unresolved schema ref " + ref);
                }
            }
            return object(value, "resolved schema " + ref);
        }
    }

    private static final class Json {
        private final String text;
        private int index;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            if (text == null) {
                throw new AssertionError("ArchitectureClient returned null");
            }
            Json parser = new Json(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.index != text.length()) {
                throw parser.error("trailing content");
            }
            return value;
        }

        private Object value() {
            whitespace();
            if (index >= text.length()) {
                throw error("expected JSON value");
            }
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
            expect('{');
            LinkedHashMap<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                String key = stringValue();
                whitespace();
                expect(':');
                if (result.put(key, value()) != null) {
                    throw error("duplicate object key " + key);
                }
                whitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> arrayValue() {
            expect('[');
            ArrayList<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String stringValue() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < text.length()) {
                char c = text.charAt(index++);
                if (c == '"') {
                    return result.toString();
                }
                if (c == '\\') {
                    if (index >= text.length()) {
                        throw error("unterminated escape");
                    }
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
                                throw error("short unicode escape");
                            }
                            result.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                            index += 4;
                        }
                        default -> throw error("invalid escape");
                    }
                } else {
                    if (c < 0x20) {
                        throw error("control character in string");
                    }
                    result.append(c);
                }
            }
            throw error("unterminated string");
        }

        private Object numberValue() {
            int start = index;
            if (take('-')) {
                // sign consumed
            }
            digits();
            boolean decimal = false;
            if (take('.')) {
                decimal = true;
                digits();
            }
            if (index < text.length() && (text.charAt(index) == 'e' || text.charAt(index) == 'E')) {
                decimal = true;
                index++;
                if (index < text.length() && (text.charAt(index) == '+' || text.charAt(index) == '-')) {
                    index++;
                }
                digits();
            }
            String raw = text.substring(start, index);
            try {
                return decimal ? new BigDecimal(raw) : Long.valueOf(raw);
            } catch (NumberFormatException exception) {
                throw error("invalid number");
            }
        }

        private void digits() {
            int start = index;
            while (index < text.length() && Character.isDigit(text.charAt(index))) {
                index++;
            }
            if (start == index) {
                throw error("expected digit");
            }
        }

        private Object literal(String expected, Object value) {
            if (!text.startsWith(expected, index)) {
                throw error("invalid literal");
            }
            index += expected.length();
            return value;
        }

        private void whitespace() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
                index++;
            }
        }

        private boolean take(char expected) {
            if (index < text.length() && text.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                throw error("expected '" + expected + "'");
            }
        }

        private AssertionError error(String message) {
            return new AssertionError("invalid JSON at character " + index + ": " + message);
        }
    }
}
