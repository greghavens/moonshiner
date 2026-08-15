import java.io.IOException;
import java.math.BigDecimal;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** Protected, offline acceptance harness for the migration architecture artifact. */
public final class TestMain {
    private static final Path INVENTORY = Path.of("estate-inventory.json");
    private static final Path SNAPSHOT = Path.of("compatibility-snapshot.json");
    private static final Path SCHEMA = Path.of("migration-plan.schema.json");
    private static final Path OUTPUT = Path.of("migration-plan.json");

    public static void main(String[] args) throws Exception {
        Files.deleteIfExists(OUTPUT);
        MigrationPlanClient.main(new String[] {
                INVENTORY.toString(), SNAPSHOT.toString(), OUTPUT.toString()
        });
        check(Files.isRegularFile(OUTPUT), "client did not create migration-plan.json");

        // Artifact schema validation is intentionally the first verification phase.
        Object schema = Json.parse(Files.readString(SCHEMA, StandardCharsets.UTF_8));
        Object artifact = Json.parse(Files.readString(OUTPUT, StandardCharsets.UTF_8));
        List<String> schemaErrors = new SchemaValidator(schema).validate(artifact);
        if (!schemaErrors.isEmpty()) {
            throw new AssertionError("artifact schema validation failed first: "
                    + String.join(" | ", schemaErrors));
        }
        System.out.println("schema validation passed");

        // Only after the schema succeeds may the pinned fixture and authority be inspected.
        Map<String, Object> plan = object(artifact, "artifact");
        Map<String, Object> inventory = object(
                Json.parse(Files.readString(INVENTORY, StandardCharsets.UTF_8)), "inventory");
        Map<String, Object> snapshot = object(
                Json.parse(Files.readString(SNAPSHOT, StandardCharsets.UTF_8)), "snapshot");

        verifyIdentity(plan, inventory, snapshot);
        verifyResearch(plan);
        verifyMappings(plan, inventory, snapshot);
        verifyLifecycle(plan, inventory, snapshot);
        verifyArchitecture(plan, inventory, snapshot);
        verifyContent(plan, inventory, snapshot);
        verifySteps(plan, inventory, snapshot);

        System.out.println("VCF migration architecture verified");
    }

    private static void verifyIdentity(Map<String, Object> plan,
                                       Map<String, Object> inventory,
                                       Map<String, Object> snapshot) {
        equal("1.0", string(plan.get("schemaVersion"), "schemaVersion"), "schema version");
        equal(string(inventory.get("inventoryId"), "inventory.inventoryId"),
                string(plan.get("inventoryId"), "inventoryId"), "inventory id");
        equal(string(snapshot.get("snapshotId"), "snapshot.snapshotId"),
                string(plan.get("snapshotId"), "snapshotId"), "snapshot id");
        equal(string(inventory.get("targetVcfVersion"), "inventory.targetVcfVersion"),
                string(plan.get("targetVersion"), "targetVersion"), "inventory target version");
        equal(string(snapshot.get("targetVersion"), "snapshot.targetVersion"),
                string(plan.get("targetVersion"), "targetVersion"), "snapshot target version");
    }

