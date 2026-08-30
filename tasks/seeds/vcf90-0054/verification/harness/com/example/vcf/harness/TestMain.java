package com.example.vcf.harness;

import com.example.vcf.VcenterRemediationClient;
import com.example.vcf.VcenterRemediationClient.ApplyOptions;
import com.example.vcf.VcenterRemediationClient.Outcome;
import com.example.vcf.VcenterRemediationClient.PollOptions;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Drives {@link VcenterRemediationClient} against the loopback {@link MockVcenter} and checks the
 * requests it produced with {@link WireVerifier}.
 *
 * <p>Nothing here touches the network: the mock binds the loopback interface and the client is only
 * ever handed that address.
 *
 * <p>Exit code 0 means every scenario passed.
 */
public final class TestMain {

    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path LOG_DIR = Path.of("build", "request-logs");

    private static final long POLL_INTERVAL_MILLIS = 5;
    private static final int MAX_POLLS = 40;

    public static void main(String[] args) throws Exception {
        MockVcenter mock = new MockVcenter(CONTRACT);
        mock.start();
        try {
            if (!mock.baseUrl().startsWith("http://127.") && !mock.baseUrl().startsWith("http://[::1]")) {
                throw new IllegalStateException("the mock must bind loopback, bound " + mock.baseUrl());
            }
            System.out.println("mock vCenter (contract-pinned) listening on " + mock.baseUrl());
            System.out.println();

            List<String> failures = new ArrayList<>();
            failures.addAll(scenarioSucceedsAfterPolling(mock));
            failures.addAll(scenarioUnsetOptionalsAreOmitted(mock));
            failures.addAll(scenarioTaskFails(mock));
            failures.addAll(scenarioPollLimitIsEnforced(mock));

            System.out.println();
            if (failures.isEmpty()) {
                System.out.println("ALL SCENARIOS PASSED");
                return;
            }
            System.out.println(failures.size() + " CHECK(S) FAILED");
            for (String failure : failures) {
                System.out.println("  - " + failure);
            }
            System.exit(1);
        } finally {
            mock.stop();
        }
    }

    // ------------------------------------------------------------ scenario 1

    /**
     * The remediation task walks PENDING, RUNNING, BLOCKED, RUNNING, SUCCEEDED. BLOCKED is not
     * terminal, so a client that stops there settles on the wrong answer and polls too few times.
     */
    private static List<String> scenarioSucceedsAfterPolling(MockVcenter mock) throws Exception {
        String name = "succeeds-after-polling";
        mock.reset(name, List.of("PENDING", "RUNNING", "BLOCKED", "RUNNING", "SUCCEEDED"), null);

        WireVerifier verifier = new WireVerifier(CONTRACT);
        verifier.scenario(name);

        String cluster = "domain-c1007";
        ApplyOptions apply = new ApplyOptions();
        apply.hosts = List.of("host-101", "host-118");
        apply.acceptEula = Boolean.TRUE;
        // apply.commit stays unset -> "commit" must not appear in the body

        PollOptions poll = new PollOptions();
        poll.excludeResult = Boolean.TRUE;
        // poll.returnAll stays unset -> "return_all" must not appear in the query

        Outcome outcome = run(mock, verifier, cluster, apply, poll, name);
        if (outcome != null) {
            expect(verifier, "SUCCEEDED", outcome.status, "Outcome.status");
            expect(verifier, mock.taskId(), outcome.taskId, "Outcome.taskId");
            expect(verifier, 5, outcome.pollCount,
                    "Outcome.pollCount — the task reported PENDING, RUNNING, BLOCKED, RUNNING, SUCCEEDED, "
                            + "so it takes five polls to observe a terminal status");
            expect(verifier, null, outcome.errorMessage, "Outcome.errorMessage on a successful task");
        }

        List<MockVcenter.Recorded> log = mock.log();
        verifier.verifyStayedInsideContract(mock);
        verifier.verifyCallSequence(mock, 5);
        if (log.size() >= 2) {
            verifier.verifyLogin(log.get(0), basicAuth(mock));
            verifier.verifyApply(log.get(1), cluster, mock.sessionToken(), Json.obj(
                    "hosts", List.of("host-101", "host-118"),
                    "accept_eula", Boolean.TRUE));
        }
        for (MockVcenter.Recorded rec : mock.logFor("Cis.Tasks_get")) {
            verifier.verifyPoll(rec, mock.taskId(), mock.sessionToken(), Map.of("exclude_result", "true"));
        }

        return report(name, verifier, mock);
    }

