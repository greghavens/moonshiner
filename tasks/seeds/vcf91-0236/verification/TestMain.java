import static java.util.List.of;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Supplier;

/**
 * Harness for the SDDC LCM support-bundle client.
 *
 * <p>Every scenario runs the client against a loopback mock whose routes come from
 * {@code docs/contract.json}, then hands the recorded exchange to {@link WireVerifier}. No live
 * VMware endpoint is contacted.
 */
public final class TestMain {

    private static final String TOKEN = "eyJhbGciOiJSUzI1NiJ9.sddc-lcm-test-token";
    private static final String CORRELATION = "corr-vcf91-0236";

    private static final String CMP_A = "af6ef462-e192-4fe1-9522-67a50a2b3392";
    private static final String CMP_B = "1c9b6ad2-77c1-4f0b-8d5e-6b2a0f31c7d4";
    private static final String CMP_C = "7d3f8e51-2b44-4c9a-9f10-5e6c8a2d1b03";
    private static final String CMP_D = "b52a0c19-4d6e-4a83-9c27-3f81e5b04a6d";

    private static final String TASK_A = "f47ac10b-58cc-4372-a567-0e02b2c3d479";
    private static final String TASK_B = "2e1d9c8b-7a65-4f34-8123-9d0ea4b7c651";
    private static final String TASK_C = "5b8e2f47-c390-41d6-a7f2-0c4b19d8e365";
    private static final String TASK_D = "3f7a1e05-6c92-4d38-b0a7-58e14c9b2f60";
    private static final String TASK_DEL_1 = "9a4c7e20-1f38-4b5d-8e69-2c07d3f4a1b8";
    private static final String TASK_DEL_2 = "c60b3d92-8e47-4a15-b3f0-71d29c5e8a64";
    private static final String TASK_STUCK = "8d2c5b71-0a34-4e69-9f18-42b7e03d6c95";

    private static final MockSddcLcm.Bundle A1 =
            new MockSddcLcm.Bundle("bundle-a-0001", "sddc-a-20260509.tgz", 1024L,
                    "2026-05-09T08:00:00.000Z", "https://vmsp.broadcom.com/b/bundle-a-0001");
    private static final MockSddcLcm.Bundle A2 =
            new MockSddcLcm.Bundle("bundle-a-0002", "sddc-a-20260511.tgz", 2048L,
                    "2026-05-11T09:00:00.000Z", "https://vmsp.broadcom.com/b/bundle-a-0002");
    private static final MockSddcLcm.Bundle A3 =
            new MockSddcLcm.Bundle("bundle-a-0003", "sddc-a-20260512.tgz", 4096L,
                    "2026-05-12T10:00:00.000Z", "https://vmsp.broadcom.com/b/bundle-a-0003");
    private static final MockSddcLcm.Bundle A4 =
            new MockSddcLcm.Bundle("bundle-a-0004", "sddc-a-20260513.tgz", 8192L,
                    "2026-05-13T11:29:00.000Z", "https://vmsp.broadcom.com/b/bundle-a-0004");
    private static final MockSddcLcm.Bundle C1 =
            new MockSddcLcm.Bundle("bundle-c-0001", "sddc-c-20260513.tgz", 6144L,
                    "2026-05-13T11:30:00.000Z", "https://vmsp.broadcom.com/b/bundle-c-0001");
    private static final MockSddcLcm.Bundle D1 =
            new MockSddcLcm.Bundle("bundle-d-0001", "sddc-d-20260513.tgz", 512L,
                    "2026-05-13T11:31:00.000Z", "https://vmsp.broadcom.com/b/bundle-d-0001");

    private static final String FAILED_STAGE = "support-bundle-upload";
    private static final String FAILED_MESSAGE = "Upload to VMSP rejected the bundle: depot quota exceeded";

    private static int failures = 0;