    private static void verifyMappings(Map<String, Object> plan,
                                       Map<String, Object> inventory,
                                       Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> products = index(
                array(inventory.get("sourceProducts"), "inventory.sourceProducts"), "productId");
        Map<String, Map<String, Object>> rules = index(
                array(snapshot.get("sourceRules"), "snapshot.sourceRules"), "productId");
        Map<String, Map<String, Object>> mappings = index(
                array(plan.get("sourceMappings"), "sourceMappings"), "productId");

        equal(products.keySet(), rules.keySet(), "snapshot source-rule coverage");
        equal(products.keySet(), mappings.keySet(), "artifact source-mapping coverage");

        for (String productId : products.keySet()) {
            Map<String, Object> product = products.get(productId);
            Map<String, Object> rule = rules.get(productId);
            Map<String, Object> mapping = mappings.get(productId);
            equal(string(product.get("productName"), productId + ".productName"),
                    string(mapping.get("sourceProductName"), productId + ".sourceProductName"),
                    productId + " source name");
            equal(string(product.get("version"), productId + ".version"),
                    string(mapping.get("sourceVersion"), productId + ".sourceVersion"),
                    productId + " source version");
            equal(string(rule.get("targetProduct"), productId + ".targetProduct"),
                    string(mapping.get("targetProductName"), productId + ".targetProductName"),
                    productId + " target product");
            equal(string(rule.get("targetVersion"), productId + ".targetVersion"),
                    string(mapping.get("targetVersion"), productId + ".mapping.targetVersion"),
                    productId + " target version");
            equal(string(rule.get("requiredStrategy"), productId + ".requiredStrategy"),
                    string(mapping.get("strategy"), productId + ".strategy"),
                    productId + " migration strategy");
            equal(string(rule.get("compatibilityBoundary"), productId + ".compatibilityBoundary"),
                    string(mapping.get("compatibilityDecision"), productId + ".compatibilityDecision"),
                    productId + " compatibility decision");
            equal(stringSet(rule.get("targetComponentIds"), productId + ".rule.components"),
                    stringSet(mapping.get("targetComponentIds"), productId + ".mapping.components"),
                    productId + " target components");
            Set<String> forbidden = stringSet(rule.get("forbiddenStrategies"), productId + ".forbidden");
            check(!forbidden.contains(string(mapping.get("strategy"), productId + ".strategy")),
                    productId + " uses a forbidden migration strategy");
        }
    }

    private static void verifyResearch(Map<String, Object> plan) {
        Map<String, Object> research = object(plan.get("research"), "research");
        List<Map<String, Object>> sources = objectList(
                research.get("sourcesConsulted"), "research.sourcesConsulted");
        Set<String> urls = new LinkedHashSet<>();
        StringBuilder usedClaims = new StringBuilder();
        for (int i = 0; i < sources.size(); i++) {
            Map<String, Object> source = sources.get(i);
            String path = "research.sourcesConsulted[" + i + "]";
            String url = string(source.get("url"), path + ".url");
            check(urls.add(url), "research must not repeat a consulted URL");
            URI uri;
            try {
                uri = URI.create(url);
            } catch (IllegalArgumentException e) {
                throw new AssertionError(path + ".url is not a valid URI");
            }
            String host = uri.getHost();
            check("https".equalsIgnoreCase(uri.getScheme()) && host != null,
                    path + ".url must be an absolute HTTPS URL");
            host = host.toLowerCase(Locale.ROOT);
            check(host.equals("broadcom.com") || host.endsWith(".broadcom.com"),
                    path + ".url must identify Broadcom-published material");
            try {
                LocalDate.parse(string(source.get("accessedOn"), path + ".accessedOn"));
            } catch (RuntimeException e) {
                throw new AssertionError(path + ".accessedOn must be a valid ISO calendar date");
            }
            for (Object claim : array(source.get("claims"), path + ".claims")) {
                usedClaims.append(' ').append(string(claim, path + ".claims[]"));
            }
        }
        String claims = usedClaims.toString().toLowerCase(Locale.ROOT);
        check(containsAny(claims, "upgrade", "migration", "transition"),
                "research claims must cover supported migration paths");
        check(containsAny(claims, "content", "import", "historical", "dashboard", "transfer"),
                "research claims must cover content compatibility");
        check(containsAny(claims, "sizing", "capacity", "resource", "vcpu", "memory", "objects", "metrics", "eps", "ingestion"),
                "research claims must cover sizing");
        check(containsAny(claims, "support", "eogs", "lifecycle"),
                "research claims must cover support boundaries");
    }

    private static boolean containsAny(String text, String... terms) {
        for (String term : terms) {
            if (text.contains(term)) return true;
        }
        return false;
    }

