import com.example.vcfa.VcfaDeploymentActionClient;
import com.example.vcfa.VcfaDeploymentActionClient.ActionOutcome;
import com.example.vcfa.VcfaDeploymentActionClient.VcfaApiException;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Harness for the VCF Automation deployment-action precheck gate.
 *
 * <p>PROTECTED FILE - the harness owns this. Do not edit it.
 *
 * <p>Each scenario stands up a fresh {@link MockVcfaServer} on the loopback interface, drives
 * {@link VcfaDeploymentActionClient} against it, then hands the server's request log to
 * {@link Verifier}. Nothing here reaches a real VMware endpoint.
 *
 * <p>Usage: {@code java -cp build TestMain [repoRoot]}
 */
public final class TestMain {

    private static final String TOKEN = "eyJhbGciOiJIUzI1NiJ9.harness-token.sig";
    private static final String DEPLOYMENT = "b3f1c9d2-7a54-4e18-9c30-2f6a8d15e447";
    private static final String MISSING_DEPLOYMENT = "00000000-0000-0000-0000-0000000000ff";

    private static final String POWER_OFF_ID = "e7d41a6c-2b90-4f5d-8a11-c6b207e93f4a";
    private static final String SNAPSHOT_ID = "1c58e0b7-9d3f-42aa-b7e6-5048d1ac2b93";

    private final Path repoRoot;
    private final Verifier v = new Verifier();
    private int scenarioSeq;

    private TestMain(Path repoRoot) {
        this.repoRoot = repoRoot;
    }

    public static void main(String[] args) throws Exception {
        Path root = Paths.get(args.length > 0 ? args[0] : ".").toAbsolutePath().normalize();
        java.nio.file.Files.createDirectories(root.resolve("build"));
        TestMain harness = new TestMain(root);
        harness.run();
        System.exit(harness.v.report() ? 0 : 1);
    }

    private void run() {
        guard(this::submitsValidActionWithNoOptionalFields);
        guard(this::submitsValidActionWithReasonAndInputs);
        guard(this::treatsBlankReasonAndEmptyInputsAsUnset);
        guard(this::gatesActionThatIsNotValidForCurrentState);
        guard(this::gatesActionWhoseValidFieldIsNotBooleanTrue);
        guard(this::gatesActionMissingFromThePrecheck);
        guard(this::gatesWhenThePrecheckItselfFails);
    }

    @FunctionalInterface
    private interface Scenario {
        void run() throws Exception;
    }

    /** Runs a scenario, turning an unexpected blow-up into a failed check rather than a dead run. */
    private void guard(Scenario scenario) {
        try {
            scenario.run();
        } catch (Throwable t) {
            v.check(
                    "the scenario ran to completion",
                    false,
                    t.getClass().getName() + ": " + t.getMessage());
        }
    }

    // -------------------------------------------------------------- fixtures

    /** A deployment offering one runnable action and one that is not valid right now. */
    private MockVcfaServer applianceWithBothActions() throws Exception {
        return newServer()
                .withDeployment(
                        DEPLOYMENT,
                        List.of(
                                MockVcfaServer.action(POWER_OFF_ID, "Deployment.PowerOff", true),
                                MockVcfaServer.action(SNAPSHOT_ID, "Deployment.Snapshot", false)));
    }

    private MockVcfaServer newServer() throws Exception {
        Path log = repoRoot.resolve("build/requests-" + (++scenarioSeq) + ".jsonl");
        return new MockVcfaServer(repoRoot.resolve("docs/contract.json"), log, TOKEN);
    }

    private VcfaDeploymentActionClient clientFor(MockVcfaServer server) {
        return new VcfaDeploymentActionClient(server.baseUrl(), TOKEN);
    }

