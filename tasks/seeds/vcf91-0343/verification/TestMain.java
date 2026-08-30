import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.lang.reflect.Modifier;
import java.util.List;

public class TestMain {
    private static int checks;

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void checkApiShape() {
        check(Modifier.isPublic(AutomationClient.class.getModifiers()),
                "AutomationClient must be public");
        check(Modifier.isFinal(AutomationClient.class.getModifiers()),
                "AutomationClient must be final");
        check(AutomationClient.ProjectSummary.class.isRecord(),
                "ProjectSummary must be a record");
        check(AutomationClient.ProjectUpdate.class.isRecord(),
                "ProjectUpdate must be a record");
        check(AutomationClient.Tag.class.isRecord(), "Tag must be a record");
        check(AutomationClient.ZoneAssignment.class.isRecord(),
                "ZoneAssignment must be a record");
        check(AutomationClient.StepResult.class.isRecord(),
                "StepResult must be a record");
        check(AutomationClient.ChangeReport.class.isRecord(),
                "ChangeReport must be a record");
    }

    private static void checkProjects(AutomationClient.ChangeReport report) {
        List<String> ordered = report.projects().stream()
                .map(project -> project.name() + "|" + project.id())
                .toList();
        check(ordered.equals(List.of(
                        "Alpha Project|project-a",
                        "Alpha Project|project-b",
                        "Payments Platform|project-payments",
                        "Zeta Project|project-z")),
                "collection output must be sorted by name and then id: " + ordered);
    }

    private static void checkReport(
            AutomationClient.ChangeReport report,
            int[] statuses,
            boolean[] succeeded,
            String[] messages,
            boolean complete) {
        checkProjects(report);
        check(report.steps().size() == 3, "all three change steps should be reported");
        List<String> operations = List.of(
                "updateProject",
                "updateProjectResourceMetadata",
                "updateProjectZoneAssignments");
        for (int index = 0; index < operations.size(); index++) {
            AutomationClient.StepResult step = report.steps().get(index);
            check(step.operation().equals(operations.get(index)),
                    "wrong operation id at step " + index);
            check(step.statusCode() == statuses[index],
                    "wrong HTTP status at step " + index + ": " + step.statusCode());
            check(step.succeeded() == succeeded[index],
                    "wrong success flag at step " + index);
            check(step.message().equals(messages[index]),
                    "wrong message at step " + index + ": " + step.message());
        }
        check(report.complete() == complete, "wrong complete flag");
    }

    public static void main(String[] args) throws Exception {
        check(args.length == 2, "usage: TestMain <base-uri> <request-log>");
        URI baseUri = URI.create(args[0]);
        checkApiShape();
        AutomationClient client = new AutomationClient(baseUri, "fixture-token");

        for (int run = 0; run < 2; run++) {
            AutomationClient.ChangeReport report = client.applyProjectChange(
                    "project/payments 42",
                    new AutomationClient.ProjectUpdate(
                            "Payments Platform", "Quarterly \"capacity\" refresh\nrun " + run),
                    List.of(
                            new AutomationClient.Tag("environment", "production"),
                            new AutomationClient.Tag("owner", "platform-\"ops\"")),
                    List.of(new AutomationClient.ZoneAssignment("zone-retired", 1, 12)));
            checkReport(
                    report,
                    new int[] {200, 200, 400},
                    new boolean[] {true, true, false},
                    new String[] {"", "", "zone zone-retired is not available"},
                    false);
        }

        AutomationClient alternateClient = new AutomationClient(baseUri, "alternate-token");
        AutomationClient.ChangeReport successful = alternateClient.applyProjectChange(
                "team?red#1",
                new AutomationClient.ProjectUpdate("Delivery 🚀", "Path C:\\deploy\tphase"),
                List.of(
                        new AutomationClient.Tag("release\nchannel", "βeta"),
                        new AutomationClient.Tag("optional", "present")),
                List.of(new AutomationClient.ZoneAssignment("zone-active", 2, 25)));
        checkReport(
                successful,
                new int[] {200, 200, 202},
                new boolean[] {true, true, true},
                new String[] {"", "", ""},
                true);

        AutomationClient.ChangeReport earlierFailures = client.applyProjectChange(
                "project early",
                new AutomationClient.ProjectUpdate("rejected", "later steps must still execute"),
                List.of(new AutomationClient.Tag("restricted", "value")),
                List.of(new AutomationClient.ZoneAssignment("zone-active", 3, 30)));
        checkReport(
                earlierFailures,
                new int[] {400, 403, 202},
                new boolean[] {false, false, true},
                new String[] {"project update rejected", "metadata update forbidden", ""},
                false);

        HttpResponse<String> unsupported = HttpClient.newHttpClient().send(
                HttpRequest.newBuilder(baseUri.resolve("/iaas/api/about")).GET().build(),
                HttpResponse.BodyHandlers.ofString());
        check(unsupported.statusCode() == 404,
                "the mock must not serve operations outside the pinned contract");

        System.out.println("OK " + checks + " checks");
    }
}
