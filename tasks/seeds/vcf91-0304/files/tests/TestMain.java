import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Drives {@link NiTierClient} against {@link MockNiServer} and prints what happened on the wire as
 * a single JSON document between two markers. All judgement lives in {@code tests/verify.py}; this
 * harness only exercises the client and reports.
 */
public final class TestMain {

    private static final String BEGIN = "---MOONSHINER-JSON-BEGIN---";
    private static final String END = "---MOONSHINER-JSON-END---";

    private static final String APP_ID = "18230:561:271275765";
    private static final String APP_NAME = "Payments";
    private static final String TOKEN = "9f1c4b2e-tier-token";

    public static void main(String[] args) throws Exception {
        List<Object> scenarios = new ArrayList<>();
        scenarios.add(minimalCreate());
        scenarios.add(fullBody());
        scenarios.add(repeatedCallConverges());
        scenarios.add(existingTierIsAdopted());
        scenarios.add(caseSensitiveNearMatchIsNotAdopted());
        scenarios.add(lostRaceIsAbsorbed());
        scenarios.add(conflictWithoutTierIsReported());
        scenarios.add(nonConflictFailureIsReported());
        scenarios.add(listFailureIsReported());
        scenarios.add(unknownApplication());
        scenarios.add(kubernetesMember());
        scenarios.add(baseUrlWithTrailingSlash());

        Map<String, Object> document = new LinkedHashMap<>();
        document.put("scenarios", scenarios);
        System.out.println(BEGIN);
        System.out.println(Json.write(document));
        System.out.println(END);
    }

    // ------------------------------------------------------------- scenarios