    private static void verifyLifecycle(Map<String, Object> plan,
                                        Map<String, Object> inventory,
                                        Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> products = index(
                array(inventory.get("sourceProducts"), "inventory.sourceProducts"), "productId");
        Map<String, Map<String, Object>> expected = index(
                array(snapshot.get("lifecycleBoundaries"), "snapshot.lifecycleBoundaries"), "productId");
        Map<String, Map<String, Object>> actual = index(
                array(plan.get("lifecycleBoundaries"), "lifecycleBoundaries"), "productId");
        equal(products.keySet(), actual.keySet(), "lifecycle coverage");
        equal(expected.keySet(), actual.keySet(), "snapshot lifecycle coverage");

        for (String id : expected.keySet()) {
            Map<String, Object> exp = expected.get(id);
            Map<String, Object> got = actual.get(id);
            for (String field : List.of("productName", "version", "boundaryType", "date")) {
                equal(string(exp.get(field), id + ".expected." + field),
                        string(got.get(field), id + ".actual." + field),
                        id + " lifecycle " + field);
            }
            check(!string(got.get("designImpact"), id + ".designImpact").isBlank(),
                    id + " lifecycle boundary needs a design impact");
        }
    }

    private static void verifyArchitecture(Map<String, Object> plan,
                                           Map<String, Object> inventory,
                                           Map<String, Object> snapshot) {
        Map<String, Object> architecture = object(plan.get("architecture"), "architecture");
        Map<String, Map<String, Object>> expected = index(
                array(snapshot.get("componentRules"), "snapshot.componentRules"), "componentId");
        Map<String, Map<String, Object>> actual = index(
                array(architecture.get("components"), "architecture.components"), "componentId");
        equal(expected.keySet(), actual.keySet(), "target component coverage");

        Set<String> placedAtWorkload = new HashSet<>();
        Set<String> targetProducts = new HashSet<>();
        for (String id : expected.keySet()) {
            Map<String, Object> exp = expected.get(id);
            Map<String, Object> got = actual.get(id);
            for (String field : List.of("targetProduct", "targetVersion", "role")) {
                equal(string(exp.get(field), id + ".expected." + field),
                        string(got.get(field), id + ".actual." + field),
                        id + " " + field);
            }
            equal(exp.get("placement"), got.get("placement"), id + " placement");
            equal(exp.get("sizing"), got.get("sizing"), id + " sizing");
            equal(capacityByMetric(exp.get("capacity"), id + ".expected.capacity"),
                    capacityByMetric(got.get("capacity"), id + ".actual.capacity"),
                    id + " capacity basis");
            for (Map<String, Object> cap : objectList(got.get("capacity"), id + ".capacity")) {
                long required = integer(cap.get("required"), id + ".capacity.required");
                long provided = integer(cap.get("provided"), id + ".capacity.provided");
                check(provided >= required, id + " capacity is below demand for " + cap.get("metric"));
            }
            Map<String, Object> placement = object(got.get("placement"), id + ".placement");
            verifyPlacementExists(placement, inventory, id);
            String domain = string(placement.get("domainId"), id + ".placement.domainId");
            if (!domain.equals("mgmt-domain")) {
                placedAtWorkload.add(id);
            }
            targetProducts.add(string(got.get("targetProduct"), id + ".targetProduct"));
        }
        check(placedAtWorkload.contains("ops-ucp-atl") && placedAtWorkload.contains("ops-ucp-den"),
                "both workload-domain collector placements must be explicit");
        check(targetProducts.containsAll(Set.of(
                        "VCF Operations", "VCF Automation", "VCF Operations for Logs")),
                "architecture must size and place all three target products");
    }

    private static void verifyPlacementExists(Map<String, Object> placement,
                                              Map<String, Object> inventory,
                                              String componentId) {
        String siteId = string(placement.get("siteId"), componentId + ".siteId");
        String domainId = string(placement.get("domainId"), componentId + ".domainId");
        String clusterId = string(placement.get("clusterId"), componentId + ".clusterId");
        Set<String> fds = stringSet(placement.get("failureDomains"), componentId + ".failureDomains");
        for (Map<String, Object> site : objectList(inventory.get("sites"), "inventory.sites")) {
            if (!siteId.equals(site.get("siteId"))) continue;
            for (Map<String, Object> domain : objectList(site.get("domains"), siteId + ".domains")) {
                if (!domainId.equals(domain.get("domainId"))) continue;
                for (Map<String, Object> cluster : objectList(domain.get("clusters"), domainId + ".clusters")) {
                    if (!clusterId.equals(cluster.get("clusterId"))) continue;
                    Set<String> available = stringSet(cluster.get("failureDomains"), clusterId + ".failureDomains");
                    check(available.containsAll(fds), componentId + " names unavailable failure domains");
                    return;
                }
            }
        }
        throw new AssertionError(componentId + " placement is not present in the estate inventory");
    }