    /**
     * Drives a scenario in which the client is expected to gate rather than throw. An exception is
     * recorded as a failure but does not abort the scenario, so the request log still gets checked -
     * a client that pushed the mutating call through and got rejected by the appliance should be
     * reported as "a POST reached the appliance", not merely as a stack trace.
     */
    private ActionOutcome attemptExpectingGate(
            MockVcfaServer server, String deploymentId, String actionName, String reason) {
        try {
            return clientFor(server).requestDeploymentAction(deploymentId, actionName, reason, null);
        } catch (VcfaApiException e) {
            v.check(
                    "the client gated instead of calling the appliance and failing",
                    false,
                    "it threw " + e.getMessage());
            return null;
        }
    }

    // ------------------------------------------------------------ scenarios

    private void submitsValidActionWithNoOptionalFields() throws Exception {
        v.scenario("A valid action with no reason and no inputs is submitted");
        try (MockVcfaServer server = applianceWithBothActions().start()) {
            ActionOutcome outcome =
                    clientFor(server).requestDeploymentAction(DEPLOYMENT, "Deployment.PowerOff", null, null);

            v.check("the action was submitted", outcome.isSubmitted(), "outcome was " + outcome);
            v.equal("no gate fired", null, outcome.gate());
            v.equal("the validated action id was used", POWER_OFF_ID, outcome.actionId());
            v.equal("the created request id was read back", "req-0001", outcome.requestId());
            v.equal("the request status was read back", "INITIALIZATION", outcome.status());

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 2);
            v.assertOnlyContractOperations(log);
            v.assertPrecheckPrecededSubmit(log);
            v.assertPrecheckWireShape(log.get(0), DEPLOYMENT, TOKEN);
            v.equal("the appliance accepted the precheck", 200, log.get(0).get("responseStatus"));

            Map<String, Object> expectedBody = new LinkedHashMap<>();
            expectedBody.put("actionId", POWER_OFF_ID);
            v.assertMutatingWireShape(log.get(1), DEPLOYMENT, TOKEN, expectedBody);
            v.equal("the appliance accepted the submit", 200, log.get(1).get("responseStatus"));
        }
    }

    private void submitsValidActionWithReasonAndInputs() throws Exception {
        v.scenario("A reason and inputs supplied by the caller are sent verbatim");
        Map<String, Object> inputs = new LinkedHashMap<>();
        inputs.put("powerOffMode", "HARD");
        inputs.put("graceSeconds", 30);
        String reason = "Scheduled maintenance window";

        try (MockVcfaServer server = applianceWithBothActions().start()) {
            ActionOutcome outcome =
                    clientFor(server)
                            .requestDeploymentAction(DEPLOYMENT, "Deployment.PowerOff", reason, inputs);

            v.check("the action was submitted", outcome.isSubmitted(), "outcome was " + outcome);

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 2);
            v.assertOnlyContractOperations(log);
            v.assertPrecheckPrecededSubmit(log);

            Map<String, Object> expectedInputs = new LinkedHashMap<>();
            expectedInputs.put("powerOffMode", "HARD");
            expectedInputs.put("graceSeconds", 30);
            Map<String, Object> expectedBody = new LinkedHashMap<>();
            expectedBody.put("actionId", POWER_OFF_ID);
            expectedBody.put("reason", reason);
            expectedBody.put("inputs", expectedInputs);
            v.assertMutatingWireShape(log.get(1), DEPLOYMENT, TOKEN, expectedBody);
            v.equal("the appliance accepted the submit", 200, log.get(1).get("responseStatus"));
        }
    }

    private void treatsBlankReasonAndEmptyInputsAsUnset() throws Exception {
        v.scenario("A blank reason and an empty inputs map are omitted, not sent empty");
        try (MockVcfaServer server = applianceWithBothActions().start()) {
            ActionOutcome outcome =
                    clientFor(server)
                            .requestDeploymentAction(
                                    DEPLOYMENT, "Deployment.PowerOff", " \u2003\t", new LinkedHashMap<>());

            v.check("the action was submitted", outcome.isSubmitted(), "outcome was " + outcome);

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 2);

            Map<String, Object> expectedBody = new LinkedHashMap<>();
            expectedBody.put("actionId", POWER_OFF_ID);
            v.assertMutatingWireShape(log.get(1), DEPLOYMENT, TOKEN, expectedBody);
            v.assertFieldOmitted(log.get(1), "reason");
            v.assertFieldOmitted(log.get(1), "inputs");
        }
    }

    private void gatesActionThatIsNotValidForCurrentState() throws Exception {
        v.scenario("An action the precheck reports as not valid is never submitted");
        try (MockVcfaServer server = applianceWithBothActions().start()) {
            ActionOutcome outcome =
                    attemptExpectingGate(server, DEPLOYMENT, "Deployment.Snapshot", "why not");

            if (outcome != null) {
                v.check("the action was not submitted", !outcome.isSubmitted(), "outcome was " + outcome);
                v.equal("the ACTION_NOT_VALID gate fired", "ACTION_NOT_VALID", outcome.gate());
                v.equal("the matched invalid action id was preserved", SNAPSHOT_ID, outcome.actionId());
                v.equal("no request id was invented", null, outcome.requestId());
                v.equal("no status was invented", null, outcome.status());
            }

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 1);
            v.assertOnlyContractOperations(log);
            v.assertPrecheckWireShape(log.get(0), DEPLOYMENT, TOKEN);
            v.assertNothingMutated(log);
        }
    }

    private void gatesActionWhoseValidFieldIsNotBooleanTrue() throws Exception {
        v.scenario("An action with a non-boolean valid value is never submitted");
        Map<String, Object> action =
                MockVcfaServer.action(POWER_OFF_ID, "Deployment.PowerOff", true);
        action.put("valid", "true");
        try (MockVcfaServer server =
                newServer().withDeployment(DEPLOYMENT, List.of(action)).start()) {
            ActionOutcome outcome =
                    attemptExpectingGate(server, DEPLOYMENT, "Deployment.PowerOff", null);

            if (outcome != null) {
                v.check("the action was not submitted", !outcome.isSubmitted(), "outcome was " + outcome);
                v.equal("the ACTION_NOT_VALID gate fired", "ACTION_NOT_VALID", outcome.gate());
                v.equal("the matched action id was preserved", POWER_OFF_ID, outcome.actionId());
            }

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 1);
            v.assertOnlyContractOperations(log);
            v.assertNothingMutated(log);
        }
    }

    private void gatesActionMissingFromThePrecheck() throws Exception {
        v.scenario("An action the precheck does not list at all is never submitted");
        try (MockVcfaServer server = applianceWithBothActions().start()) {
            ActionOutcome outcome = attemptExpectingGate(server, DEPLOYMENT, "Deployment.Delete", null);

            if (outcome != null) {
                v.check("the action was not submitted", !outcome.isSubmitted(), "outcome was " + outcome);
                v.equal("the ACTION_NOT_AVAILABLE gate fired", "ACTION_NOT_AVAILABLE", outcome.gate());
                v.equal("no action id was invented", null, outcome.actionId());
            }

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 1);
            v.assertOnlyContractOperations(log);
            v.assertNothingMutated(log);
        }
    }

    private void gatesWhenThePrecheckItselfFails() throws Exception {
        v.scenario("A precheck that fails outright stops the submit");
        try (MockVcfaServer server = applianceWithBothActions().start()) {
            VcfaApiException thrown = null;
            try {
                clientFor(server)
                        .requestDeploymentAction(MISSING_DEPLOYMENT, "Deployment.PowerOff", null, null);
            } catch (VcfaApiException e) {
                thrown = e;
            }

            v.check("the precheck failure was surfaced", thrown != null, "no VcfaApiException was thrown");
            if (thrown != null) {
                v.equal("the failure carries HTTP 404", 404, thrown.statusCode());
                v.equal("the failure names the precheck operation", "getDeploymentActions", thrown.operationId());
            }

            List<Map<String, Object>> log = server.readLog();
            v.assertRequestCount(log, 1);
            v.assertOnlyContractOperations(log);
            v.assertNothingMutated(log);
        }
    }
}
