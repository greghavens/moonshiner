package com.broadcom.vcf.sddclcm.harness;

import com.broadcom.vcf.sddclcm.SddcLcmClient;

import java.nio.file.Path;
import java.time.Duration;
import java.net.http.HttpClient;
import java.util.ArrayList;
import java.util.List;

/**
 * Protected verification entry point.
 *
 * <p>Each scenario starts the contract-pinned loopback fixture on an ephemeral
 * {@code 127.0.0.1} port, drives {@link SddcLcmClient} against it and asserts the
 * recorded wire shape. No live VMware endpoint is contacted.
 *
 * <p>This file is part of the protected harness. Do not modify it.
 */
public final class TestMain {

    private static final int SCENARIO_COUNT = 12;
    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path REQUEST_LOG = Path.of("build", "request-log.json");

    private interface Scenario {
        void run(ContractMock mock, TokenAuthority authority, SddcLcmClient client) throws Exception;
    }

    public static void main(String[] args) {
        List<String> failed = new ArrayList<>();

        run("fleet component upgrade resumes across an access token expiry",
                ContractMock.Mode.EXPIRE_DURING_APPLY, failed, TestMain::upgradeResumesAfterRenewal);
        run("a null HttpClient selects the JDK default client",
                ContractMock.Mode.EXPIRE_DURING_APPLY, failed, TestMain::nullHttpClientUsesDefault);
        run("performBackup false is encoded rather than omitted",
                ContractMock.Mode.EXPIRE_DURING_APPLY, failed, TestMain::falseBackupIsEncoded);
        run("a second challenge on the resumed apply is not renewed again",
                ContractMock.Mode.SECOND_UNAUTHORIZED, failed, TestMain::secondChallengeIsFatal);
        run("a failed precheck stops the workflow before apply",
                ContractMock.Mode.PRECHECK_FAILS, failed, TestMain::failedPrecheckStopsWorkflow);
        run("a canceled precheck stops the workflow before apply",
                ContractMock.Mode.PRECHECK_CANCELED, failed, TestMain::canceledPrecheckStopsWorkflow);
        run("a server error on apply is not treated as an expiry",
                ContractMock.Mode.APPLY_SERVER_ERROR, failed, TestMain::serverErrorIsNotRenewed);
        run("a missing fleet component is rejected before depot resolution",
                ContractMock.Mode.EXPIRE_DURING_APPLY, failed, TestMain::unknownFleetComponentIsRejected);
        run("an instance-scoped entry in a fleet listing is rejected",
                ContractMock.Mode.NON_FLEET_LISTING, failed, TestMain::malformedFleetListingIsRejected);
        run("duplicate matching fleet components are rejected",
                ContractMock.Mode.DUPLICATE_FLEET_COMPONENT, failed, TestMain::malformedFleetListingIsRejected);
        run("a healthy response reporting down stops the workflow",
                ContractMock.Mode.HEALTH_DOWN, failed, TestMain::unhealthyServiceStopsWorkflow);
        run("unusable arguments are rejected before any request is sent",
                ContractMock.Mode.EXPIRE_DURING_APPLY, failed, TestMain::argumentsAreValidatedUpFront);

        System.out.println();
        if (failed.isEmpty()) {
            System.out.println("OK  " + SCENARIO_COUNT + " scenarios passed");
            return;
        }
        System.out.println("FAIL  " + failed.size() + " of " + SCENARIO_COUNT + " scenarios failed: " + failed);
        System.exit(1);
    }

    // ------------------------------------------------------------- scenarios

    private static void upgradeResumesAfterRenewal(ContractMock mock, TokenAuthority authority, SddcLcmClient client)
            throws Exception {
        SddcLcmClient.UpgradeOutcome outcome = client.upgradeFleetComponent(upgradeRequest());
        WireVerifier.verifyUpgradeWire(mock.requestLog(), authority, outcome);
        expect(authority.refreshCount() == 1,
                "the session must be renewed exactly once, saw " + authority.refreshCount() + " renewals");
    }

    private static void nullHttpClientUsesDefault(ContractMock mock, TokenAuthority authority,
                                                  SddcLcmClient ignored) throws Exception {
        SddcLcmClient client = SddcLcmClient.create(mock.serviceRootUrl(), authority.newAccessTokenSource(), null);
        SddcLcmClient.UpgradeOutcome outcome = client.upgradeFleetComponent(upgradeRequest());
        WireVerifier.verifyUpgradeWire(mock.requestLog(), authority, outcome);
    }

