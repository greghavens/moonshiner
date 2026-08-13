import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.Map;

/**
 * Drives {@link VcfLcmClient} against the loopback SDDC LCM fixture and writes the
 * client's rollout report to disk.
 *
 * Usage: TestMain <baseUrl> <bearerToken> <requestJsonFile> <reportOutFile>
 *
 * The base URL includes the spec's server base path, e.g. http://127.0.0.1:8931/sddc-lcm.
 * The exit status reflects whether the client ran and produced a well formed report --
 * a rollout whose outcome is FAILED is a valid result, not a harness error.
 */
public final class TestMain {

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            System.err.println("usage: TestMain <baseUrl> <token> <requestJsonFile> <reportOutFile>");
            System.exit(2);
        }
        String baseUrl = args[0];
        String token = args[1];
        Path requestFile = Paths.get(args[2]);
        Path reportFile = Paths.get(args[3]);

        String requestJson = new String(Files.readAllBytes(requestFile), StandardCharsets.UTF_8);

        String reportJson;
        try {
            reportJson = VcfLcmClient.run(baseUrl, token, requestJson);
        } catch (Exception e) {
            System.err.println("client threw: " + e);
            e.printStackTrace();
            System.exit(1);
            return;
        }

        if (reportJson == null) {
            System.err.println("client returned null instead of a report document");
            System.exit(1);
            return;
        }

        Object report;
        try {
            report = Json.parse(reportJson);
        } catch (RuntimeException e) {
            System.err.println("client returned a document that is not valid JSON: " + e);
            System.err.println(reportJson);
            System.exit(1);
            return;
        }

        Files.createDirectories(reportFile.toAbsolutePath().getParent());
        Files.write(reportFile, Json.writeIndented(report).getBytes(StandardCharsets.UTF_8));

        System.out.println("---- rollout report ----");
        System.out.println("outcome:       " + Json.str(report, "outcome"));
        System.out.println("componentId:   " + Json.str(report, "componentId"));
        System.out.println("correlationId: " + Json.str(report, "correlationId"));
        Object steps = Json.path(report, "steps");
        if (steps instanceof List) {
            for (Object s : Json.list(steps)) {
                Map<String, Object> step = Json.map(s);
                System.out.println("  step " + step.get("operationId")
                        + (step.get("action") == null ? "" : " (" + step.get("action") + ")")
                        + " -> " + step.get("status")
                        + (step.get("taskId") == null ? "" : "  task=" + step.get("taskId")));
            }
        }
        Object failure = Json.path(report, "failure");
        if (failure != null) {
            System.out.println("failure stage: " + Json.str(failure, "failedStageId"));
            System.out.println("failure text:  " + Json.str(failure, "message"));
        }
        System.out.println("report written to " + reportFile.toAbsolutePath());
    }
}