    private static Map<String, Object> minimalCreate() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            NiTierClient client = client(mock, mock.baseUrl());
            return run("minimal_create", mock,
                    () -> List.of(client.ensureTier(APP_ID, new NiTierClient.TierSpec("web-tier"))));
        }
    }

    private static Map<String, Object> fullBody() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            NiTierClient client = client(mock, mock.baseUrl());
            NiTierClient.TierSpec spec = new NiTierClient.TierSpec("edge \"dmz\" tier")
                    .searchCriteria("VirtualMachine", "security_groups.entity_id = '18230:82:604573173'")
                    .ipAddresses(List.of("10.0.0.1", "10.0.0.1/24", "10.0.0.1-10.0.0.200"))
                    .vms(List.of(
                            new NiTierClient.Member("18230:1:1158969162", "VIRTUALMACHINE", "VM1"),
                            new NiTierClient.Member("18230:601:863301375", "EC2INSTANCE", null)))
                    .physicalIps(List.of(
                            new NiTierClient.Member("18230:541:365252372", "IPENDPOINT", "52.35.41.245")))
                    .sourceGroupEntityIds(List.of("18230:566:264351372"));
            return run("full_body", mock, () -> List.of(client.ensureTier(APP_ID, spec)));
        }
    }

    private static Map<String, Object> repeatedCallConverges() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            NiTierClient client = client(mock, mock.baseUrl());
            return run("repeated_call_converges", mock, () -> Arrays.asList(
                    client.ensureTier(APP_ID, new NiTierClient.TierSpec("web-tier")),
                    client.ensureTier(APP_ID, new NiTierClient.TierSpec("web-tier"))));
        }
    }

    private static Map<String, Object> existingTierIsAdopted() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            String seeded = mock.seedTier(APP_ID, "web-tier");
            NiTierClient client = client(mock, mock.baseUrl());
            Map<String, Object> scenario = run("existing_tier_is_adopted", mock,
                    () -> List.of(client.ensureTier(APP_ID, new NiTierClient.TierSpec("web-tier"))));
            scenario.put("seeded_tier_id", seeded);
            return scenario;
        }
    }

    private static Map<String, Object> caseSensitiveNearMatchIsNotAdopted() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            String wrongCase = mock.seedTier(APP_ID, "Web-Tier");
            String longerName = mock.seedTier(APP_ID, "web-tier-blue");
            NiTierClient client = client(mock, mock.baseUrl());
            Map<String, Object> scenario = run("case_sensitive_near_match", mock,
                    () -> List.of(client.ensureTier(
                            APP_ID, new NiTierClient.TierSpec("web-tier"))));
            scenario.put("seeded_tier_ids", List.of(wrongCase, longerName));
            return scenario;
        }
    }

    private static Map<String, Object> lostRaceIsAbsorbed() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            String seeded = mock.seedTierHiddenFromNextList(APP_ID, "web-tier");
            NiTierClient client = client(mock, mock.baseUrl());
            Map<String, Object> scenario = run("lost_race_is_absorbed", mock,
                    () -> List.of(client.ensureTier(APP_ID, new NiTierClient.TierSpec("web-tier"))));
            scenario.put("seeded_tier_id", seeded);
            return scenario;
        }
    }

    private static Map<String, Object> conflictWithoutTierIsReported() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME)
                    .failNextAddTier(400, "Concurrent tier was rolled back");
            NiTierClient client = client(mock, mock.baseUrl());
            return run("conflict_without_tier", mock,
                    () -> List.of(client.ensureTier(
                            APP_ID, new NiTierClient.TierSpec("web-tier"))));
        }
    }

    private static Map<String, Object> nonConflictFailureIsReported() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME)
                    .failNextAddTier(500, "Tier service is unavailable");
            NiTierClient client = client(mock, mock.baseUrl());
            return run("non_conflict_failure", mock,
                    () -> List.of(client.ensureTier(
                            APP_ID, new NiTierClient.TierSpec("web-tier"))));
        }
    }

    private static Map<String, Object> listFailureIsReported() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME)
                    .failNextTierList(500, "Tier inventory is unavailable");
            NiTierClient client = client(mock, mock.baseUrl());
            return run("list_failure", mock,
                    () -> List.of(client.ensureTier(
                            APP_ID, new NiTierClient.TierSpec("web-tier"))));
        }
    }

    private static Map<String, Object> unknownApplication() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            NiTierClient client = client(mock, mock.baseUrl());
            return run("unknown_application", mock, () -> List.of(
                    client.ensureTier("18230:561:000000000", new NiTierClient.TierSpec("web-tier"))));
        }
    }

    private static Map<String, Object> kubernetesMember() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            NiTierClient client = client(mock, mock.baseUrl());
            NiTierClient.TierSpec spec = new NiTierClient.TierSpec("service-tier")
                    .kubernetesServices(List.of(new NiTierClient.Member(
                            "18230:1504:321", "KUBERNETESSERVICE", "checkout")));
            return run("kubernetes_member", mock,
                    () -> List.of(client.ensureTier(APP_ID, spec)));
        }
    }

    private static Map<String, Object> baseUrlWithTrailingSlash() throws Exception {
        try (MockNiServer mock = new MockNiServer()) {
            mock.addApplication(APP_ID, APP_NAME);
            NiTierClient client = client(mock, mock.baseUrl() + "/");
            return run("base_url_with_trailing_slash", mock,
                    () -> List.of(client.ensureTier(APP_ID, new NiTierClient.TierSpec("web-tier"))));
        }
    }

    // --------------------------------------------------------------- plumbing

    private static NiTierClient client(MockNiServer mock, String baseUrl) {
        return new NiTierClient(baseUrl, TOKEN, mock.httpClient());
    }

    private interface Body {
        List<NiTierClient.EnsureResult> call();
    }

    private static Map<String, Object> run(String name, MockNiServer mock, Body body) {
        Map<String, Object> scenario = new LinkedHashMap<>();
        scenario.put("name", name);
        try {
            List<Object> results = new ArrayList<>();
            for (NiTierClient.EnsureResult result : body.call()) {
                Map<String, Object> entry = new LinkedHashMap<>();
                entry.put("created", result.created());
                entry.put("tier_id", result.tierId());
                entry.put("tier_name", result.tierName());
                results.add(entry);
            }
            scenario.put("results", results);
            scenario.put("error", null);
        } catch (NiTierClient.NiApiException e) {
            Map<String, Object> error = new LinkedHashMap<>();
            error.put("type", "NiApiException");
            error.put("status_code", (long) e.statusCode());
            error.put("message", String.valueOf(e.getMessage()));
            scenario.put("results", List.of());
            scenario.put("error", error);
        } catch (RuntimeException e) {
            Map<String, Object> error = new LinkedHashMap<>();
            error.put("type", e.getClass().getName());
            error.put("status_code", null);
            error.put("message", String.valueOf(e.getMessage()));
            scenario.put("results", List.of());
            scenario.put("error", error);
        }
        scenario.put("requests", describe(mock));
        return scenario;
    }

    private static List<Object> describe(MockNiServer mock) {
        List<Object> requests = new ArrayList<>();
        for (MockNiServer.Record record : mock.requestLog()) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("sequence", (long) record.sequence());
            entry.put("method", record.method());
            entry.put("path", record.path());
            entry.put("query", record.query());
            entry.put("target", record.target());
            entry.put("headers", new LinkedHashMap<>(record.headers()));
            entry.put("body", record.body());
            requests.add(entry);
        }
        return requests;
    }
}
