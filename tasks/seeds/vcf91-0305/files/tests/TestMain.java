import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Harness that exercises AppOnboarder against the contract-pinned loopback mock.
 *
 * usage: TestMain <workDir> [configPath]
 *
 * Writes:
 *   <workDir>/requests.jsonl  request log produced by the mock
 *   <workDir>/report.json     report produced by AppOnboarder
 *   <workDir>/harness.json    outcome of the run itself
 */
public final class TestMain {

    public static void main(String[] args) throws Exception {
        Path workDir = Path.of(args.length > 0 ? args[0] : "out").toAbsolutePath();
        Files.createDirectories(workDir);

        Path requestLog = workDir.resolve("requests.jsonl");
        Path report = workDir.resolve("report.json");
        Path harness = workDir.resolve("harness.json");
        Path config = Path.of(args.length > 1 ? args[1] : "config/onboarding.json")
                .toAbsolutePath();

        Files.deleteIfExists(report);

        MockVcfOnServer mock = new MockVcfOnServer(requestLog);
        mock.start();

        int exitCode = -1;
        String error = null;
        long startedAt = System.nanoTime();
        try {
            String[] clientArgs = {
                    mock.baseUrl(),
                    config.toString(),
                    report.toString(),
                    MockVcfOnServer.USERNAME,
                    MockVcfOnServer.PASSWORD,
            };
            exitCode = AppOnboarder.run(clientArgs);
        } catch (Throwable t) {
            error = t.getClass().getName() + ": " + t.getMessage();
            t.printStackTrace();
        } finally {
            mock.stop();
        }
        long elapsedMs = (System.nanoTime() - startedAt) / 1_000_000L;

        StringBuilder sb = new StringBuilder("{");
        sb.append("\"exit_code\":").append(exitCode);
        sb.append(",\"error\":").append(error == null ? "null" : jsonStr(error));
        sb.append(",\"report_written\":").append(Files.exists(report));
        sb.append(",\"elapsed_ms\":").append(elapsedMs);
        sb.append("}\n");
        Files.writeString(harness, sb.toString(), StandardCharsets.UTF_8);

        System.out.println("harness: exit_code=" + exitCode
                + " report_written=" + Files.exists(report)
                + (error == null ? "" : " error=" + error));
    }

    private static String jsonStr(String s) {
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.append('"').toString();
    }
}