    private static void verifyContent(Map<String, Object> plan,
                                      Map<String, Object> inventory,
                                      Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> products = index(
                array(inventory.get("sourceProducts"), "inventory.sourceProducts"), "productId");
        Map<String, Map<String, Object>> expected = index(
                array(snapshot.get("contentRules"), "snapshot.contentRules"), "contentId");
        Map<String, Map<String, Object>> actual = index(
                array(plan.get("contentDisposition"), "contentDisposition"), "contentId");

        Map<String, Map<String, Object>> inventoryContent = new LinkedHashMap<>();
        Map<String, String> contentOwners = new LinkedHashMap<>();
        for (Map.Entry<String, Map<String, Object>> productEntry : products.entrySet()) {
            for (Map<String, Object> item : objectList(
                    productEntry.getValue().get("content"), productEntry.getKey() + ".content")) {
                String contentId = string(item.get("contentId"), "contentId");
                check(inventoryContent.putIfAbsent(contentId, item) == null,
                        "duplicate inventory content id " + contentId);
                contentOwners.put(contentId, productEntry.getKey());
            }
        }
        equal(inventoryContent.keySet(), expected.keySet(), "snapshot content-rule coverage");
        equal(inventoryContent.keySet(), actual.keySet(), "artifact content-disposition coverage");

        Set<String> decisions = new HashSet<>();
        for (String contentId : inventoryContent.keySet()) {
            String ownerId = contentOwners.get(contentId);
            Map<String, Object> owner = products.get(ownerId);
            Map<String, Object> item = inventoryContent.get(contentId);
            Map<String, Object> exp = expected.get(contentId);
            Map<String, Object> got = actual.get(contentId);
            equal(ownerId, string(got.get("sourceProductId"), contentId + ".sourceProductId"),
                    contentId + " source product id");
            equal(string(owner.get("productName"), ownerId + ".productName"),
                    string(got.get("sourceProductName"), contentId + ".sourceProductName"),
                    contentId + " source product name");
            equal(string(owner.get("version"), ownerId + ".version"),
                    string(got.get("sourceVersion"), contentId + ".sourceVersion"),
                    contentId + " source version");
            equal(string(item.get("kind"), contentId + ".kind"),
                    string(got.get("kind"), contentId + ".actual.kind"), contentId + " kind");
            for (String field : List.of("decision", "targetComponentId", "method")) {
                equal(string(exp.get(field), contentId + ".expected." + field),
                        string(got.get(field), contentId + ".actual." + field),
                        contentId + " " + field);
            }
            decisions.add(string(got.get("decision"), contentId + ".decision"));
        }
        equal(Set.of("carry", "recreate", "replace", "abandon"), decisions,
                "content decision categories");
    }

