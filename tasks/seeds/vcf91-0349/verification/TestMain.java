import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class TestMain {
    private static final String PROJECTS_PATH = "/iaas/api/projects";
    private static final String DEPLOYMENTS_PATH = "/deployment/api/deployments";

    public static void main(String[] args) throws Exception {
        verifyProvenanceFixtures();
        try (MockVcfAutomationServer mock =
                     new MockVcfAutomationServer(Path.of("docs", "contract.json"))) {
            mock.start();
            rejectUnknownOperation(mock.baseUri());

            VcfAutomationClient client = new VcfAutomationClient(mock.baseUri(), mock.token());
            List<VcfAutomationClient.Deployment> actual =
                    client.listDeployments(mock.targetProjectName());
            List<MockVcfAutomationServer.FixtureDeployment> expected = mock.expectedDeployments();

            check(actual.size() == expected.size(), "the complete deployment collection was not returned");
            for (int i = 0; i < expected.size(); i++) {
                MockVcfAutomationServer.FixtureDeployment want = expected.get(i);
                VcfAutomationClient.Deployment got = actual.get(i);
                check(got.id().equals(want.id()), "deployment id/order mismatch at index " + i);
                check(got.name().equals(want.name()), "deployment name/order mismatch at index " + i);
                check(got.status().equals(want.status()), "deployment status mismatch at index " + i);
            }

            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (PrintStream output = new PrintStream(bytes, true, StandardCharsets.UTF_8)) {
                client.emitDeployments(mock.targetProjectName(), output);
            }
            StringBuilder wantedOutput = new StringBuilder();
            for (MockVcfAutomationServer.FixtureDeployment item : expected) {
                wantedOutput.append("{\"id\":").append(MockVcfAutomationServer.json(item.id()))
                        .append(",\"name\":").append(MockVcfAutomationServer.json(item.name()))
                        .append(",\"status\":").append(MockVcfAutomationServer.json(item.status()))
                        .append("}\n");
            }
            check(bytes.toString(StandardCharsets.UTF_8).equals(wantedOutput.toString()),
                    "emitDeployments did not produce stable JSON Lines output");

            verifyRequestLog(mock.requestLog(), mock.token());
            verifyNon2xxIsFailure(mock);
        }
        System.out.println("PASS");
    }

    private static void verifyNon2xxIsFailure(MockVcfAutomationServer mock) {
        VcfAutomationClient unauthorized =
                new VcfAutomationClient(mock.baseUri(), "deliberately-wrong-token");
        boolean failed = false;
        try {
            unauthorized.listDeployments(mock.targetProjectName());
        } catch (Exception expected) {
            failed = true;
        }
        check(failed, "client accepted a non-2xx response with a success-shaped JSON body");
    }

    private static void rejectUnknownOperation(URI baseUri) throws Exception {
        HttpResponse<String> response = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(baseUri.resolve("/not-in-contract")).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        check(response.statusCode() == 404, "mock served an operation outside the pinned contract");
    }

    private static void verifyProvenanceFixtures() throws Exception {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        check(contract.contains("reference documentation, not a published API specification"),
                "contract provenance statement is missing");
        check(contract.contains("\"product_version\": \"9.1\""), "contract is not pinned to 9.1");
        check(sources.contains("developer.broadcom.com/xapis/vm-apps-org-provisioning-service/latest/iaas/api/projects/get/"),
                "Get Projects official source is missing");
        check(sources.contains("developer.broadcom.com/xapis/vm-apps-org-policies/latest/deployment/api/deployments/get/"),
                "Get Deployments official source is missing");
        check(count(sources, "\"fetched_on\": \"2026-08-16\"") == 3,
                "every official source must record the fetch date");
        check(count(sources, "\"operation\":") == 2,
                "every official source must identify its operation");
    }

    private static void verifyRequestLog(
            List<MockVcfAutomationServer.RequestLogEntry> log, String token) {
        Set<String> identifiersReturnedByEarlierLookups = new LinkedHashSet<>();
        Set<Integer> pagesRequested = new LinkedHashSet<>();

        for (MockVcfAutomationServer.RequestLogEntry entry : log) {
            if (entry.path().equals("/not-in-contract")) {
                check(entry.responseStatus() == 404, "unknown operation did not fail closed");
                continue;
            }
            check(entry.method().equals("GET"), "client used an undocumented method");
            check(entry.path().equals(PROJECTS_PATH) || entry.path().equals(DEPLOYMENTS_PATH),
                    "client called an operation outside the contract: " + entry.path());
            check(String.valueOf(entry.authorization()).equals("Bearer " + token),
                    "client omitted or changed the bearer token");
            check(entry.responseStatus() == 200, "client request failed with " + entry.responseStatus());

            if (entry.path().equals(PROJECTS_PATH)) {
                identifiersReturnedByEarlierLookups.addAll(entry.identifiersReturned());
                continue;
            }

            String used = entry.identifierUsed();
            check(used != null && identifiersReturnedByEarlierLookups.contains(used),
                    "deployment filter used identifier not returned by the client's own earlier lookup: " + used);
            List<String> sizes = entry.query().get("size");
            check(sizes != null && sizes.equals(List.of("2")), "deployment page size must be 2");
            List<String> pages = entry.query().get("page");
            check(pages != null && pages.size() == 1, "deployment request omitted page");
            pagesRequested.add(Integer.parseInt(pages.get(0)));
        }

        check(!identifiersReturnedByEarlierLookups.isEmpty(), "client never performed its project lookup");
        check(pagesRequested.containsAll(List.of(0, 1, 2)),
                "client did not retrieve every deployment page: " + pagesRequested);
    }

    private static int count(String text, String needle) {
        int count = 0;
        for (int at = 0; (at = text.indexOf(needle, at)) >= 0; at += needle.length()) count++;
        return count;
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