    // ------------------------------------------------------------ scenario 2

    /**
     * Nothing optional is really supplied: a whitespace-only commit, an empty host list and no GetSpec
     * properties. The body must carry a single member and the polls must carry no query string.
     * {@code acceptEula = false} is supplied, though, so it has to be on the wire as {@code false}.
     */
    private static List<String> scenarioUnsetOptionalsAreOmitted(MockVcenter mock) throws Exception {
        String name = "unset-optionals-are-omitted";
        mock.reset(name, List.of("PENDING", "RUNNING", "SUCCEEDED"), null);

        WireVerifier verifier = new WireVerifier(CONTRACT);
        verifier.scenario(name);

        String cluster = "domain-c88";
        ApplyOptions apply = new ApplyOptions();
        apply.commit = " \t";
        apply.hosts = List.of();
        apply.acceptEula = Boolean.FALSE;

        PollOptions poll = new PollOptions();

        Outcome outcome = run(mock, verifier, cluster, apply, poll, name);
        if (outcome != null) {
            expect(verifier, "SUCCEEDED", outcome.status, "Outcome.status");
            expect(verifier, mock.taskId(), outcome.taskId, "Outcome.taskId");
            expect(verifier, 3, outcome.pollCount, "Outcome.pollCount");
        }

        List<MockVcenter.Recorded> log = mock.log();
        verifier.verifyStayedInsideContract(mock);
        verifier.verifyCallSequence(mock, 3);
        if (log.size() >= 2) {
            verifier.verifyLogin(log.get(0), basicAuth(mock));
            verifier.verifyApply(log.get(1), cluster, mock.sessionToken(),
                    Json.obj("accept_eula", Boolean.FALSE));
        }
        for (MockVcenter.Recorded rec : mock.logFor("Cis.Tasks_get")) {
            verifier.verifyPoll(rec, mock.taskId(), mock.sessionToken(), Map.of());
        }

        return report(name, verifier, mock);
    }

    // ------------------------------------------------------------ scenario 3

    /** The task settles on FAILED. That is an outcome to report, not an exception to raise. */
    private static List<String> scenarioTaskFails(MockVcenter mock) throws Exception {
        String name = "task-fails";
        String failure = "Remediation failed on host host-118: the host could not enter maintenance mode.";
        mock.reset(name, List.of("PENDING", "RUNNING", "RUNNING", "FAILED"), failure);

        WireVerifier verifier = new WireVerifier(CONTRACT);
        verifier.scenario(name);

        String cluster = "domain-c2041";
        ApplyOptions apply = new ApplyOptions();
        apply.commit = "a4f0b2c9-7f21-4f0e-9c3d-6b5a1e2d4c88";
        // apply.hosts and apply.acceptEula stay unset -> neither property may appear in the body

        PollOptions poll = new PollOptions();
        poll.returnAll = Boolean.TRUE;
        poll.excludeResult = Boolean.FALSE;

        Outcome outcome = run(mock, verifier, cluster, apply, poll, name);
        if (outcome != null) {
            expect(verifier, "FAILED", outcome.status, "Outcome.status");
            expect(verifier, mock.taskId(), outcome.taskId, "Outcome.taskId");
            expect(verifier, 4, outcome.pollCount, "Outcome.pollCount");
            expect(verifier, failure, outcome.errorMessage,
                    "Outcome.errorMessage — error.messages[0].default_message of the failed task");
        }

        List<MockVcenter.Recorded> log = mock.log();
        verifier.verifyStayedInsideContract(mock);
        verifier.verifyCallSequence(mock, 4);
        if (log.size() >= 2) {
            verifier.verifyLogin(log.get(0), basicAuth(mock));
            verifier.verifyApply(log.get(1), cluster, mock.sessionToken(), Json.obj(
                    "commit", "a4f0b2c9-7f21-4f0e-9c3d-6b5a1e2d4c88"));
        }
        Map<String, String> expectedQuery = new LinkedHashMap<>();
        expectedQuery.put("return_all", "true");
        expectedQuery.put("exclude_result", "false");
        for (MockVcenter.Recorded rec : mock.logFor("Cis.Tasks_get")) {
            verifier.verifyPoll(rec, mock.taskId(), mock.sessionToken(), expectedQuery);
        }

        return report(name, verifier, mock);
    }