    private static void verifySteps(Map<String, Object> plan,
                                    Map<String, Object> inventory,
                                    Map<String, Object> snapshot) {
        Map<String, Map<String, Object>> products = index(
                array(inventory.get("sourceProducts"), "inventory.sourceProducts"), "productId");
        List<Map<String, Object>> expected = objectList(
                snapshot.get("requiredSteps"), "snapshot.requiredSteps");
        List<Map<String, Object>> actual = objectList(plan.get("migrationSteps"), "migrationSteps");
        equal(expected.size(), actual.size(), "migration step count");

        long previous = Long.MIN_VALUE;
        Set<String> referencedContent = new LinkedHashSet<>();
        for (int i = 0; i < expected.size(); i++) {
            Map<String, Object> exp = expected.get(i);
            Map<String, Object> got = actual.get(i);
            String stepId = string(exp.get("stepId"), "expected step id");
            equal(stepId, string(got.get("stepId"), "actual step id"), "step order at index " + i);
            long sequence = integer(got.get("sequence"), stepId + ".sequence");
            equal(integer(exp.get("sequence"), stepId + ".expected.sequence"), sequence,
                    stepId + " sequence");
            check(sequence > previous, "migration step sequence must be strictly increasing");
            previous = sequence;

            Set<String> expectedSourceIds = stringSet(exp.get("sourceProductIds"), stepId + ".expected.sources");
            Set<String> actualSourceIds = new LinkedHashSet<>();
            for (Map<String, Object> source : objectList(got.get("sourceProducts"), stepId + ".sourceProducts")) {
                String productId = string(source.get("productId"), stepId + ".productId");
                check(actualSourceIds.add(productId), stepId + " repeats source product " + productId);
                Map<String, Object> inv = products.get(productId);
                check(inv != null, stepId + " names unknown source product " + productId);
                equal(string(inv.get("productName"), productId + ".productName"),
                        string(source.get("productName"), stepId + ".source.productName"),
                        stepId + " source name for " + productId);
                equal(string(inv.get("version"), productId + ".version"),
                        string(source.get("version"), stepId + ".source.version"),
                        stepId + " source version for " + productId);
            }
            equal(expectedSourceIds, actualSourceIds, stepId + " source products");
            equal(stringSet(exp.get("targetComponentIds"), stepId + ".expected.targets"),
                    stringSet(got.get("targetComponentIds"), stepId + ".actual.targets"),
                    stepId + " target components");

            List<Map<String, Object>> gates = objectList(got.get("gates"), stepId + ".gates");
            Set<String> gateIds = new LinkedHashSet<>();
            Set<String> entryGateIds = new LinkedHashSet<>();
            Set<String> exitGateIds = new LinkedHashSet<>();
            for (Map<String, Object> gate : gates) {
                String gateId = string(gate.get("gateId"), stepId + ".gateId");
                check(gateIds.add(gateId), stepId + " repeats a gate id");
                String phase = string(gate.get("phase"), stepId + ".gate.phase");
                (phase.equals("entry") ? entryGateIds : exitGateIds).add(gateId);
            }
            Set<String> requiredGateIds = stringSet(
                    exp.get("requiredGateIds"), stepId + ".requiredGates");
            Set<String> requiredEntryGateIds = stringSet(
                    exp.get("requiredEntryGateIds"), stepId + ".requiredEntryGates");
            Set<String> requiredExitGateIds = stringSet(
                    exp.get("requiredExitGateIds"), stepId + ".requiredExitGates");
            Set<String> overlap = new HashSet<>(requiredEntryGateIds);
            overlap.retainAll(requiredExitGateIds);
            check(overlap.isEmpty(), stepId + " snapshot assigns a gate to both phases");
            Set<String> classifiedGateIds = new LinkedHashSet<>(requiredEntryGateIds);
            classifiedGateIds.addAll(requiredExitGateIds);
            equal(requiredGateIds, classifiedGateIds, stepId + " snapshot gate phase coverage");
            equal(requiredGateIds, gateIds, stepId + " technical gates");
            equal(requiredEntryGateIds, entryGateIds, stepId + " entry gates");
            equal(requiredExitGateIds, exitGateIds, stepId + " exit gates");
            referencedContent.addAll(stringSet(got.get("contentItemIds"), stepId + ".contentItemIds"));
        }

        Set<String> inventoryContentIds = new LinkedHashSet<>();
        for (Map<String, Object> product : products.values()) {
            for (Map<String, Object> item : objectList(product.get("content"), "product.content")) {
                inventoryContentIds.add(string(item.get("contentId"), "contentId"));
            }
        }
        equal(inventoryContentIds, referencedContent, "content coverage across migration steps");
    }

    private static Map<String, Map<String, Object>> capacityByMetric(Object value, String path) {
        return index(array(value, path), "metric");
    }