    public static void main(String[] args) throws Exception {
        try (MockSddcLcm mock = MockSddcLcm.start()) {
            run("configuration is rejected before any request", () -> configurationRejected(mock));
            run("mixed collection run with pruning", () -> mixedRunWithPruning(mock));
            run("no correlation id and pruning disabled", () -> withoutCorrelationId(mock));
            run("listing rejected mid-run", () -> listingRejectedMidRun(mock));
            run("task never reaches a terminal status", () -> pollTimeout(mock));
        }
        if (failures > 0) {
            System.out.println();
            System.out.println(failures + " scenario(s) failed");
            System.exit(1);
        }
        System.out.println();
        System.out.println("all scenarios passed");
    }

    // ------------------------------------------------------------------ scenarios

    private static void configurationRejected(MockSddcLcm mock) {
        String scenario = "configuration is rejected before any request";
        mock.reset();

        rejects(scenario, "null configuration", () -> null);
        rejects(scenario, "null base URL", () -> config(null, TOKEN));
        rejects(scenario, "blank base URL", () -> config("   ", TOKEN));
        rejects(scenario, "relative base URL", () -> config("vcf.example.test", TOKEN));
        rejects(scenario, "non-HTTP scheme", () -> config("ftp://vcf.example.test", TOKEN));
        rejects(scenario, "no host", () -> config("https://", TOKEN));
        rejects(scenario, "userinfo in base URL", () -> config("https://admin@vcf.example.test", TOKEN));
        rejects(scenario, "non-root path", () -> config("https://vcf.example.test/sddc-lcm", TOKEN));
        rejects(scenario, "query in base URL", () -> config("https://vcf.example.test/?tenant=a", TOKEN));
        rejects(scenario, "fragment in base URL", () -> config("https://vcf.example.test/#top", TOKEN));
        rejects(scenario, "blank token", () -> config("https://vcf.example.test", "  "));
        rejects(scenario, "control character in token",
                () -> config("https://vcf.example.test", "tok" + (char) 0x01 + "en"));
        rejects(scenario, "control character in correlation id",
                () -> new SddcLcmSupportBundleClient.Config(
                        "https://vcf.example.test", TOKEN, "corr\nid", null, null, null));
        rejects(scenario, "non-positive poll interval",
                () -> new SddcLcmSupportBundleClient.Config(
                        "https://vcf.example.test", TOKEN, null, Duration.ZERO, null, null));
        rejects(scenario, "negative poll timeout",
                () -> new SddcLcmSupportBundleClient.Config(
                        "https://vcf.example.test", TOKEN, null, null, Duration.ofSeconds(-1), null));

        SddcLcmSupportBundleClient accepted =
                SddcLcmSupportBundleClient.create(config("https://vcf.example.test/", TOKEN));
        WireVerifier.check(
                "[" + scenario + "] a bare trailing slash must be an acceptable base URL",
                accepted != null);

        WireVerifier.silent(scenario, mock);
    }