    private static void falseBackupIsEncoded(ContractMock mock, TokenAuthority authority,
                                             SddcLcmClient client) throws Exception {
        SddcLcmClient.UpgradeRequest request = upgradeRequest();
        request = new SddcLcmClient.UpgradeRequest(request.componentType(), request.targetVersion(),
                request.fleetDepotFqdn(), request.fleetDepotCertificate(), request.correlationId(), false);
        SddcLcmClient.UpgradeOutcome outcome = client.upgradeFleetComponent(request);
        WireVerifier.verifyUpgradeWire(mock.requestLog(), authority, outcome, false);
    }

    private static void secondChallengeIsFatal(ContractMock mock, TokenAuthority authority, SddcLcmClient client)
            throws Exception {
        SddcLcmClient.ApiException failure = expectFailure(SddcLcmClient.ApiException.class,
                () -> client.upgradeFleetComponent(upgradeRequest()));
        expect(failure.statusCode() == 401, "a repeated challenge must surface HTTP 401, saw " + failure.statusCode());
        expect("performComponentAction".equals(failure.operationId()),
                "the failure must name performComponentAction, named " + failure.operationId());
        expectNoTokenLeak(failure);

        List<ContractMock.RecordedRequest> log = mock.requestLog();
        expect(authority.refreshCount() == 1,
                "the session may be renewed only once, saw " + authority.refreshCount() + " renewals");
        expect(count(log, "performComponentAction") == 3,
                "expected one precheck and two apply submissions, saw "
                        + count(log, "performComponentAction") + " component actions");
        expect(log.stream().noneMatch(request -> request.rawTarget().contains(ContractMock.APPLY_TASK_ID)),
                "no apply task may be polled once the resumed submission is refused");

        WireVerifier.Failures failures = new WireVerifier.Failures(log);
        WireVerifier.verifyInvariants(log, authority, 2, failures);
        failures.raise();
    }

    private static void failedPrecheckStopsWorkflow(ContractMock mock, TokenAuthority authority, SddcLcmClient client)
            throws Exception {
        terminalPrecheckStopsWorkflow(mock, authority, client, "FAILED");
    }

    private static void canceledPrecheckStopsWorkflow(ContractMock mock, TokenAuthority authority,
                                                       SddcLcmClient client) throws Exception {
        terminalPrecheckStopsWorkflow(mock, authority, client, "CANCELED");
    }

    private static void terminalPrecheckStopsWorkflow(ContractMock mock, TokenAuthority authority,
                                                       SddcLcmClient client, String terminalStatus) throws Exception {
        SddcLcmClient.TaskFailureException failure = expectFailure(SddcLcmClient.TaskFailureException.class,
                () -> client.upgradeFleetComponent(upgradeRequest()));
        expect(ContractMock.PRECHECK_TASK_ID.equals(failure.taskId()),
                "the failure must name the precheck task, named " + failure.taskId());
        expect("precheck".equals(failure.taskType()),
                "the failure must report the precheck task type, reported " + failure.taskType());
        expect(terminalStatus.equals(failure.status()),
                "the failure must report the terminal status " + terminalStatus + ", reported " + failure.status());
        expectNoTokenLeak(failure);

        List<ContractMock.RecordedRequest> log = mock.requestLog();
        expect(log.size() == 6, "expected exactly 6 requests before the workflow stops, saw " + log.size());
        expect(log.stream().noneMatch(request -> request.rawTarget().contains("action=apply")),
                "a failed precheck must not be followed by an apply submission");
        expect(authority.refreshCount() == 0,
                "a failed precheck must not renew the session, saw " + authority.refreshCount() + " renewals");
        verifyInvariants(log, authority, 0);
    }