    private static Map<String, Map<String, Object>> index(List<Object> values, String key) {
        Map<String, Map<String, Object>> indexed = new LinkedHashMap<>();
        for (int i = 0; i < values.size(); i++) {
            Map<String, Object> value = object(values.get(i), key + "[" + i + "]");
            String id = string(value.get(key), key + "[" + i + "]." + key);
            check(indexed.putIfAbsent(id, value) == null, "duplicate " + key + " " + id);
        }
        return indexed;
    }

    private static List<Map<String, Object>> objectList(Object value, String path) {
        List<Map<String, Object>> result = new ArrayList<>();
        List<Object> values = array(value, path);
        for (int i = 0; i < values.size(); i++) {
            result.add(object(values.get(i), path + "[" + i + "]"));
        }
        return result;
    }

    private static Set<String> stringSet(Object value, String path) {
        Set<String> result = new LinkedHashSet<>();
        List<Object> values = array(value, path);
        for (int i = 0; i < values.size(); i++) {
            String item = string(values.get(i), path + "[" + i + "]");
            check(result.add(item), path + " contains duplicate " + item);
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value, String path) {
        if (!(value instanceof Map<?, ?>)) {
            throw new AssertionError(path + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> array(Object value, String path) {
        if (!(value instanceof List<?>)) {
            throw new AssertionError(path + " must be an array");
        }
        return (List<Object>) value;
    }

    private static String string(Object value, String path) {
        if (!(value instanceof String text)) {
            throw new AssertionError(path + " must be a string");
        }
        return text;
    }

    private static long integer(Object value, String path) {
        if (!(value instanceof BigDecimal number) || number.stripTrailingZeros().scale() > 0) {
            throw new AssertionError(path + " must be an integer");
        }
        try {
            return number.longValueExact();
        } catch (ArithmeticException e) {
            throw new AssertionError(path + " is outside the supported integer range");
        }
    }

    private static void equal(Object expected, Object actual, String label) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(label + " expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static final class SchemaValidator {
        private final Object root;

        SchemaValidator(Object root) {
            this.root = root;
        }

        List<String> validate(Object instance) {
            List<String> errors = new ArrayList<>();
            validateNode(root, instance, "$", errors);
            return errors;
        }

        private void validateNode(Object rawSchema, Object instance, String path, List<String> errors) {
            if (!(rawSchema instanceof Map<?, ?>)) {
                errors.add(path + ": schema node is not an object");
                return;
            }
            Map<String, Object> schema = object(rawSchema, "schema");
            if (schema.containsKey("$ref")) {
                validateNode(resolve(string(schema.get("$ref"), "$ref")), instance, path, errors);
                return;
            }
            if (schema.containsKey("const") && !jsonEquals(schema.get("const"), instance)) {
                errors.add(path + ": value does not equal const");
            }
            if (schema.containsKey("enum")) {
                boolean match = false;
                for (Object allowed : array(schema.get("enum"), "enum")) {
                    match |= jsonEquals(allowed, instance);
                }
                if (!match) errors.add(path + ": value is not in enum");
            }

            String type = schema.containsKey("type") ? string(schema.get("type"), "schema.type") : null;
            if (type != null && !matchesType(type, instance)) {
                errors.add(path + ": expected type " + type);
                return;
            }

            if (instance instanceof Map<?, ?>) validateObject(schema, object(instance, path), path, errors);
            if (instance instanceof List<?>) validateArray(schema, array(instance, path), path, errors);
            if (instance instanceof String text) validateString(schema, text, path, errors);
            if (instance instanceof BigDecimal number) validateNumber(schema, number, path, errors);
        }

        private void validateObject(Map<String, Object> schema, Map<String, Object> instance,
                                    String path, List<String> errors) {
            if (schema.containsKey("required")) {
                for (Object nameValue : array(schema.get("required"), "required")) {
                    String name = string(nameValue, "required[]");
                    if (!instance.containsKey(name)) errors.add(path + ": missing required property " + name);
                }
            }
            Map<String, Object> properties = schema.containsKey("properties")
                    ? object(schema.get("properties"), "properties") : Map.of();
            for (Map.Entry<String, Object> entry : instance.entrySet()) {
                if (properties.containsKey(entry.getKey())) {
                    validateNode(properties.get(entry.getKey()), entry.getValue(),
                            path + "." + entry.getKey(), errors);
                } else if (Boolean.FALSE.equals(schema.get("additionalProperties"))) {
                    errors.add(path + ": unexpected property " + entry.getKey());
                }
            }
        }

        private void validateArray(Map<String, Object> schema, List<Object> instance,
                                   String path, List<String> errors) {
            if (schema.containsKey("minItems")) {
                long minimum = ((BigDecimal) schema.get("minItems")).longValueExact();
                if (instance.size() < minimum) errors.add(path + ": fewer than " + minimum + " items");
            }
            if (Boolean.TRUE.equals(schema.get("uniqueItems"))) {
                for (int i = 0; i < instance.size(); i++) {
                    for (int j = i + 1; j < instance.size(); j++) {
                        if (jsonEquals(instance.get(i), instance.get(j))) {
                            errors.add(path + ": duplicate array items at " + i + " and " + j);
                        }
                    }
                }
            }
            if (schema.containsKey("items")) {
                for (int i = 0; i < instance.size(); i++) {
                    validateNode(schema.get("items"), instance.get(i), path + "[" + i + "]", errors);
                }
            }
        }

        private void validateString(Map<String, Object> schema, String instance,
                                    String path, List<String> errors) {
            if (schema.containsKey("minLength")) {
                long minimum = ((BigDecimal) schema.get("minLength")).longValueExact();
                if (instance.codePointCount(0, instance.length()) < minimum) {
                    errors.add(path + ": shorter than " + minimum + " characters");
                }
            }
            if (schema.containsKey("pattern")) {
                String regex = string(schema.get("pattern"), "pattern");
                if (!Pattern.compile(regex).matcher(instance).find()) {
                    errors.add(path + ": does not match pattern " + regex);
                }
            }
        }

        private void validateNumber(Map<String, Object> schema, BigDecimal instance,
                                    String path, List<String> errors) {
            if (schema.containsKey("minimum")) {
                BigDecimal minimum = (BigDecimal) schema.get("minimum");
                if (instance.compareTo(minimum) < 0) errors.add(path + ": below minimum " + minimum);
            }
        }

        private Object resolve(String reference) {
            if (!reference.startsWith("#/")) {
                throw new AssertionError("only local schema references are supported: " + reference);
            }
            Object current = root;
            for (String token : reference.substring(2).split("/")) {
                String decoded = token.replace("~1", "/").replace("~0", "~");
                current = object(current, "schema ref " + reference).get(decoded);
                if (current == null) throw new AssertionError("unresolved schema reference " + reference);
            }
            return current;
        }

        private static boolean matchesType(String type, Object value) {
            return switch (type) {
                case "object" -> value instanceof Map<?, ?>;
                case "array" -> value instanceof List<?>;
                case "string" -> value instanceof String;
                case "boolean" -> value instanceof Boolean;
                case "number" -> value instanceof BigDecimal;
                case "integer" -> value instanceof BigDecimal number
                        && number.stripTrailingZeros().scale() <= 0;
                case "null" -> value == null;
                default -> throw new AssertionError("unsupported schema type " + type);
            };
        }

        private static boolean jsonEquals(Object left, Object right) {
            if (left instanceof BigDecimal a && right instanceof BigDecimal b) {
                return a.compareTo(b) == 0;
            }
            if (left instanceof List<?> a && right instanceof List<?> b) {
                if (a.size() != b.size()) return false;
                for (int i = 0; i < a.size(); i++) {
                    if (!jsonEquals(a.get(i), b.get(i))) return false;
                }
                return true;
            }
            if (left instanceof Map<?, ?> a && right instanceof Map<?, ?> b) {
                if (!a.keySet().equals(b.keySet())) return false;
                for (Object key : a.keySet()) {
                    if (!jsonEquals(a.get(key), b.get(key))) return false;
                }
                return true;
            }
            return Objects.equals(left, right);
        }
    }

    private static final class Json {
        static Object parse(String text) {
            Parser parser = new Parser(text);
            Object value = parser.value();
            parser.whitespace();
            if (!parser.end()) throw parser.error("trailing data");
            return value;
        }

        private static final class Parser {
            private final String text;
            private int at;

            Parser(String text) {
                this.text = text;
            }

            Object value() {
                whitespace();
                if (end()) throw error("expected value");
                return switch (text.charAt(at)) {
                    case '{' -> objectValue();
                    case '[' -> arrayValue();
                    case '"' -> stringValue();
                    case 't' -> literal("true", Boolean.TRUE);
                    case 'f' -> literal("false", Boolean.FALSE);
                    case 'n' -> literal("null", null);
                    default -> numberValue();
                };
            }

            Map<String, Object> objectValue() {
                expect('{');
                Map<String, Object> result = new LinkedHashMap<>();
                whitespace();
                if (take('}')) return result;
                while (true) {
                    whitespace();
                    if (end() || text.charAt(at) != '"') throw error("expected object key");
                    String key = stringValue();
                    whitespace();
                    expect(':');
                    Object parsed = value();
                    if (result.containsKey(key)) throw error("duplicate object key " + key);
                    result.put(key, parsed);
                    whitespace();
                    if (take('}')) return result;
                    expect(',');
                }
            }

            List<Object> arrayValue() {
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

            String stringValue() {
                expect('"');
                StringBuilder result = new StringBuilder();
                while (!end()) {
                    char c = text.charAt(at++);
                    if (c == '"') return result.toString();
                    if (c < 0x20) throw error("control character in string");
                    if (c != '\\') {
                        result.append(c);
                        continue;
                    }
                    if (end()) throw error("unfinished escape");
                    char escaped = text.charAt(at++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicode());
                        default -> throw error("unknown escape " + escaped);
                    }
                }
                throw error("unterminated string");
            }

            char unicode() {
                if (at + 4 > text.length()) throw error("short unicode escape");
                int value = 0;
                for (int i = 0; i < 4; i++) {
                    int digit = Character.digit(text.charAt(at++), 16);
                    if (digit < 0) throw error("bad unicode escape");
                    value = value * 16 + digit;
                }
                return (char) value;
            }

            Object literal(String token, Object value) {
                if (!text.startsWith(token, at)) throw error("expected " + token);
                at += token.length();
                return value;
            }

            BigDecimal numberValue() {
                int start = at;
                if (take('-') && end()) throw error("unfinished number");
                if (take('0')) {
                    if (!end() && Character.isDigit(text.charAt(at))) throw error("leading zero");
                } else {
                    digits();
                }
                if (take('.')) digits();
                if (!end() && (text.charAt(at) == 'e' || text.charAt(at) == 'E')) {
                    at++;
                    if (!end() && (text.charAt(at) == '+' || text.charAt(at) == '-')) at++;
                    digits();
                }
                try {
                    return new BigDecimal(text.substring(start, at));
                } catch (NumberFormatException e) {
                    throw error("invalid number");
                }
            }

            void digits() {
                int start = at;
                while (!end() && Character.isDigit(text.charAt(at))) at++;
                if (start == at) throw error("expected digit");
            }

            void whitespace() {
                while (!end()) {
                    char c = text.charAt(at);
                    if (c == ' ' || c == '\n' || c == '\r' || c == '\t') at++;
                    else return;
                }
            }

            void expect(char expected) {
                whitespace();
                if (end() || text.charAt(at) != expected) throw error("expected '" + expected + "'");
                at++;
            }

            boolean take(char expected) {
                if (!end() && text.charAt(at) == expected) {
                    at++;
                    return true;
                }
                return false;
            }

            boolean end() {
                return at >= text.length();
            }

            IllegalArgumentException error(String message) {
                return new IllegalArgumentException(message + " at character " + at);
            }
        }
    }
}