    private static void mixedRunWithPruning(MockSddcLcm mock) throws Exception {
        String scenario = "mixed collection run with pruning";
        mock.reset();

        mock.generateAccepts(CMP_A, TASK_A);
        mock.taskRunsThrough(TASK_A, of("PENDING", "RUNNING", "SUCCEEDED"));
        mock.listReturns(CMP_A, of(A2, A4, A1, A3));
        mock.deleteAccepts(CMP_A, A1.id(), TASK_DEL_1);
        mock.taskRunsThrough(TASK_DEL_1, of("RUNNING", "SUCCEEDED"));
        mock.deleteAccepts(CMP_A, A2.id(), TASK_DEL_2);
        mock.taskRunsThrough(TASK_DEL_2, of("SUCCEEDED"));

        mock.generateAccepts(CMP_B, TASK_B);
        mock.taskFailsAt(TASK_B, of("SCHEDULED", "RUNNING", "RUNNING", "FAILED"),
                FAILED_STAGE, FAILED_MESSAGE);

        mock.generateAccepts(CMP_C, TASK_C);
        mock.taskRunsThrough(TASK_C, of("RUNNING", "SUCCEEDED"));
        mock.listReturns(CMP_C, of(C1));

        Duration interval = Duration.ofMillis(20);
        SddcLcmSupportBundleClient client =
                SddcLcmSupportBundleClient.create(
                        new SddcLcmSupportBundleClient.Config(
                                mock.baseUrl(), TOKEN, CORRELATION, interval,
                                Duration.ofSeconds(20), null));

        long startedAt = System.nanoTime();
        SddcLcmSupportBundleClient.CollectionReport report =
                client.collect(
                        new SddcLcmSupportBundleClient.CollectionPlan(
                                of(
                                        new SddcLcmSupportBundleClient.ComponentRequest(CMP_A, null),
                                        new SddcLcmSupportBundleClient.ComponentRequest(CMP_B, 14),
                                        new SddcLcmSupportBundleClient.ComponentRequest(CMP_C, 0)),
                                2));
        long elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000L;

        List<WireVerifier.Expect> expected = new ArrayList<>();
        expected.add(generate(CMP_A, "{}"));
        expected.add(task(TASK_A));
        expected.add(task(TASK_A));
        expected.add(task(TASK_A));
        expected.add(list(CMP_A));
        expected.add(delete(CMP_A, A1.id()));
        expected.add(task(TASK_DEL_1));
        expected.add(task(TASK_DEL_1));
        expected.add(delete(CMP_A, A2.id()));
        expected.add(task(TASK_DEL_2));
        expected.add(generate(CMP_B, "{\"lookBackWindow\":14}"));
        expected.add(task(TASK_B));
        expected.add(task(TASK_B));
        expected.add(task(TASK_B));
        expected.add(task(TASK_B));
        expected.add(generate(CMP_C, "{\"lookBackWindow\":0}"));
        expected.add(task(TASK_C));
        expected.add(task(TASK_C));
        expected.add(list(CMP_C));

        WireVerifier.verify(scenario, mock, TOKEN, CORRELATION, expected);
        WireVerifier.polls(scenario, mock, TASK_A, 3);
        WireVerifier.polls(scenario, mock, TASK_DEL_1, 2);
        WireVerifier.polls(scenario, mock, TASK_DEL_2, 1);
        WireVerifier.polls(scenario, mock, TASK_B, 4);
        WireVerifier.polls(scenario, mock, TASK_C, 2);

        // Seven waits separate the twelve polls, so the run cannot have finished sooner.
        long floorMillis = 7L * interval.toMillis();
        WireVerifier.check(
                "[" + scenario + "] the run took " + elapsedMillis + "ms, which is below the "
                        + floorMillis + "ms floor implied by waiting PollInterval between polls",
                elapsedMillis >= floorMillis);

        List<SddcLcmSupportBundleClient.ComponentOutcome> outcomes = report.outcomes();
        WireVerifier.equal("[" + scenario + "] outcome count", 3, outcomes.size());

        SddcLcmSupportBundleClient.ComponentOutcome a = outcomes.get(0);
        WireVerifier.equal("[" + scenario + "] outcome 1 componentId", CMP_A, a.componentId());
        WireVerifier.equal("[" + scenario + "] outcome 1 task id", TASK_A, a.generationTaskId());
        WireVerifier.equal("[" + scenario + "] outcome 1 terminal status", "SUCCEEDED", a.terminalStatus());
        WireVerifier.check("[" + scenario + "] outcome 1 must carry the collected bundle", a.bundle() != null);
        WireVerifier.equal("[" + scenario + "] outcome 1 bundle id", A4.id(), a.bundle().id());
        WireVerifier.equal("[" + scenario + "] outcome 1 bundle name", A4.name(), a.bundle().name());
        WireVerifier.equal("[" + scenario + "] outcome 1 bundle size", A4.size(), a.bundle().sizeBytes());
        WireVerifier.equal("[" + scenario + "] outcome 1 bundle timestamp",
                A4.createdTimestamp(), a.bundle().createdTimestamp());
        WireVerifier.equal("[" + scenario + "] outcome 1 bundle url", A4.url(), a.bundle().url());
        WireVerifier.equal("[" + scenario + "] outcome 1 failed stage", null, a.failedStage());
        WireVerifier.equal("[" + scenario + "] outcome 1 message", null, a.message());
        WireVerifier.equal("[" + scenario + "] outcome 1 pruned bundles",
                of(A1.id(), A2.id()), a.prunedBundleIds());

        SddcLcmSupportBundleClient.ComponentOutcome b = outcomes.get(1);
        WireVerifier.equal("[" + scenario + "] outcome 2 componentId", CMP_B, b.componentId());
        WireVerifier.equal("[" + scenario + "] outcome 2 task id", TASK_B, b.generationTaskId());
        WireVerifier.equal("[" + scenario + "] outcome 2 terminal status", "FAILED", b.terminalStatus());
        WireVerifier.equal("[" + scenario + "] outcome 2 bundle", null, b.bundle());
        WireVerifier.equal("[" + scenario + "] outcome 2 failed stage", FAILED_STAGE, b.failedStage());
        WireVerifier.equal("[" + scenario + "] outcome 2 message", FAILED_MESSAGE, b.message());
        WireVerifier.equal("[" + scenario + "] outcome 2 pruned bundles", of(), b.prunedBundleIds());

        SddcLcmSupportBundleClient.ComponentOutcome c = outcomes.get(2);
        WireVerifier.equal("[" + scenario + "] outcome 3 componentId", CMP_C, c.componentId());
        WireVerifier.equal("[" + scenario + "] outcome 3 terminal status", "SUCCEEDED", c.terminalStatus());
        WireVerifier.check("[" + scenario + "] outcome 3 must carry the collected bundle", c.bundle() != null);
        WireVerifier.equal("[" + scenario + "] outcome 3 bundle id", C1.id(), c.bundle().id());
        WireVerifier.equal("[" + scenario + "] outcome 3 pruned bundles", of(), c.prunedBundleIds());
    }

