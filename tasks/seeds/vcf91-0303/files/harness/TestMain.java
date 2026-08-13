import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * Drives VcfOnAppInventory against the loopback mock twice, with two different datasets and two
 * different configurations, and leaves everything the verifier needs under the output directory.
 *
 * Two runs, not one, on purpose: a client that hardcodes one dataset's answer cannot satisfy the
 * other. They differ in page size, in how the final page signals the end of the walk, in whether
 * modifiedAfter is configured, and in whether an authentication domain is configured.
 *
 * The client runs as a subprocess, so its exit code and its stdout are observed exactly as a
 * caller would see them and a stray System.exit cannot take the harness down with it.
 *
 * Usage: java -cp <classpath> TestMain <output-dir>
 *
 * DO NOT MODIFY.
 */
public final class TestMain {

    private static final long CLIENT_TIMEOUT_SECONDS = 90;

    /** One invocation of the client against one dataset. */
    private record RunConfig(String name,
                             String fixture,
                             String username,
                             String password,
                             String domainType,
                             String domainValue,
                             int pageSize,
                             Long modifiedAfter) {}

    private static final List<RunConfig> RUNS = List.of(
            // Seven applications, page size 3 -> pages of 3, 3, 1. No domain, so the credential
            // body must carry exactly username and password. No modifiedAfter, so that parameter
            // must never appear. The final page omits 'cursor' entirely.
            new RunConfig("run1", "dataset-a.json",
                    "admin@local", "s3cr3t-A",
                    null, null,
                    3, null),

            // Five applications, page size 2 -> pages of 2, 2, 1. A LOCAL domain with no value, so
            // 'domain' must be present carrying only 'domain_type'. modifiedAfter is configured and
            // must ride along on every page request. The final page carries an EMPTY 'cursor'.
            new RunConfig("run2", "dataset-b.json",
                    "netops@corp.example", "s3cr3t-B",
                    "LOCAL", null,
                    2, 1700000000000L));

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            System.err.println("usage: TestMain <output-dir>");
            System.exit(2);
        }
        Path outDir = Path.of(args[0]).toAbsolutePath().normalize();
        Path harnessDir = locateHarnessDir();
        Files.createDirectories(outDir);

        List<Object> summaries = new ArrayList<>();
        for (RunConfig config : RUNS) {
            summaries.add(execute(config, harnessDir, outDir));
        }

        Map<String, Object> manifest = new LinkedHashMap<>();
        manifest.put("runs", summaries);
        Files.writeString(outDir.resolve("runs.json"), MiniJson.write(manifest) + "\n",
                StandardCharsets.UTF_8);

        // Always exit 0. Whether the client behaved is the verifier's call, and it needs these
        // artifacts to make it.
        System.out.println("harness: wrote artifacts for " + RUNS.size() + " runs to " + outDir);
    }

    private static Map<String, Object> execute(RunConfig config, Path harnessDir, Path outDir)
            throws IOException, InterruptedException {
        Path runDir = outDir.resolve(config.name());
        Files.createDirectories(runDir);

        Map<String, Object> expectedDomain = null;
        if (config.domainType() != null || config.domainValue() != null) {
            expectedDomain = new LinkedHashMap<>();
            if (config.domainType() != null) {
                expectedDomain.put("domain_type", config.domainType());
            }
            if (config.domainValue() != null) {
                expectedDomain.put("value", config.domainValue());
            }
        }

        Path fixture = harnessDir.resolve("fixtures").resolve(config.fixture());
        MockVcfOnServer mock = new MockVcfOnServer(fixture, config.username(), config.password(),
                expectedDomain, config.modifiedAfter());
        String baseUrl = mock.start();

        int exitCode;
        boolean timedOut = false;
        try {
            List<String> command = new ArrayList<>(List.of(
                    Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                    "-cp", System.getProperty("java.class.path"),
                    "VcfOnAppInventory",
                    "--base-url", baseUrl,
                    "--username", config.username(),
                    "--password", config.password(),
                    "--page-size", String.valueOf(config.pageSize())));
            if (config.domainType() != null) {
                command.add("--domain-type");
                command.add(config.domainType());
            }
            if (config.domainValue() != null) {
                command.add("--domain-value");
                command.add(config.domainValue());
            }
            if (config.modifiedAfter() != null) {
                command.add("--modified-after");
                command.add(String.valueOf(config.modifiedAfter()));
            }

            Process process = new ProcessBuilder(command)
                    .redirectOutput(runDir.resolve("stdout.txt").toFile())
                    .redirectError(runDir.resolve("stderr.txt").toFile())
                    .start();
            process.getOutputStream().close();
            if (process.waitFor(CLIENT_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                exitCode = process.exitValue();
            } else {
                process.destroyForcibly();
                process.waitFor();
                exitCode = -1;
                timedOut = true;
            }
        } finally {
            mock.stop();
            mock.writeRequestLog(runDir.resolve("requests.jsonl"));
        }

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("name", config.name());
        summary.put("fixture", config.fixture());
        summary.put("base_url", baseUrl);
        summary.put("username", config.username());
        summary.put("password", config.password());
        summary.put("domain_type", config.domainType());
        summary.put("domain_value", config.domainValue());
        summary.put("page_size", (long) config.pageSize());
        summary.put("modified_after", config.modifiedAfter());
        summary.put("exit_code", (long) exitCode);
        summary.put("timed_out", timedOut);
        System.out.println("harness: " + config.name() + " finished with exit code " + exitCode
                + (timedOut ? " (TIMED OUT)" : ""));
        return summary;
    }

    /** The harness directory, resolved from this class's own location on the classpath. */
    private static Path locateHarnessDir() {
        // build/ sits beside harness/ in the project root.
        Path here = Path.of("").toAbsolutePath();
        Path candidate = here.resolve("harness");
        if (Files.isDirectory(candidate)) {
            return candidate;
        }
        String override = System.getProperty("harness.dir");
        if (override != null && Files.isDirectory(Path.of(override))) {
            return Path.of(override);
        }
        throw new IllegalStateException("cannot locate the harness directory from " + here
                + "; run the harness from the project root or set -Dharness.dir=<path>");
    }
}