    private static void serverErrorIsNotRenewed(ContractMock mock, TokenAuthority authority, SddcLcmClient client)
            throws Exception {
        SddcLcmClient.ApiException failure = expectFailure(SddcLcmClient.ApiException.class,
                () -> client.upgradeFleetComponent(upgradeRequest()));
        expect(failure.statusCode() == 500, "the failure must surface HTTP 500, surfaced " + failure.statusCode());
        expect("performComponentAction".equals(failure.operationId()),
                "the failure must name performComponentAction, named " + failure.operationId());
        expectNoTokenLeak(failure);

        List<ContractMock.RecordedRequest> log = mock.requestLog();
        expect(authority.refreshCount() == 0,
                "only an authentication challenge may renew the session, saw " + authority.refreshCount()
                        + " renewals");
        expect(log.size() == 7, "expected exactly 7 requests before the workflow stops, saw " + log.size());
        expect(count(log, "performComponentAction") == 2,
                "a server error must not be retried, saw " + count(log, "performComponentAction")
                        + " component actions");
        verifyInvariants(log, authority, 0);
    }

    private static void unknownFleetComponentIsRejected(ContractMock mock, TokenAuthority authority,
                                                        SddcLcmClient client) throws Exception {
        SddcLcmClient.UpgradeRequest request = new SddcLcmClient.UpgradeRequest(
                ContractMock.INSTANCE_COMPONENT_TYPE,
                ContractMock.TARGET_VERSION,
                ContractMock.FLEET_DEPOT_FQDN,
                ContractMock.FLEET_DEPOT_CERTIFICATE,
                ContractMock.CORRELATION_ID,
                true);
        SddcLcmClient.ProtocolException failure = expectFailure(SddcLcmClient.ProtocolException.class,
                () -> client.upgradeFleetComponent(request));
        expect("getComponents".equals(failure.operationId()),
                "the failure must name getComponents, named " + failure.operationId());
        expectNoTokenLeak(failure);

        List<ContractMock.RecordedRequest> log = mock.requestLog();
        expect(log.size() == 2, "expected exactly 2 requests before the workflow stops, saw " + log.size());
        expect(count(log, "resolveDepotComponents") == 0,
                "a component missing from the fleet listing must not reach the depot");
        expect(authority.refreshCount() == 0, "no renewal may happen in this scenario");
        verifyInvariants(log, authority, 0);
    }

    private static void malformedFleetListingIsRejected(ContractMock mock, TokenAuthority authority,
                                                         SddcLcmClient client) throws Exception {
        SddcLcmClient.ProtocolException failure = expectFailure(SddcLcmClient.ProtocolException.class,
                () -> client.upgradeFleetComponent(upgradeRequest()));
        expect("getComponents".equals(failure.operationId()),
                "the failure must name getComponents, named " + failure.operationId());
        expectNoTokenLeak(failure);

        List<ContractMock.RecordedRequest> log = mock.requestLog();
        expect(log.size() == 2, "expected exactly 2 requests before the workflow stops, saw " + log.size());
        expect(count(log, "resolveDepotComponents") == 0,
                "a malformed fleet listing must be rejected before depot resolution");
        expect(authority.refreshCount() == 0, "no renewal may happen in this scenario");
        verifyInvariants(log, authority, 0);
    }

    private static void unhealthyServiceStopsWorkflow(ContractMock mock, TokenAuthority authority,
                                                       SddcLcmClient client) throws Exception {
        SddcLcmClient.ProtocolException failure = expectFailure(SddcLcmClient.ProtocolException.class,
                () -> client.upgradeFleetComponent(upgradeRequest()));
        expect("getHealth".equals(failure.operationId()),
                "the failure must name getHealth, named " + failure.operationId());
        expectNoTokenLeak(failure);

        List<ContractMock.RecordedRequest> log = mock.requestLog();
        expect(log.size() == 1, "expected exactly 1 request before the workflow stops, saw " + log.size());
        expect(count(log, "getComponents") == 0, "an unhealthy service must not be queried for components");
        expect(authority.refreshCount() == 0, "no renewal may happen in this scenario");
        verifyInvariants(log, authority, 0);
    }