    private static void withoutCorrelationId(MockSddcLcm mock) throws Exception {
        String scenario = "no correlation id and pruning disabled";
        mock.reset();

        mock.generateAccepts(CMP_A, TASK_A);
        mock.taskRunsThrough(TASK_A, of("RUNNING", "SUCCEEDED"));
        mock.listReturns(CMP_A, of(A2, A4, A1, A3));

        SddcLcmSupportBundleClient client =
                SddcLcmSupportBundleClient.create(
                        new SddcLcmSupportBundleClient.Config(
                                mock.baseUrl(), TOKEN, null, Duration.ofMillis(5),
                                Duration.ofSeconds(20), null));

        SddcLcmSupportBundleClient.CollectionReport report =
                client.collect(
                        new SddcLcmSupportBundleClient.CollectionPlan(
                                of(new SddcLcmSupportBundleClient.ComponentRequest(CMP_A, null)), 0));

        WireVerifier.verify(
                scenario,
                mock,
                TOKEN,
                null,
                of(generate(CMP_A, "{}"), task(TASK_A), task(TASK_A), list(CMP_A)));
        WireVerifier.polls(scenario, mock, TASK_A, 2);

        WireVerifier.equal("[" + scenario + "] outcome count", 1, report.outcomes().size());
        SddcLcmSupportBundleClient.ComponentOutcome only = report.outcomes().get(0);
        WireVerifier.equal("[" + scenario + "] bundle id", A4.id(), only.bundle().id());
        WireVerifier.equal("[" + scenario + "] pruning must be disabled", of(), only.prunedBundleIds());
    }

