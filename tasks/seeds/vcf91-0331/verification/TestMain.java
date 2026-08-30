import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deterministic acceptance harness for the VCF Automation machine-provisioning client.
 *
 * <p>Every scenario runs against a loopback fixture built from {@code docs/contract.json}. No live
 * VMware endpoint is contacted anywhere in this run.
 */
public final class TestMain {

    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path SOURCES = Path.of("docs", "official_sources.json");

    private static final String REFRESH_TOKEN = "rt-9f2c-4d81-provisioning";
    private static final String BEARER = "bt-eyJhbGciOiJSUzI1NiJ9.vcfa";
    private static final Duration INTERVAL = Duration.ofMillis(20);
    private static final Duration TIMEOUT = Duration.ofSeconds(5);

    private static final String LOGIN_BODY = "{\"refreshToken\":\"" + REFRESH_TOKEN + "\"}";

    public static void main(String[] args) throws Exception {
        require(
                Files.isRegularFile(CONTRACT) && Files.isRegularFile(SOURCES),
                "run this harness from the project root; docs/contract.json and"
                        + " docs/official_sources.json must be readable");

        WireVerifier.provenance(CONTRACT, SOURCES);

        provisionsAndReusesTheSession();
        readsTheMachineWithSelect();
        doesNotTrustTheAcceptanceReply();
        reportsAFailedRequest();
        givesUpOnAnUnfinishedRequest();
        reportsARejectedCreate();
        reportsARejectedLogin();
        reportsARejectedTracker();
        rejectsATerminalTrackerWithoutAMachine();
        rejectsATerminalTrackerWithoutProgress();
        validatesTheConfigurationBeforeAnyRequest();

        System.out.println(
                "PASS: reference-derived VCF Automation contract, asynchronous request tracking"
                        + " and request wire shape verified");
    }

    // --------------------------------------------------------------- scenarios

