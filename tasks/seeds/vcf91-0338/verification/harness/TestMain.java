import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Harness entry point. Builds the client under test, runs one triage against
 * the loopback mock and writes the report it produced.
 *
 * Usage: TestMain &lt;baseUrl&gt; &lt;bearerToken&gt; &lt;deploymentId&gt; &lt;reportPath&gt;
 *
 * PROTECTED FILE - part of the graded harness, do not modify.
 */
public final class TestMain {

    public static void main(String[] args) {
        if (args.length != 4) {
            System.err.println("usage: TestMain <baseUrl> <bearerToken> <deploymentId> <reportPath>");
            System.exit(2);
        }
        String baseUrl = args[0];
        String token = args[1];
        String deploymentId = args[2];
        Path reportPath = Path.of(args[3]);

        String report;
        try {
            VcfaTriage client = new VcfaTriage(baseUrl, token);
            report = client.triage(deploymentId);
        } catch (Throwable t) {
            System.err.println("client threw " + t);
            t.printStackTrace(System.err);
            writeQuietly(reportPath, "");
            System.exit(1);
            return;
        }

        if (report == null) {
            System.err.println("client returned a null report");
            writeQuietly(reportPath, "");
            System.exit(1);
            return;
        }

        writeQuietly(reportPath, report);
        System.out.println(report);
    }

    private static void writeQuietly(Path path, String text) {
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }
            Files.writeString(path, text, StandardCharsets.UTF_8);
        } catch (Exception e) {
            System.err.println("could not write report: " + e);
        }
    }
}
