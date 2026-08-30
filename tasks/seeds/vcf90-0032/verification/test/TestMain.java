import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Harness that exercises the single-file client against the loopback mock.
 *
 * It only drives the client and checks that it produced an output document; the
 * request-level assertions live in verify/verify.py, which reads the mock's
 * request log after this harness exits.
 */
public final class TestMain {

    private static final String DEFAULT_USERNAME = "administrator@vsphere.local";
    private static final String DEFAULT_PASSWORD = "VMw@re1!VMw@re1!";

    public static void main(String[] args) {
        String baseUrl = arg(args, 0, System.getenv("SDDC_BASE_URL"), null);
        if (baseUrl == null || baseUrl.isBlank()) {
            fail("no base URL: pass it as argv[0] or set SDDC_BASE_URL");
            return;
        }
        String username = arg(args, 1, System.getenv("SDDC_USERNAME"), DEFAULT_USERNAME);
        String password = arg(args, 2, System.getenv("SDDC_PASSWORD"), DEFAULT_PASSWORD);
        Path outDir = Path.of(arg(args, 3, System.getenv("SDDC_OUT_DIR"), "out"));

        System.out.println("[harness] base url : " + baseUrl);
        System.out.println("[harness] out dir  : " + outDir.toAbsolutePath());

        long started = System.nanoTime();
        Path produced;
        try {
            Files.createDirectories(outDir);
            produced = VcfDiagnostics.diagnose(baseUrl, username, password, outDir);
        } catch (Throwable t) {
            System.out.println("[harness] VcfDiagnostics.diagnose threw:");
            t.printStackTrace(System.out);
            fail("client did not complete");
            return;
        }
        long millis = (System.nanoTime() - started) / 1_000_000L;
        System.out.println("[harness] diagnose() returned in " + millis + " ms");

        if (produced == null) {
            fail("diagnose() returned null instead of the path to diagnosis.json");
            return;
        }
        Path expected = outDir.resolve("diagnosis.json");
        if (!produced.toAbsolutePath().normalize().equals(expected.toAbsolutePath().normalize())) {
            fail("diagnose() returned " + produced + " but must write " + expected);
            return;
        }
        if (!Files.isRegularFile(produced)) {
            fail("diagnose() did not write " + expected);
            return;
        }

        String body;
        try {
            body = Files.readString(produced);
        } catch (Exception e) {
            fail("could not read " + produced + ": " + e);
            return;
        }
        if (body.isBlank()) {
            fail(expected + " is empty");
            return;
        }

        System.out.println("[harness] diagnosis.json:");
        System.out.println(body.stripTrailing());
        System.out.println("[harness] OK - client ran and produced a diagnosis document");
    }

    private static String arg(String[] args, int i, String env, String fallback) {
        if (args.length > i && !args[i].isBlank()) {
            return args[i];
        }
        if (env != null && !env.isBlank()) {
            return env;
        }
        return fallback;
    }

    private static void fail(String message) {
        System.out.println("[harness] FAIL: " + message);
        System.exit(1);
    }

    private TestMain() {
    }
}
