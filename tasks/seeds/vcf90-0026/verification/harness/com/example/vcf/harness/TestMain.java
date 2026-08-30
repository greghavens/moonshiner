package com.example.vcf.harness;

import com.example.vcf.SddcManagerClient;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Boots the loopback mock, drives {@link SddcManagerClient} against it, and records the outcome.
 *
 * <p>PROTECTED HARNESS FILE — do not modify.
 *
 * <p>Writes two artefacts under the output directory (default {@code target}):
 *
 * <ul>
 *   <li>{@code request-log.jsonl} — one JSON object per HTTP request the mock received
 *   <li>{@code result.json} — what the client returned, or the exception it threw
 * </ul>
 *
 * <p>Exits 0 whether or not the client succeeded; {@code Verifier} is the judge.
 */
public final class TestMain {

    /** A client that never finishes must not wedge verification. */
    private static final long WATCHDOG_SECONDS = 120;

    public static void main(String[] args) throws Exception {
        Path outDir = Path.of(args.length > 0 ? args[0] : "target");
        Files.createDirectories(outDir);
        Path requestLog = outDir.resolve("request-log.jsonl");
        Path resultFile = outDir.resolve("result.json");
        String terminalTaskStatus = args.length > 1 ? args[1] : "SUCCESSFUL";
        Files.deleteIfExists(resultFile);
        startWatchdog(resultFile);

        MockSddcManager mock = new MockSddcManager(requestLog, terminalTaskStatus);
        mock.start();
        Map<String, Object> outcome = new LinkedHashMap<>();
        try {
            String baseUrl = mock.baseUrl();
            System.out.println("[harness] mock SDDC Manager listening on " + baseUrl);
            SddcManagerClient client =
                    new SddcManagerClient(baseUrl, MockSddcManager.USERNAME, MockSddcManager.PASSWORD);
            Map<String, Object> result = client.downloadPendingSddcManagerBundle();
            outcome.put("ok", Boolean.TRUE);
            outcome.put("result", result == null ? null : new LinkedHashMap<String, Object>(result));
            System.out.println("[harness] client returned " + MiniJson.write(outcome.get("result")));
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            outcome.put("ok", Boolean.FALSE);
            outcome.put("error", t.getClass().getName() + ": " + t.getMessage());
            outcome.put("stackTrace", sw.toString());
            System.out.println("[harness] client threw " + outcome.get("error"));
        } finally {
            mock.stop();
        }
        Files.writeString(resultFile, MiniJson.write(outcome), StandardCharsets.UTF_8);
        System.out.println("[harness] wrote " + resultFile + " and " + requestLog);
        System.exit(0);
    }

    private static void startWatchdog(Path resultFile) {
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(WATCHDOG_SECONDS * 1000L);
            } catch (InterruptedException e) {
                return;
            }
            Map<String, Object> outcome = new LinkedHashMap<>();
            outcome.put("ok", Boolean.FALSE);
            outcome.put(
                    "error",
                    "the client did not return within " + WATCHDOG_SECONDS + " seconds");
            try {
                Files.writeString(resultFile, MiniJson.write(outcome), StandardCharsets.UTF_8);
            } catch (Exception ignored) {
                // nothing useful to do; the missing result file is itself a failure
            }
            System.out.println("[harness] watchdog fired after " + WATCHDOG_SECONDS + "s");
            Runtime.getRuntime().halt(0);
        }, "harness-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    private TestMain() {}
}