    private static void argumentsAreValidatedUpFront(ContractMock mock, TokenAuthority authority, SddcLcmClient client)
            throws Exception {
        String root = mock.serviceRootUrl();
        SddcLcmClient.AccessTokenSource tokens = authority.newAccessTokenSource();

        expectIllegalArgument("a null service root", () -> SddcLcmClient.create(null, tokens, HttpClient.newHttpClient()));
        expectIllegalArgument("a blank service root", () -> SddcLcmClient.create("  ", tokens, HttpClient.newHttpClient()));
        expectIllegalArgument("a schemeless service root",
                () -> SddcLcmClient.create("vcf.broadcom.com/sddc-lcm", tokens, HttpClient.newHttpClient()));
        expectIllegalArgument("a non HTTP service root",
                () -> SddcLcmClient.create("ftp://vcf.broadcom.com/sddc-lcm", tokens, HttpClient.newHttpClient()));
        expectIllegalArgument("a service root with a query",
                () -> SddcLcmClient.create(root + "?region=west", tokens, HttpClient.newHttpClient()));
        expectIllegalArgument("a service root with a fragment",
                () -> SddcLcmClient.create(root + "#lcm", tokens, HttpClient.newHttpClient()));
        expectIllegalArgument("a null token source", () -> SddcLcmClient.create(root, null, HttpClient.newHttpClient()));

        expectIllegalArgument("a null request", () -> client.upgradeFleetComponent(null));
        expectIllegalArgument("a blank component type",
                () -> client.upgradeFleetComponent(withComponentType(" ")));
        expectIllegalArgument("a blank target version",
                () -> client.upgradeFleetComponent(withTargetVersion("")));
        expectIllegalArgument("a blank fleet depot fqdn",
                () -> client.upgradeFleetComponent(withDepotFqdn("")));
        expectIllegalArgument("a blank fleet depot certificate",
                () -> client.upgradeFleetComponent(withDepotCertificate("   ")));
        expectIllegalArgument("a correlation id that is not a uuid",
                () -> client.upgradeFleetComponent(withCorrelationId("upgrade-42")));
        expectIllegalArgument("a null correlation id", () -> client.upgradeFleetComponent(withCorrelationId(null)));

        SddcLcmClient blankToken = SddcLcmClient.create(root, new SddcLcmClient.AccessTokenSource() {
            @Override
            public String currentAccessToken() {
                return "   ";
            }

            @Override
            public String refreshAccessToken() {
                return "   ";
            }
        }, HttpClient.newHttpClient());
        expectIllegalArgument("a blank access token", () -> blankToken.upgradeFleetComponent(upgradeRequest()));

        SddcLcmClient unsafeToken = SddcLcmClient.create(root, new SddcLcmClient.AccessTokenSource() {
            @Override
            public String currentAccessToken() {
                return TokenAuthority.INITIAL_ACCESS_TOKEN + "\r\nX-Injected: 1";
            }

            @Override
            public String refreshAccessToken() {
                return TokenAuthority.INITIAL_ACCESS_TOKEN + "\r\nX-Injected: 1";
            }
        }, HttpClient.newHttpClient());
        IllegalArgumentException unsafeTokenFailure = expectIllegalArgument("a header unsafe access token",
                () -> unsafeToken.upgradeFleetComponent(upgradeRequest()));
        expectNoTokenLeak(unsafeTokenFailure);

        expect(mock.requestLog().isEmpty(),
                "unusable arguments must be rejected before any request reaches the service, saw "
                        + mock.requestLog());
    }

    // -------------------------------------------------------------- plumbing

    private static SddcLcmClient.UpgradeRequest upgradeRequest() {
        return new SddcLcmClient.UpgradeRequest(
                ContractMock.COMPONENT_TYPE,
                ContractMock.TARGET_VERSION,
                ContractMock.FLEET_DEPOT_FQDN,
                ContractMock.FLEET_DEPOT_CERTIFICATE,
                ContractMock.CORRELATION_ID,
                true);
    }

    private static SddcLcmClient.UpgradeRequest withComponentType(String value) {
        SddcLcmClient.UpgradeRequest base = upgradeRequest();
        return new SddcLcmClient.UpgradeRequest(value, base.targetVersion(), base.fleetDepotFqdn(),
                base.fleetDepotCertificate(), base.correlationId(), base.performBackup());
    }

    private static SddcLcmClient.UpgradeRequest withTargetVersion(String value) {
        SddcLcmClient.UpgradeRequest base = upgradeRequest();
        return new SddcLcmClient.UpgradeRequest(base.componentType(), value, base.fleetDepotFqdn(),
                base.fleetDepotCertificate(), base.correlationId(), base.performBackup());
    }

    private static SddcLcmClient.UpgradeRequest withDepotFqdn(String value) {
        SddcLcmClient.UpgradeRequest base = upgradeRequest();
        return new SddcLcmClient.UpgradeRequest(base.componentType(), base.targetVersion(), value,
                base.fleetDepotCertificate(), base.correlationId(), base.performBackup());
    }