    private static void listingRejectedMidRun(MockSddcLcm mock) {
        String scenario = "listing rejected mid-run";
        mock.reset();

        mock.generateAccepts(CMP_A, TASK_A);
        mock.taskRunsThrough(TASK_A, of("RUNNING", "SUCCEEDED"));
        mock.listReturns(CMP_A, of(A4));
        mock.generateAccepts(CMP_D, TASK_D);
        mock.taskRunsThrough(TASK_D, of("SUCCEEDED"));
        mock.listReturns(CMP_D, of(D1));
        mock.failsWith("getComponentSupportBundles", CMP_D, 500,
                "SDDC_LCM_BUNDLE_INDEX_UNAVAILABLE", "The support bundle index is unavailable.");

        SddcLcmSupportBundleClient client =
                SddcLcmSupportBundleClient.create(
                        new SddcLcmSupportBundleClient.Config(
                                mock.baseUrl(), TOKEN, CORRELATION, Duration.ofMillis(5),
                                Duration.ofSeconds(20), null));

        SddcLcmSupportBundleClient.CollectionPlan plan =
                new SddcLcmSupportBundleClient.CollectionPlan(
                        of(
                                new SddcLcmSupportBundleClient.ComponentRequest(CMP_A, 7),
                                new SddcLcmSupportBundleClient.ComponentRequest(CMP_D, null)),
                        5);

        SddcLcmSupportBundleClient.CollectionException thrown = null;
        try {
            client.collect(plan);
        } catch (SddcLcmSupportBundleClient.CollectionException e) {
            thrown = e;
        }

        WireVerifier.check(
                "[" + scenario + "] collect must fail when a documented success status is not returned",
                thrown != null);
        WireVerifier.check(
                "[" + scenario + "] expected an API failure but got " + thrown.getClass().getSimpleName(),
                thrown instanceof SddcLcmSupportBundleClient.SddcLcmApiException);

        SddcLcmSupportBundleClient.SddcLcmApiException api =
                (SddcLcmSupportBundleClient.SddcLcmApiException) thrown;
        WireVerifier.equal("[" + scenario + "] failing operationId",
                "getComponentSupportBundles", api.operationId());
        WireVerifier.equal("[" + scenario + "] failing componentId", CMP_D, api.componentId());
        WireVerifier.equal("[" + scenario + "] HTTP status", 500, api.statusCode());
        WireVerifier.equal("[" + scenario + "] error code",
                "SDDC_LCM_BUNDLE_INDEX_UNAVAILABLE", api.errorCode());
        WireVerifier.equal("[" + scenario + "] error message",
                "The support bundle index is unavailable.", api.apiMessage());
        WireVerifier.equal("[" + scenario + "] error reference id", "ref-0001", api.referenceId());
        WireVerifier.withoutToken(scenario, TOKEN, api.getMessage());

        WireVerifier.equal("[" + scenario + "] the completed component must survive the failure",
                1, api.report().outcomes().size());
        SddcLcmSupportBundleClient.ComponentOutcome survived = api.report().outcomes().get(0);
        WireVerifier.equal("[" + scenario + "] surviving componentId", CMP_A, survived.componentId());
        WireVerifier.equal("[" + scenario + "] surviving bundle", A4.id(), survived.bundle().id());

        WireVerifier.verify(
                scenario,
                mock,
                TOKEN,
                CORRELATION,
                of(
                        generate(CMP_A, "{\"lookBackWindow\":7}"),
                        task(TASK_A),
                        task(TASK_A),
                        list(CMP_A),
                        generate(CMP_D, "{}"),
                        task(TASK_D),
                        list(CMP_D)));
    }

