import java.net.URI;
import java.net.http.HttpClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

public final class TestMain {
    private static final String PUT =
            "OrgsOrgIdProjectsProjectIdInfraUpdateSecurityPolicyForDomain";
    private static final String STATUS =
            "OrgsOrgIdProjectsProjectIdInfraReadIntentStatus";
    private static final String LIST =
            "OrgsOrgIdProjectsProjectIdInfraListSecurityPoliciesForDomain";

    private TestMain() {
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("expected BASE_URI and REQUEST_LOG");
        }
        Path requestLog = Path.of(args[1]);
        NsxPolicyClient client = new NsxPolicyClient(
                URI.create(args[0]),
                "integration-user",
                "integration-password",
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofSeconds(2))
                        .build(),
                duration -> {
                    long millis = Math.max(1L, duration.toMillis());
                    Thread.sleep(millis);
                });

        List<String> policyIds = List.of(
                "web & edge", "alpha-policy", "mike-policy", "web & edge");
        List<String> displayNames = List.of("zulu", "alpha", "mike", "zulu");
        List<List<NsxPolicyClient.PolicySummary>> expectedRuns = List.of(
                List.of(new NsxPolicyClient.PolicySummary("web & edge", "zulu")),
                List.of(
                        new NsxPolicyClient.PolicySummary("alpha-policy", "alpha"),
                        new NsxPolicyClient.PolicySummary("web & edge", "zulu")),
                List.of(
                        new NsxPolicyClient.PolicySummary("alpha-policy", "alpha"),
                        new NsxPolicyClient.PolicySummary("mike-policy", "mike"),
                        new NsxPolicyClient.PolicySummary("web & edge", "zulu")),
                List.of(
                        new NsxPolicyClient.PolicySummary("alpha-policy", "alpha"),
                        new NsxPolicyClient.PolicySummary("mike-policy", "mike"),
                        new NsxPolicyClient.PolicySummary("web & edge", "zulu")));

        for (int run = 0; run < policyIds.size(); run++) {
            List<NsxPolicyClient.PolicySummary> actual = client.upsertWaitAndList(
                    "acme org", "project/blue", "default",
                    policyIds.get(run), displayNames.get(run),
                    Duration.ofSeconds(2), Duration.ofMillis(2));
            List<NsxPolicyClient.PolicySummary> expected = expectedRuns.get(run);
            if (!actual.equals(expected)) {
                throw new AssertionError(
                        "collection must be locally sorted; run=" + run
                                + " expected=" + expected + " actual=" + actual);
            }
            try {
                actual.add(new NsxPolicyClient.PolicySummary("x", "x"));
                throw new AssertionError("returned collection must be immutable");
            } catch (UnsupportedOperationException expectedException) {
                // Expected.
            }
        }

        List<String> lines = Files.readAllLines(requestLog);
        List<String> operations = new ArrayList<>();
        for (String line : lines) {
            if (line.contains("\"operationId\":\"" + PUT + "\"")) {
                operations.add(PUT);
            } else if (line.contains("\"operationId\":\"" + STATUS + "\"")) {
                operations.add(STATUS);
            } else if (line.contains("\"operationId\":\"" + LIST + "\"")) {
                operations.add(LIST);
            } else {
                throw new AssertionError("mock received a non-contract operation: " + line);
            }
        }
        List<String> expectedOperations = new ArrayList<>();
        for (int run = 0; run < policyIds.size(); run++) {
            expectedOperations.addAll(List.of(
                    PUT, STATUS, STATUS, STATUS, LIST));
        }
        if (!operations.equals(expectedOperations)) {
            throw new AssertionError(
                    "client did not poll each update to terminal success: " + operations);
        }

        String completeLog = String.join("\n", lines);
        if (!completeLog.contains(
                "\"response_order\":[\"zulu\",\"alpha\",\"mike\"]")
                || !completeLog.contains(
                        "\"response_order\":[\"mike\",\"alpha\",\"zulu\"]")) {
            throw new AssertionError("mock did not flip collection response order");
        }
        if (!completeLog.contains("/acme%20org/projects/project%2Fblue/")
                || !completeLog.contains("security-policies/web%20%26%20edge")) {
            throw new AssertionError("path segments were not independently percent encoded");
        }
        if (!completeLog.contains(
                "\"intent_path\":[\"/infra/domains/default/security-policies/web & edge\"]")) {
            throw new AssertionError("intent_path query parameter was missing or incorrect");
        }

        System.out.println("PASS vcf91-0087");
    }
}