    /** The happy path, twice on one client: the session and the discovered apiVersion are reused. */
    private static void provisionsAndReusesTheSession() throws Exception {
        String scenario = "provisions and reuses the session";
        String version = "2025-11-15";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-1a2b",
                            "Provisioning app-node-01",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(
                                    MockVcfAutomation.Tracker.inProgress(25),
                                    MockVcfAutomation.Tracker.inProgress(70),
                                    MockVcfAutomation.Tracker.finished("/iaas/api/machines/mach-7c3d")),
                            new MockVcfAutomation.MachineDoc(
                                    "mach-7c3d", "app-node-01", "ON", "10.24.8.31", "vm-5511", "prj-8a41")));
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-3c4d",
                            "Provisioning app-node-02",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(
                                    MockVcfAutomation.Tracker.inProgress(40),
                                    MockVcfAutomation.Tracker.finished("/iaas/api/machines/mach-9e1f")),
                            new MockVcfAutomation.MachineDoc(
                                    "mach-9e1f", "app-node-02", "ON", "10.24.8.32", "vm-5512", "prj-8a41")));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            Map<String, String> properties = new LinkedHashMap<>();
            properties.put("osType", "LINUX");
            properties.put("folderName", "rainpole/app");
            VcfAutomationMachineClient.ProvisionResult first =
                    client.provision(
                            new VcfAutomationMachineClient.MachineRequest(
                                    "app-node-01",
                                    "prj-8a41",
                                    "ubuntu-22.04",
                                    "medium",
                                    "Reference-derived provisioning smoke",
                                    List.of(
                                            new VcfAutomationMachineClient.Tag("ownedBy", "Rainpole"),
                                            new VcfAutomationMachineClient.Tag("tier", "web")),
                                    properties,
                                    null));

            WireVerifier.equal("[" + scenario + "] request id", "req-1a2b", first.requestId());
            WireVerifier.equal(
                    "[" + scenario + "] request self link",
                    "/iaas/api/request-tracker/req-1a2b",
                    first.requestSelfLink());
            WireVerifier.equal("[" + scenario + "] terminal status", "FINISHED", first.terminalStatus());
            WireVerifier.equal("[" + scenario + "] progress", 100, first.progress());
            WireVerifier.equal("[" + scenario + "] discovered apiVersion", version, first.apiVersion());
            WireVerifier.equal("[" + scenario + "] tracker reads", 3, first.trackerReads());
            WireVerifier.equal(
                    "[" + scenario + "] machine",
                    new VcfAutomationMachineClient.MachineRef(
                            "mach-7c3d", "app-node-01", "ON", "10.24.8.31", "vm-5511", "prj-8a41"),
                    first.machine());
            WireVerifier.equal(
                    "[" + scenario + "] apiVersion() after discovery", version, client.apiVersion());

            VcfAutomationMachineClient.ProvisionResult second =
                    client.provision(
                            new VcfAutomationMachineClient.MachineRequest(
                                    "app-node-02",
                                    "prj-8a41",
                                    "ubuntu-22.04",
                                    "small",
                                    null,
                                    List.of(),
                                    new LinkedHashMap<>(),
                                    "dep-33f1"));

            WireVerifier.equal("[" + scenario + "] second request id", "req-3c4d", second.requestId());
            WireVerifier.equal("[" + scenario + "] second tracker reads", 2, second.trackerReads());
            WireVerifier.equal(
                    "[" + scenario + "] second machine id", "mach-9e1f", second.machine().id());

            String query = "apiVersion=" + version;
            List<WireVerifier.Expect> expected = new ArrayList<>();
            expected.add(login());
            expected.add(about());
            expected.add(
                    WireVerifier.Expect.entity(
                            "createMachine",
                            "/iaas/api/machines",
                            query,
                            "{\"name\":\"app-node-01\",\"projectId\":\"prj-8a41\","
                                    + "\"image\":\"ubuntu-22.04\",\"flavor\":\"medium\","
                                    + "\"description\":\"Reference-derived provisioning smoke\","
                                    + "\"tags\":[{\"key\":\"ownedBy\",\"value\":\"Rainpole\"},"
                                    + "{\"key\":\"tier\",\"value\":\"web\"}],"
                                    + "\"customProperties\":{\"osType\":\"LINUX\","
                                    + "\"folderName\":\"rainpole/app\"}}"));
            expected.add(tracker("req-1a2b", query));
            expected.add(tracker("req-1a2b", query));
            expected.add(tracker("req-1a2b", query));
            expected.add(WireVerifier.Expect.read("getMachine", "/iaas/api/machines/mach-7c3d", query));
            expected.add(
                    WireVerifier.Expect.entity(
                            "createMachine",
                            "/iaas/api/machines",
                            query,
                            "{\"name\":\"app-node-02\",\"projectId\":\"prj-8a41\","
                                    + "\"image\":\"ubuntu-22.04\",\"flavor\":\"small\","
                                    + "\"deploymentId\":\"dep-33f1\"}"));
            expected.add(tracker("req-3c4d", query));
            expected.add(tracker("req-3c4d", query));
            expected.add(WireVerifier.Expect.read("getMachine", "/iaas/api/machines/mach-9e1f", query));

            WireVerifier.verify(scenario, mock, BEARER, expected);
            WireVerifier.polls(scenario, mock, "req-1a2b", 3);
            WireVerifier.polls(scenario, mock, "req-3c4d", 2);
        }
    }

    /** $select is sent only when configured, after apiVersion, and the version is discovered. */
    private static void readsTheMachineWithSelect() throws Exception {
        String scenario = "reads the machine with $select";
        String version = "2024-06-30";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-5e6f",
                            "Provisioning db-node-01",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(MockVcfAutomation.Tracker.finished("/iaas/api/machines/mach-2b8a")),
                            new MockVcfAutomation.MachineDoc(
                                    "mach-2b8a", "db-node-01", "ON", "10.24.8.44", "vm-5590", "prj-8a41")));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl() + "/",
                                    REFRESH_TOKEN,
                                    "powerState",
                                    Duration.ofSeconds(Long.MAX_VALUE),
                                    Duration.ofSeconds(Long.MAX_VALUE),
                                    null));

            VcfAutomationMachineClient.ProvisionResult result =
                    client.provision(
                            new VcfAutomationMachineClient.MachineRequest(
                                    "db-node-01", "prj-8a41", "photon-5", "large"));

            WireVerifier.equal("[" + scenario + "] discovered apiVersion", version, result.apiVersion());
            WireVerifier.equal("[" + scenario + "] tracker reads", 1, result.trackerReads());
            WireVerifier.equal("[" + scenario + "] machine id", "mach-2b8a", result.machine().id());

            String query = "apiVersion=" + version;
            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    query,
                                    "{\"name\":\"db-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"large\"}"),
                            tracker("req-5e6f", query),
                            WireVerifier.Expect.read(
                                    "getMachine",
                                    "/iaas/api/machines/mach-2b8a",
                                    query + "&$select=powerState")));
            WireVerifier.polls(scenario, mock, "req-5e6f", 1);
        }
    }

    /** The 202 acceptance reply is not an observation of the terminal state. */
    private static void doesNotTrustTheAcceptanceReply() throws Exception {
        String scenario = "does not trust the acceptance reply";
        String version = "2025-11-15";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-7a8b",
                            "Provisioning cache-node-01",
                            new MockVcfAutomation.Tracker(
                                    "FINISHED", 100, "accepted", List.of("/iaas/api/machines/mach-stale")),
                            List.of(
                                    MockVcfAutomation.Tracker.inProgress(60),
                                    MockVcfAutomation.Tracker.finished("/iaas/api/machines/mach-fresh")),
                            new MockVcfAutomation.MachineDoc(
                                    "mach-fresh", "cache-node-01", "ON", "10.24.8.57", "vm-5601", "prj-8a41")));
            // The machine the acceptance reply pointed at is readable too, so a client that trusts
            // that reply is caught by the recorded request log rather than by an accidental 404.
            mock.stageMachine(
                    new MockVcfAutomation.MachineDoc(
                            "mach-stale", "cache-node-01", "OFF", "10.24.8.56", "vm-5600", "prj-8a41"));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.ProvisionResult result =
                    client.provision(
                            new VcfAutomationMachineClient.MachineRequest(
                                    "cache-node-01", "prj-8a41", "photon-5", "small"));

            WireVerifier.equal(
                    "[" + scenario + "] machine read from the polled tracker",
                    "mach-fresh",
                    result.machine().id());
            WireVerifier.equal("[" + scenario + "] tracker reads", 2, result.trackerReads());

            String query = "apiVersion=" + version;
            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    query,
                                    "{\"name\":\"cache-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"small\"}"),
                            tracker("req-7a8b", query),
                            tracker("req-7a8b", query),
                            WireVerifier.Expect.read(
                                    "getMachine", "/iaas/api/machines/mach-fresh", query)));
            WireVerifier.polls(scenario, mock, "req-7a8b", 2);
        }
    }

    /** A FAILED tracker ends the run with the service's own message and no machine read. */
    private static void reportsAFailedRequest() throws Exception {
        String scenario = "reports a failed request";
        String version = "2025-11-15";
        String message = "Cannot allocate storage: datastore 'sddc-ds-02' is full";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-9c0d",
                            "Provisioning edge-node-01",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(
                                    MockVcfAutomation.Tracker.inProgress(30),
                                    MockVcfAutomation.Tracker.failed(message)),
                            null));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.RequestFailedException failure =
                    expect(
                            VcfAutomationMachineClient.RequestFailedException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "edge-node-01", "prj-8a41", "photon-5", "small")));

            WireVerifier.equal("[" + scenario + "] request id", "req-9c0d", failure.requestId());
            WireVerifier.equal("[" + scenario + "] operationId", "getRequestTracker", failure.operationId());
            WireVerifier.equal("[" + scenario + "] terminal status", "FAILED", failure.terminalStatus());
            WireVerifier.equal("[" + scenario + "] reported message", message, failure.apiMessage());
            WireVerifier.equal("[" + scenario + "] progress", 100, failure.progress());
            WireVerifier.withoutSecrets(scenario, failure.getMessage(), REFRESH_TOKEN, BEARER);

            String query = "apiVersion=" + version;
            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    query,
                                    "{\"name\":\"edge-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"small\"}"),
                            tracker("req-9c0d", query),
                            tracker("req-9c0d", query)));
            WireVerifier.polls(scenario, mock, "req-9c0d", 2);
        }
    }

    /** A request that never leaves INPROGRESS ends at the deadline, not at the first poll. */
    private static void givesUpOnAnUnfinishedRequest() throws Exception {
        String scenario = "gives up on an unfinished request";
        String version = "2025-11-15";
        Duration interval = Duration.ofMillis(80);
        Duration timeoutLimit = Duration.ofSeconds(1);
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-2e3f",
                            "Provisioning slow-node-01",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(MockVcfAutomation.Tracker.inProgress(10)),
                            null));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(),
                                    REFRESH_TOKEN,
                                    null,
                                    interval,
                                    timeoutLimit,
                                    null));

            VcfAutomationMachineClient.RequestPollTimeoutException timeout =
                    expect(
                            VcfAutomationMachineClient.RequestPollTimeoutException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "slow-node-01", "prj-8a41", "photon-5", "small")));

            WireVerifier.equal("[" + scenario + "] request id", "req-2e3f", timeout.requestId());
            WireVerifier.equal("[" + scenario + "] last status", "INPROGRESS", timeout.lastStatus());
            WireVerifier.equal("[" + scenario + "] last progress", 10, timeout.lastProgress());
            WireVerifier.pollsAtLeast(scenario, mock, "req-2e3f", 3);
            WireVerifier.pollsRespectDelay(
                    scenario, mock, "req-2e3f", Duration.ofMillis(40));
            long acceptedAt = mock.acceptedAtNanos("req-2e3f");
            WireVerifier.check(
                    "[" + scenario + "] timeout was reported before the configured deadline",
                    acceptedAt > 0
                            && System.nanoTime() - acceptedAt
                                    >= timeoutLimit.minusMillis(100).toNanos());

            String query = "apiVersion=" + version;
            List<WireVerifier.Expect> expected = new ArrayList<>();
            expected.add(login());
            expected.add(about());
            expected.add(
                    WireVerifier.Expect.entity(
                            "createMachine",
                            "/iaas/api/machines",
                            query,
                            "{\"name\":\"slow-node-01\",\"projectId\":\"prj-8a41\","
                                    + "\"image\":\"photon-5\",\"flavor\":\"small\"}"));
            for (int i = 0; i < mock.trackerPolls("req-2e3f"); i++) {
                expected.add(tracker("req-2e3f", query));
            }
            WireVerifier.verify(scenario, mock, BEARER, expected);
        }
    }

    /** A rejected createMachine surfaces the ServiceErrorResponse and tracks nothing. */
    private static void reportsARejectedCreate() throws Exception {
        String scenario = "reports a rejected create";
        String version = "2025-11-15";
        String message = "Invalid machine specification: unknown flavor 'colossal'";
        try (MockVcfAutomation mock = mock(version)) {
            mock.failCreate(new MockVcfAutomation.ApiError(400, message, 40010, "err-5f21"));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.VcfAutomationApiException failure =
                    expect(
                            VcfAutomationMachineClient.VcfAutomationApiException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "bad-node-01", "prj-8a41", "photon-5", "colossal")));

            WireVerifier.equal("[" + scenario + "] operationId", "createMachine", failure.operationId());
            WireVerifier.equal("[" + scenario + "] status code", 400, failure.statusCode());
            WireVerifier.equal("[" + scenario + "] reported message", message, failure.apiMessage());
            WireVerifier.equal("[" + scenario + "] error code", 40010, failure.errorCode());
            WireVerifier.equal("[" + scenario + "] server error id", "err-5f21", failure.serverErrorId());
            WireVerifier.check(
                    "[" + scenario + "] a failure before acceptance carries no request id",
                    failure.requestId() == null);
            WireVerifier.withoutSecrets(scenario, failure.getMessage(), REFRESH_TOKEN, BEARER);

            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    "apiVersion=" + version,
                                    "{\"name\":\"bad-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"colossal\"}")));
        }
    }

    /** A rejected token exchange stops the run at the first operation. */
    private static void reportsARejectedLogin() throws Exception {
        String scenario = "reports a rejected login";
        try (MockVcfAutomation mock = mock("2025-11-15")) {
            mock.failLogin(
                    new MockVcfAutomation.ApiError(403, "Refresh token is expired", 403, "err-77c1"));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.VcfAutomationApiException failure =
                    expect(
                            VcfAutomationMachineClient.VcfAutomationApiException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "any-node", "prj-8a41", "photon-5", "small")));

            WireVerifier.equal(
                    "[" + scenario + "] operationId", "retrieveAuthToken", failure.operationId());
            WireVerifier.equal("[" + scenario + "] status code", 403, failure.statusCode());
            WireVerifier.equal(
                    "[" + scenario + "] reported message", "Refresh token is expired", failure.apiMessage());
            WireVerifier.withoutSecrets(scenario, failure.getMessage(), REFRESH_TOKEN, BEARER);
            WireVerifier.equal(
                    "[" + scenario + "] apiVersion() before discovery", null, client.apiVersion());

            WireVerifier.verify(scenario, mock, BEARER, List.of(login()));
        }
    }

    /** A rejected tracker read is an API failure carrying the accepted request id. */
    private static void reportsARejectedTracker() throws Exception {
        String scenario = "reports a rejected tracker";
        String version = "2025-11-15";
        String message = "Request tracker is temporarily unavailable";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                            new MockVcfAutomation.Provision(
                                    "req-6i7j",
                                    "Provisioning retry-node-01",
                                    MockVcfAutomation.Tracker.inProgress(0),
                                    List.of(MockVcfAutomation.Tracker.inProgress(25)),
                                    null))
                    .failTracker(
                            new MockVcfAutomation.ApiError(503, message, 50321, "err-6i7j"));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.VcfAutomationApiException failure =
                    expect(
                            VcfAutomationMachineClient.VcfAutomationApiException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "retry-node-01",
                                                    "prj-8a41",
                                                    "photon-5",
                                                    "small")));

            WireVerifier.equal("[" + scenario + "] operationId", "getRequestTracker", failure.operationId());
            WireVerifier.equal("[" + scenario + "] request id", "req-6i7j", failure.requestId());
            WireVerifier.equal("[" + scenario + "] status code", 503, failure.statusCode());
            WireVerifier.equal("[" + scenario + "] reported message", message, failure.apiMessage());
            WireVerifier.equal("[" + scenario + "] error code", 50321, failure.errorCode());
            WireVerifier.equal("[" + scenario + "] server error id", "err-6i7j", failure.serverErrorId());
            WireVerifier.withoutSecrets(scenario, failure.getMessage(), REFRESH_TOKEN, BEARER);

            String query = "apiVersion=" + version;
            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    query,
                                    "{\"name\":\"retry-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"small\"}"),
                            tracker("req-6i7j", query)));
        }
    }

    /** A FINISHED tracker that points at no machine is a protocol failure, not a silent success. */
    private static void rejectsATerminalTrackerWithoutAMachine() throws Exception {
        String scenario = "rejects a terminal tracker without a machine";
        String version = "2025-11-15";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-4g5h",
                            "Provisioning ghost-node-01",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(MockVcfAutomation.Tracker.finished()),
                            null));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.VcfAutomationProtocolException failure =
                    expect(
                            VcfAutomationMachineClient.VcfAutomationProtocolException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "ghost-node-01", "prj-8a41", "photon-5", "small")));

            WireVerifier.equal("[" + scenario + "] request id", "req-4g5h", failure.requestId());

            String query = "apiVersion=" + version;
            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    query,
                                    "{\"name\":\"ghost-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"small\"}"),
                            tracker("req-4g5h", query)));
            WireVerifier.polls(scenario, mock, "req-4g5h", 1);
        }
    }

    /** A required RequestTracker member with the wrong JSON type is a protocol failure. */
    private static void rejectsATerminalTrackerWithoutProgress() throws Exception {
        String scenario = "rejects a terminal tracker without progress";
        String version = "2025-11-15";
        try (MockVcfAutomation mock = mock(version)) {
            mock.stage(
                    new MockVcfAutomation.Provision(
                            "req-8k9m",
                            "Provisioning malformed-node-01",
                            MockVcfAutomation.Tracker.inProgress(0),
                            List.of(
                                    new MockVcfAutomation.Tracker(
                                            "FINISHED",
                                            null,
                                            "Provisioning completed",
                                            List.of("/iaas/api/machines/mach-8k9m"))),
                            new MockVcfAutomation.MachineDoc(
                                    "mach-8k9m",
                                    "malformed-node-01",
                                    "ON",
                                    "10.24.8.88",
                                    "vm-5688",
                                    "prj-8a41")));

            VcfAutomationMachineClient client =
                    VcfAutomationMachineClient.create(
                            new VcfAutomationMachineClient.Config(
                                    mock.baseUrl(), REFRESH_TOKEN, null, INTERVAL, TIMEOUT, null));

            VcfAutomationMachineClient.VcfAutomationProtocolException failure =
                    expect(
                            VcfAutomationMachineClient.VcfAutomationProtocolException.class,
                            scenario,
                            () ->
                                    client.provision(
                                            new VcfAutomationMachineClient.MachineRequest(
                                                    "malformed-node-01",
                                                    "prj-8a41",
                                                    "photon-5",
                                                    "small")));

            WireVerifier.equal("[" + scenario + "] operationId", "getRequestTracker", failure.operationId());
            WireVerifier.equal("[" + scenario + "] request id", "req-8k9m", failure.requestId());

            String query = "apiVersion=" + version;
            WireVerifier.verify(
                    scenario,
                    mock,
                    BEARER,
                    List.of(
                            login(),
                            about(),
                            WireVerifier.Expect.entity(
                                    "createMachine",
                                    "/iaas/api/machines",
                                    query,
                                    "{\"name\":\"malformed-node-01\",\"projectId\":\"prj-8a41\","
                                            + "\"image\":\"photon-5\",\"flavor\":\"small\"}"),
                            tracker("req-8k9m", query)));
        }
    }

    /** An unusable configuration is rejected by create, before anything is sent. */
    private static void validatesTheConfigurationBeforeAnyRequest() throws Exception {
        String scenario = "validates the configuration before any request";
        try (MockVcfAutomation mock = mock("2025-11-15")) {
            String base = mock.baseUrl();
            expect(
                    IllegalArgumentException.class,
                    scenario,
                    () -> VcfAutomationMachineClient.create(null));
            List<VcfAutomationMachineClient.Config> rejected =
                    List.of(
                            new VcfAutomationMachineClient.Config(null, REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("   ", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config(" https://vcfa.rainpole.io", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("ftp://vcfa.rainpole.io", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("https://vcfa.rainpole.io:99999", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("https://vcfa.rainpole.io/iaas", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("https://vcfa.rainpole.io?v=1", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("https://vcfa.rainpole.io#top", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("https://admin@vcfa.rainpole.io", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config("https://", REFRESH_TOKEN),
                            new VcfAutomationMachineClient.Config(base, null),
                            new VcfAutomationMachineClient.Config(base, "  "),
                            new VcfAutomationMachineClient.Config(base, "rt-with\nnewline"),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "power\tState", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "power%State", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "power&State", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "power=State", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "power State", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "power|State", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, "pöwerState", INTERVAL, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, null, Duration.ZERO, TIMEOUT, null),
                            new VcfAutomationMachineClient.Config(
                                    base, REFRESH_TOKEN, null, INTERVAL, Duration.ofSeconds(-1), null));

            for (VcfAutomationMachineClient.Config config : rejected) {
                try {
                    VcfAutomationMachineClient.create(config);
                    throw new WireVerifier.WireAssertionError(
                            "["
                                    + scenario
                                    + "] create accepted an unusable configuration: baseUrl <"
                                    + config.baseUrl()
                                    + ">, machineSelect <"
                                    + config.machineSelect()
                                    + ">, pollInterval <"
                                    + config.pollInterval()
                                    + ">, pollTimeout <"
                                    + config.pollTimeout()
                                    + ">");
                } catch (IllegalArgumentException expected) {
                    WireVerifier.withoutSecrets(scenario, expected.getMessage(), REFRESH_TOKEN);
                }
            }

            VcfAutomationMachineClient.create(
                    new VcfAutomationMachineClient.Config(base + "/", REFRESH_TOKEN));
            VcfAutomationMachineClient.create(
                    new VcfAutomationMachineClient.Config(base, REFRESH_TOKEN, "  ", null, null, null));
            VcfAutomationMachineClient.create(
                    new VcfAutomationMachineClient.Config(
                            base, REFRESH_TOKEN, "name,address?powerState", null, null, null));

            WireVerifier.silent(scenario, mock);
        }
    }

    // ----------------------------------------------------------------- helpers

    private static MockVcfAutomation mock(String latestApiVersion) throws Exception {
        return new MockVcfAutomation(
                CONTRACT,
                REFRESH_TOKEN,
                BEARER,
                latestApiVersion,
                List.of("2019-01-15", "2021-07-15", latestApiVersion));
    }

    private static WireVerifier.Expect login() {
        return new WireVerifier.Expect(
                "retrieveAuthToken", "POST", "/iaas/api/login", null, LOGIN_BODY, false);
    }

    private static WireVerifier.Expect about() {
        return WireVerifier.Expect.read("getAboutPage", "/iaas/api/about", null);
    }

    private static WireVerifier.Expect tracker(String requestId, String query) {
        return WireVerifier.Expect.read(
                "getRequestTracker", "/iaas/api/request-tracker/" + requestId, query);
    }

    private interface Run {
        void call() throws Exception;
    }

    private static <T extends Throwable> T expect(Class<T> type, String scenario, Run run) {
        try {
            run.call();
        } catch (Throwable thrown) {
            if (type.isInstance(thrown)) {
                return type.cast(thrown);
            }
            throw new WireVerifier.WireAssertionError(
                    "["
                            + scenario
                            + "] expected "
                            + type.getSimpleName()
                            + " but "
                            + thrown.getClass().getName()
                            + " was thrown: "
                            + thrown.getMessage());
        }
        throw new WireVerifier.WireAssertionError(
                "[" + scenario + "] expected " + type.getSimpleName() + " but the call returned");
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new WireVerifier.WireAssertionError(message);
        }
    }

    private TestMain() {}
}