    private static void pollTimeout(MockSddcLcm mock) {
        String scenario = "task never reaches a terminal status";
        mock.reset();

        mock.generateAccepts(CMP_B, TASK_STUCK);
        mock.taskRunsThrough(TASK_STUCK, of("RUNNING"));
        mock.listReturns(CMP_B, of(A4));

        SddcLcmSupportBundleClient client =
                SddcLcmSupportBundleClient.create(
                        new SddcLcmSupportBundleClient.Config(
                                mock.baseUrl(), TOKEN, CORRELATION, Duration.ofSeconds(1),
                                Duration.ofMillis(100), null));

        SddcLcmSupportBundleClient.CollectionException thrown = null;
        try {
            client.collect(
                    new SddcLcmSupportBundleClient.CollectionPlan(
                            of(new SddcLcmSupportBundleClient.ComponentRequest(CMP_B, null)), 1));
        } catch (SddcLcmSupportBundleClient.CollectionException e) {
            thrown = e;
        }

        WireVerifier.check(
                "[" + scenario + "] collect must give up once PollTimeout has passed", thrown != null);
        WireVerifier.check(
                "[" + scenario + "] expected a poll timeout but got " + thrown.getClass().getSimpleName(),
                thrown instanceof SddcLcmSupportBundleClient.TaskPollTimeoutException);

        SddcLcmSupportBundleClient.TaskPollTimeoutException timeout =
                (SddcLcmSupportBundleClient.TaskPollTimeoutException) thrown;
        WireVerifier.equal("[" + scenario + "] timed-out operationId", "getTask", timeout.operationId());
        WireVerifier.equal("[" + scenario + "] timed-out componentId", CMP_B, timeout.componentId());
        WireVerifier.equal("[" + scenario + "] timed-out task id", TASK_STUCK, timeout.taskId());
        WireVerifier.equal("[" + scenario + "] last observed status", "RUNNING", timeout.lastStatus());
        WireVerifier.equal("[" + scenario + "] no outcome can be reported",
                0, timeout.report().outcomes().size());
        WireVerifier.withoutToken(scenario, TOKEN, timeout.getMessage());

        List<MockSddcLcm.Recorded> log = mock.log();
        WireVerifier.equal(
                "[" + scenario + "] no poll may start after the timeout expires",
                1,
                mock.taskPolls(TASK_STUCK));
        WireVerifier.equal("[" + scenario + "] off-contract requests", 0, mock.offContractRequests());
        for (MockSddcLcm.Recorded recorded : log) {
            WireVerifier.check(
                    "[" + scenario + "] a bundle listing or deletion must never follow a task that never"
                            + " reached a terminal status, but observed " + recorded,
                    recorded.operationId().equals("generateComponentSupportBundle")
                            || recorded.operationId().equals("getTask"));
        }
        WireVerifier.equal(
                "[" + scenario + "] only the generation request precedes the polls",
                "generateComponentSupportBundle",
                log.get(0).operationId());
    }

    // ------------------------------------------------------------------ helpers

    private static WireVerifier.Expect generate(String componentId, String body) {
        return new WireVerifier.Expect(
                "generateComponentSupportBundle",
                "POST",
                "/sddc-lcm/v1/components/" + componentId + "/support-bundles",
                body,
                true);
    }

    private static WireVerifier.Expect task(String taskId) {
        return new WireVerifier.Expect("getTask", "GET", "/sddc-lcm/v1/tasks/" + taskId, null, false);
    }

    private static WireVerifier.Expect list(String componentId) {
        return new WireVerifier.Expect(
                "getComponentSupportBundles",
                "GET",
                "/sddc-lcm/v1/components/" + componentId + "/support-bundles",
                null,
                false);
    }

    private static WireVerifier.Expect delete(String componentId, String bundleId) {
        return new WireVerifier.Expect(
                "deleteComponentSupportBundle",
                "DELETE",
                "/sddc-lcm/v1/components/" + componentId + "/support-bundles/" + bundleId,
                null,
                true);
    }

    private static SddcLcmSupportBundleClient.Config config(String baseUrl, String token) {
        return new SddcLcmSupportBundleClient.Config(baseUrl, token);
    }

    private static void rejects(
            String scenario, String what, Supplier<SddcLcmSupportBundleClient.Config> supplier) {
        try {
            SddcLcmSupportBundleClient.create(supplier.get());
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new WireVerifier.WireAssertionError(
                "[" + scenario + "] " + what + " must be rejected by create");
    }

    private interface Scenario {
        void run() throws Exception;
    }

    private static void run(String name, Scenario scenario) {
        try {
            scenario.run();
            System.out.println("PASS  " + name);
        } catch (Throwable t) {
            failures++;
            System.out.println("FAIL  " + name);
            System.out.println("      " + String.valueOf(t.getMessage()).replace("\n", "\n      "));
            if (!(t instanceof AssertionError)) {
                System.out.println("      (" + t.getClass().getName() + ")");
            }
        }
    }
}