    private static SddcLcmClient.UpgradeRequest withDepotCertificate(String value) {
        SddcLcmClient.UpgradeRequest base = upgradeRequest();
        return new SddcLcmClient.UpgradeRequest(base.componentType(), base.targetVersion(), base.fleetDepotFqdn(),
                value, base.correlationId(), base.performBackup());
    }

    private static SddcLcmClient.UpgradeRequest withCorrelationId(String value) {
        SddcLcmClient.UpgradeRequest base = upgradeRequest();
        return new SddcLcmClient.UpgradeRequest(base.componentType(), base.targetVersion(), base.fleetDepotFqdn(),
                base.fleetDepotCertificate(), value, base.performBackup());
    }

    private static long count(List<ContractMock.RecordedRequest> log, String operationId) {
        return log.stream().filter(request -> operationId.equals(request.operationId())).count();
    }

    private static void verifyInvariants(List<ContractMock.RecordedRequest> log, TokenAuthority authority,
                                         int maxUnauthorized) {
        WireVerifier.Failures failures = new WireVerifier.Failures(log);
        WireVerifier.verifyInvariants(log, authority, maxUnauthorized, failures);
        failures.raise();
    }

    private static void expect(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void expectNoTokenLeak(Throwable failure) {
        for (Throwable current = failure; current != null; current = current.getCause()) {
            String message = String.valueOf(current.getMessage());
            expect(!message.contains(TokenAuthority.INITIAL_ACCESS_TOKEN)
                            && !message.contains(TokenAuthority.REPLACEMENT_ACCESS_TOKEN),
                    "an access token leaked into an error message: " + message);
        }
    }

    private interface Body {
        void run() throws Exception;
    }

    @SuppressWarnings("unchecked")
    private static <T extends Throwable> T expectFailure(Class<T> expected, Body body) {
        try {
            body.run();
        } catch (Throwable thrown) {
            if (expected.isInstance(thrown)) {
                return (T) thrown;
            }
            throw new AssertionError("expected " + expected.getSimpleName() + " but caught "
                    + thrown.getClass().getName() + ": " + thrown.getMessage(), thrown);
        }
        throw new AssertionError("expected " + expected.getSimpleName() + " but the call returned normally");
    }

    private static IllegalArgumentException expectIllegalArgument(String what, Body body) {
        try {
            body.run();
        } catch (IllegalArgumentException expected) {
            return expected;
        } catch (Throwable thrown) {
            throw new AssertionError("expected " + what + " to be rejected with IllegalArgumentException but caught "
                    + thrown.getClass().getName() + ": " + thrown.getMessage(), thrown);
        }
        throw new AssertionError("expected " + what + " to be rejected with IllegalArgumentException");
    }

    private static void run(String name, ContractMock.Mode mode, List<String> failed, Scenario scenario) {
        TokenAuthority authority = new TokenAuthority();
        ContractMock mock = null;
        try {
            mock = ContractMock.start(CONTRACT, authority, mode);
            HttpClient httpClient = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(10))
                    .version(HttpClient.Version.HTTP_1_1)
                    .build();
            SddcLcmClient client = SddcLcmClient.create(mock.serviceRootUrl(), authority.newAccessTokenSource(),
                    httpClient);
            scenario.run(mock, authority, client);
            System.out.println("pass  " + name);
        } catch (Throwable failure) {
            failed.add(name);
            System.out.println("FAIL  " + name);
            System.out.println(indent(String.valueOf(failure.getMessage())));
            if (!(failure instanceof AssertionError)) {
                System.out.println(indent(failure.getClass().getName()));
            }
            if (mock != null) {
                try {
                    mock.writeRequestLog(REQUEST_LOG);
                    System.out.println(indent("request log written to " + REQUEST_LOG));
                } catch (Exception ignored) {
                    // the console report above is enough
                }
            }
        } finally {
            if (mock != null) {
                mock.close();
            }
        }
    }

    private static String indent(String text) {
        StringBuilder indented = new StringBuilder();
        for (String line : text.split("\\R")) {
            indented.append("      ").append(line).append(System.lineSeparator());
        }
        return indented.toString().stripTrailing();
    }

    private TestMain() {
    }
}