    // ------------------------------------------------------------ scenario 4

    /** A task that never settles must stop after exactly the configured number of polls. */
    private static List<String> scenarioPollLimitIsEnforced(MockVcenter mock) throws Exception {
        String name = "poll-limit-is-enforced";
        int maxPolls = 3;
        mock.reset(name, List.of("PENDING", "RUNNING", "BLOCKED"), null);

        WireVerifier verifier = new WireVerifier(CONTRACT);
        verifier.scenario(name);

        String cluster = "domain-c-timeout";
        ApplyOptions apply = new ApplyOptions();
        PollOptions poll = new PollOptions();
        VcenterRemediationClient client = new VcenterRemediationClient(mock.baseUrl(), 0, maxPolls);

        boolean threw = false;
        try {
            String token = client.login(mock.username(), mock.password());
            expect(verifier, mock.sessionToken(), token,
                    "login() must return the session token the appliance issued");
            client.remediateCluster(cluster, apply, poll);
        } catch (Throwable expected) {
            threw = true;
        }
        expect(verifier, Boolean.TRUE, threw,
                "remediateCluster() must throw when the task is still non-terminal at maxPolls");

        List<MockVcenter.Recorded> log = mock.log();
        verifier.verifyStayedInsideContract(mock);
        verifier.verifyCallSequence(mock, maxPolls);
        if (log.size() >= 2) {
            verifier.verifyLogin(log.get(0), basicAuth(mock));
            verifier.verifyApply(log.get(1), cluster, mock.sessionToken(), Map.of());
        }
        for (MockVcenter.Recorded rec : mock.logFor("Cis.Tasks_get")) {
            verifier.verifyPoll(rec, mock.taskId(), mock.sessionToken(), Map.of());
        }

        return report(name, verifier, mock);
    }

    // ---------------------------------------------------------------- shared

    private static Outcome run(MockVcenter mock, WireVerifier verifier, String cluster,
                               ApplyOptions apply, PollOptions poll, String scenario) {
        VcenterRemediationClient client =
                new VcenterRemediationClient(mock.baseUrl(), POLL_INTERVAL_MILLIS, MAX_POLLS);
        try {
            String token = client.login(mock.username(), mock.password());
            expect(verifier, mock.sessionToken(), token,
                    "login() must return the session token the appliance issued");
            Outcome outcome = client.remediateCluster(cluster, apply, poll);
            if (outcome == null) {
                expect(verifier, "an Outcome", "null", "remediateCluster() return value");
            }
            return outcome;
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            expect(verifier, "no exception", sw.toString().trim(),
                    "the client threw while remediating cluster " + cluster);
            return null;
        }
    }

    private static void expect(WireVerifier verifier, Object expected, Object actual, String what) {
        verifier.scenarioCheck(expected, actual, what);
    }

    private static String basicAuth(MockVcenter mock) {
        return "Basic " + Base64.getEncoder().encodeToString(
                (mock.username() + ":" + mock.password()).getBytes(StandardCharsets.UTF_8));
    }

    private static List<String> report(String scenario, WireVerifier verifier, MockVcenter mock) {
        try {
            mock.writeLog(LOG_DIR.resolve(scenario + ".json"));
        } catch (Exception e) {
            System.out.println("  (could not write the request log: " + e + ")");
        }
        List<String> failures = verifier.failures();
        if (failures.isEmpty()) {
            System.out.println("PASS  " + scenario + "  (" + mock.log().size() + " requests)");
        } else {
            System.out.println("FAIL  " + scenario + "  (" + failures.size() + " check(s))");
            for (String failure : failures) {
                System.out.println("        " + failure);
            }
        }
        return failures;
    }
}
