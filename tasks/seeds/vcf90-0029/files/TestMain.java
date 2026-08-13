import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Protected acceptance harness for the SDDC Manager 9.0 capacity-onboarding client.
 *
 * <p>Each case starts the contract-pinned loopback mock on an ephemeral 127.0.0.1 port, drives
 * {@link VcfCapacityOnboarding} against it, then reads the mock's request log back and asserts the
 * exact wire shape of every call: method, raw target, headers, content length, and the canonical
 * form of every body, including that a member the caller left unset never appears. No live VMware
 * endpoint is contacted.
 */
public final class TestMain {

    private static final String SPEC_TAG = "9.0.0.0";
    private static final String SPEC_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f";
    private static final String SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json";
    private static final List<String> CONTRACT_OPERATIONS = List.of(
            "createToken",
            "createNetworkPool",
            "getNetworksOfNetworkPool",
            "addIpPoolToNetworkOfNetworkPool",
            "commissionHosts");

    private static final String SSO_USER = "administrator@vsphere.local";
    private static final String SSO_PASSWORD = MockSddcManager.ECHOED_SSO_PASSWORD;
    private static final String ESX_USER = "root";
    private static final String ESX_PASSWORD = MockSddcManager.ECHOED_ESX_PASSWORD;
    private static final String API_KEY = MockSddcManager.SENSITIVE_API_KEY;
    private static final String POOL_NAME = "np-mgmt-expansion-a";

    private static final String BEARER = "Bearer " + MockSddcManager.ACCESS_TOKEN;
    private static final String POOL_ID = MockSddcManager.POOL_ID;
    private static final String POOL_ID_PATH = "pool%2FBlue%20%C3%BC%3Fx%2By";

    private static final List<String> failures = new ArrayList<>();
    private static int checks;

    public static void main(String[] args) throws Exception {
        runCase("contract and sources are pinned to the 9.0.0.0 specification", TestMain::contractIsPinned);
        runCase("the commissioning step is refused after the pool work landed", TestMain::commissionRefused);
        runCase("an IP pool refusal stops the change before any host is commissioned", TestMain::ipPoolRefused);
        runCase("a read-back refusal preserves the pool that was already created", TestMain::networkReadRefused);
        runCase("a change that runs to the end reports every step as applied", TestMain::allAccepted);
        runCase("a change with no IP pool additions still reports the pool it created", TestMain::noIpPoolAdditions);
        runCase("secrets echoed by an Error response are redacted from the report", TestMain::secretsAreRedacted);
        runCase("a malformed refusal is a protocol error", TestMain::malformedRefusalIsProtocolError);
        runCase("invalid input is refused before anything reaches the wire", TestMain::validationBeforeTheWire);

        System.out.println();
        if (failures.isEmpty()) {
            System.out.println("PASS - " + checks + " assertions across 9 cases");
            return;
        }
        System.out.println("FAIL - " + failures.size() + " of " + checks + " assertions failed");
        for (String failure : failures) {
            System.out.println("  * " + failure);
        }
        System.exit(1);
    }

    // ----------------------------------------------------------------- the cases

    private static void contractIsPinned() throws IOException {
        Map<String, Object> contract = readJsonObject(Path.of("docs", "contract.json"));
        Map<String, Object> source = MockSddcManager.Json.object(contract.get("source"));
        is("contract.source.sourceKind", source.get("sourceKind"), "openapi-specification");
        is("contract.source.tag", source.get("tag"), SPEC_TAG);
        is("contract.source.commitSha", source.get("commitSha"), SPEC_COMMIT);
        is("contract.source.specPath", source.get("specPath"), SPEC_PATH);
        is("contract.source.apiVersion", source.get("apiVersion"), SPEC_TAG);

        List<String> contractOperations = new ArrayList<>();
        for (Object entry : MockSddcManager.Json.list(contract.get("operations"))) {
            contractOperations.add(MockSddcManager.Json.string(
                    MockSddcManager.Json.object(entry).get("operationId")));
        }
        is("contract names exactly the operations this client calls", contractOperations, CONTRACT_OPERATIONS);

        Map<String, Object> sources = readJsonObject(Path.of("docs", "official_sources.json"));
        Map<String, Object> specification = MockSddcManager.Json.object(sources.get("specification"));
        is("official_sources.specification.spec_path", specification.get("spec_path"), SPEC_PATH);
        is("official_sources.specification.repository_commit_sha", specification.get("repository_commit_sha"),
                SPEC_COMMIT);
        is("official_sources.specification.repository_tag", specification.get("repository_tag"), SPEC_TAG);

        List<String> recorded = new ArrayList<>();
        for (Object entry : MockSddcManager.Json.list(sources.get("operations"))) {
            Map<String, Object> record = MockSddcManager.Json.object(entry);
            recorded.add(MockSddcManager.Json.string(record.get("operationId")));
            is("every source record carries the spec path", record.get("spec_path"), SPEC_PATH);
            is("every source record carries the commit sha", record.get("repository_commit_sha"), SPEC_COMMIT);
        }
        is("official_sources records every operationId", recorded, CONTRACT_OPERATIONS);

        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.ALL_ACCEPTED)) {
            is("the mock serves only the contract operations", mock.servedOperationIds(), CONTRACT_OPERATIONS);

            HttpResponse<String> outside = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(URI.create(mock.baseUrl() + "/v1/tasks/anything")).GET().build(),
                    HttpResponse.BodyHandlers.ofString());
            is("a route the contract does not name is refused", outside.statusCode(), 404);
            isTrue("the refusal says why", outside.body().contains("ROUTE_NOT_IN_CONTRACT"));
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            record("the out-of-contract probe was interrupted");
        }
    }

    private static void commissionRefused() throws IOException {
        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.COMMISSION_REFUSED)) {
            VcfCapacityOnboarding.OnboardingReport report =
                    new VcfCapacityOnboarding(mock.baseUrl(), credentials()).onboard(standardRequest());

            is("the change did not complete", report.completed(), false);
            is("the change is reported as partially applied", report.partiallyApplied(), true);
            is("the created pool id survives the refusal", report.networkPoolId(), POOL_ID);
            is("the created pool name survives the refusal", report.networkPoolName(), POOL_NAME);
            is("both resolved network ids survive the refusal", report.networkIdsByType(),
                    Map.of("VMOTION", mock.networkId("VMOTION"), "VSAN", mock.networkId("VSAN")));
            is("both applied IP pool ranges are reported", report.appliedIpPoolRanges(), List.of(
                    "VMOTION 172.16.31.60-172.16.31.79",
                    "VSAN 172.16.32.10-172.16.32.39"));
            is("no commission task is claimed", report.commissionTaskId(), null);
            is("no commission task status is claimed", report.commissionTaskStatus(), null);

            assertSteps(report, List.of(
                    step("createToken", "SUCCEEDED", false),
                    step("createNetworkPool", "SUCCEEDED", true),
                    step("getNetworksOfNetworkPool", "SUCCEEDED", false),
                    step("addIpPoolToNetworkOfNetworkPool", "SUCCEEDED", true),
                    step("addIpPoolToNetworkOfNetworkPool", "SUCCEEDED", true),
                    step("commissionHosts", "FAILED", false)));

            VcfCapacityOnboarding.StepFailure failure = report.failure();
            isTrue("the report carries the failure", failure != null);
            if (failure != null) {
                is("the failure names the refused step", failure.stepIndex(), 5);
                is("the failure names the refused operation", failure.operationId(), "commissionHosts");
                is("the failure carries the HTTP status", failure.httpStatus(), 400);
                is("the failure carries the error code", failure.errorCode(),
                        MockSddcManager.COMMISSION_ERROR_CODE);
                is("the failure carries the reference token", failure.referenceToken(),
                        MockSddcManager.COMMISSION_REFERENCE_TOKEN);
                isTrue("the failure carries the server message",
                        failure.message() != null && failure.message().contains("esx-a03.vcf.lab"));
            }
            assertNoSecretsLeaked(report);

            List<MockSddcManager.Recorded> log = mock.requestLog();
            is("six requests reached the estate", log.size(), 6);
            assertStandardWireShape(mock, log);
            assertHostsRequest(log.get(5));
        }
    }

    private static void ipPoolRefused() throws IOException {
        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.IP_POOL_REFUSED)) {
            VcfCapacityOnboarding.OnboardingReport report =
                    new VcfCapacityOnboarding(mock.baseUrl(), credentials()).onboard(standardRequest());

            is("the change did not complete", report.completed(), false);
            is("the change is reported as partially applied", report.partiallyApplied(), true);
            is("the created pool id is reported", report.networkPoolId(), POOL_ID);
            is("only the range that landed is reported", report.appliedIpPoolRanges(),
                    List.of("VMOTION 172.16.31.60-172.16.31.79"));
            is("no commission task is claimed", report.commissionTaskId(), null);

            assertSteps(report, List.of(
                    step("createToken", "SUCCEEDED", false),
                    step("createNetworkPool", "SUCCEEDED", true),
                    step("getNetworksOfNetworkPool", "SUCCEEDED", false),
                    step("addIpPoolToNetworkOfNetworkPool", "SUCCEEDED", true),
                    step("addIpPoolToNetworkOfNetworkPool", "FAILED", false),
                    step("commissionHosts", "NOT_ATTEMPTED", false)));

            VcfCapacityOnboarding.StepFailure failure = report.failure();
            isTrue("the report carries the failure", failure != null);
            if (failure != null) {
                is("the failure names the refused step", failure.stepIndex(), 4);
                is("the failure names the refused operation", failure.operationId(),
                        "addIpPoolToNetworkOfNetworkPool");
                is("the failure carries the HTTP status", failure.httpStatus(), 400);
                is("the failure carries the error code", failure.errorCode(), MockSddcManager.IP_POOL_ERROR_CODE);
                is("the failure carries the reference token", failure.referenceToken(),
                        MockSddcManager.IP_POOL_REFERENCE_TOKEN);
            }
            assertNoSecretsLeaked(report);

            List<MockSddcManager.Recorded> log = mock.requestLog();
            is("the change stopped at the refused step", log.size(), 5);
            assertStandardWireShape(mock, log);
            for (MockSddcManager.Recorded recorded : log) {
                isTrue("no host was commissioned after the refusal",
                        !"commissionHosts".equals(recorded.operationId()));
            }
        }
    }

    private static void networkReadRefused() throws IOException {
        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.NETWORK_READ_REFUSED)) {
            VcfCapacityOnboarding.OnboardingReport report =
                    new VcfCapacityOnboarding(mock.baseUrl(), credentials()).onboard(standardRequest());

            is("the read-back refusal does not complete the change", report.completed(), false);
            is("the accepted pool creation makes the stopped change partial", report.partiallyApplied(), true);
            is("the pool id survives the read-back refusal", report.networkPoolId(), POOL_ID);
            is("the pool name survives the read-back refusal", report.networkPoolName(), POOL_NAME);
            is("no network ids are claimed when the read-back was refused", report.networkIdsByType(), Map.of());
            is("no range is claimed when no addition ran", report.appliedIpPoolRanges(), List.of());
            assertSteps(report, List.of(
                    step("createToken", "SUCCEEDED", false),
                    step("createNetworkPool", "SUCCEEDED", true),
                    step("getNetworksOfNetworkPool", "FAILED", false),
                    step("addIpPoolToNetworkOfNetworkPool", "NOT_ATTEMPTED", false),
                    step("addIpPoolToNetworkOfNetworkPool", "NOT_ATTEMPTED", false),
                    step("commissionHosts", "NOT_ATTEMPTED", false)));

            VcfCapacityOnboarding.StepFailure failure = report.failure();
            isTrue("the read-back refusal is reported", failure != null);
            if (failure != null) {
                is("the read-back failure keeps its index", failure.stepIndex(), 2);
                is("the read-back failure keeps its operation", failure.operationId(),
                        "getNetworksOfNetworkPool");
                is("the read-back failure keeps its HTTP status", failure.httpStatus(), 404);
                is("the read-back failure keeps its error code", failure.errorCode(),
                        MockSddcManager.NETWORK_READ_ERROR_CODE);
                is("the read-back failure keeps its reference token", failure.referenceToken(),
                        MockSddcManager.NETWORK_READ_REFERENCE_TOKEN);
            }
            assertNoSecretsLeaked(report);
            is("the change stopped after the refused read-back", mock.requestLog().size(), 3);
        }
    }

    private static void allAccepted() throws IOException {
        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.ALL_ACCEPTED)) {
            VcfCapacityOnboarding.CapacityRequest request = new VcfCapacityOnboarding.CapacityRequest(
                    POOL_NAME,
                    standardNetworks(),
                    standardAdditions(),
                    List.of(
                            new VcfCapacityOnboarding.HostCommission(
                                    "esx-b01.vcf.lab", ESX_USER, ESX_PASSWORD, "VMFS_FC",
                                    null, "", null, "AA:\"BB\\CC\nü"),
                            new VcfCapacityOnboarding.HostCommission(
                                    "esx-b02.vcf.lab", ESX_USER, ESX_PASSWORD, "VVOL",
                                    "FC", null, null, null)));

            VcfCapacityOnboarding.OnboardingReport report =
                    new VcfCapacityOnboarding(mock.baseUrl(), credentialsWithOptionalMembers()).onboard(request);

            is("the change completed", report.completed(), true);
            is("a completed change is not partially applied", report.partiallyApplied(), false);
            is("no failure is reported", report.failure(), null);
            is("the accepted task id is reported", report.commissionTaskId(), MockSddcManager.TASK_ID);
            is("the accepted task status is reported as the estate sent it",
                    report.commissionTaskStatus(), "IN_PROGRESS");
            assertSteps(report, List.of(
                    step("createToken", "SUCCEEDED", false),
                    step("createNetworkPool", "SUCCEEDED", true),
                    step("getNetworksOfNetworkPool", "SUCCEEDED", false),
                    step("addIpPoolToNetworkOfNetworkPool", "SUCCEEDED", true),
                    step("addIpPoolToNetworkOfNetworkPool", "SUCCEEDED", true),
                    step("commissionHosts", "SUCCEEDED", true)));
            assertNoSecretsLeaked(report);

            List<MockSddcManager.Recorded> log = mock.requestLog();
            is("six requests reached the estate", log.size(), 6);
            assertStandardWireShape(mock, log,
                    "{\"username\":\"" + SSO_USER + "\",\"password\":\"" + SSO_PASSWORD
                            + "\",\"apiKey\":\"api-\\\"key\\\\ü\",\"idToken\":\"\"}");

            MockSddcManager.Recorded hosts = log.get(5);
            is("commissionHosts is a POST /v1/hosts", hosts.method() + " " + hosts.rawTarget(),
                    "POST /v1/hosts");
            is("the vVol protocol sits between storageType and networkPoolId, and no unset member appears",
                    canonical(hosts),
                    "[{\"fqdn\":\"esx-b01.vcf.lab\",\"username\":\"" + ESX_USER + "\",\"password\":\""
                            + ESX_PASSWORD + "\",\"storageType\":\"VMFS_FC\",\"networkPoolId\":\"" + POOL_ID
                            + "\",\"networkPoolName\":\"\",\"sslThumbprint\":\"AA:\\\"BB\\\\CC\\nü\"},"
                            + "{\"fqdn\":\"esx-b02.vcf.lab\",\"username\":\"" + ESX_USER + "\",\"password\":\""
                            + ESX_PASSWORD + "\",\"storageType\":\"VVOL\",\"vvolStorageProtocolType\":\"FC\","
                            + "\"networkPoolId\":\"" + POOL_ID + "\"}]");
        }
    }

    private static void noIpPoolAdditions() throws IOException {
        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.COMMISSION_REFUSED)) {
            VcfCapacityOnboarding.CapacityRequest request = new VcfCapacityOnboarding.CapacityRequest(
                    POOL_NAME, standardNetworks(), List.of(), standardHosts());
            VcfCapacityOnboarding.OnboardingReport report =
                    new VcfCapacityOnboarding(mock.baseUrl(), credentials()).onboard(request);

            is("the pool the change created is still reported", report.networkPoolId(), POOL_ID);
            is("no IP pool range is claimed", report.appliedIpPoolRanges(), List.of());
            is("the change is reported as partially applied", report.partiallyApplied(), true);
            assertSteps(report, List.of(
                    step("createToken", "SUCCEEDED", false),
                    step("createNetworkPool", "SUCCEEDED", true),
                    step("getNetworksOfNetworkPool", "SUCCEEDED", false),
                    step("commissionHosts", "FAILED", false)));
            VcfCapacityOnboarding.StepFailure failure = report.failure();
            if (failure != null) {
                is("the failure names the refused step", failure.stepIndex(), 3);
            }
            is("four requests reached the estate", mock.requestLog().size(), 4);
        }
    }

    private static void secretsAreRedacted() throws IOException {
        try (MockSddcManager mock =
                MockSddcManager.start(MockSddcManager.Scenario.COMMISSION_REFUSED_WITH_SECRETS)) {
            VcfCapacityOnboarding.OnboardingReport report =
                    new VcfCapacityOnboarding(mock.baseUrl(), credentialsWithOptionalMembers())
                            .onboard(standardRequest());

            is("the secret-echoing refusal still returns a report", report.completed(), false);
            is("the secret-echoing refusal still reports partial application", report.partiallyApplied(), true);
            isTrue("the secret-echoing refusal is reported", report.failure() != null);
            if (report.failure() != null) {
                is("redaction preserves the non-secret part of the server message",
                        report.failure().message().contains("esx-a03.vcf.lab"), true);
                isTrue("the sensitive fragments were replaced",
                        report.failure().message().contains("[REDACTED]"));
            }
            assertNoSecretsLeaked(report);
            is("commissioning was the last request", mock.requestLog().size(), 6);
        }
    }

    private static void malformedRefusalIsProtocolError() throws IOException {
        try (MockSddcManager mock =
                MockSddcManager.start(MockSddcManager.Scenario.MALFORMED_COMMISSION_REFUSAL)) {
            VcfCapacityOnboarding client = new VcfCapacityOnboarding(mock.baseUrl(), credentials());
            try {
                client.onboard(standardRequest());
                record("the malformed refusal returned a report instead of throwing ProtocolException");
            } catch (VcfCapacityOnboarding.ProtocolException expected) {
                is("the protocol error names commissionHosts", expected.operationId(), "commissionHosts");
                isTrue("the protocol exception message is readable",
                        expected.getMessage() != null && !expected.getMessage().isBlank());
                isTrue("the protocol exception message does not leak the SSO password",
                        !expected.getMessage().contains(SSO_PASSWORD));
                isTrue("the protocol exception message does not leak an ESXi password",
                        !expected.getMessage().contains(ESX_PASSWORD));
                isTrue("the protocol exception message does not leak the access token",
                        !expected.getMessage().contains(MockSddcManager.ACCESS_TOKEN));
            }
            is("the malformed response arrived on the commissioning exchange", mock.requestLog().size(), 6);
        }
    }

    private static void validationBeforeTheWire() throws IOException {
        try (MockSddcManager mock = MockSddcManager.start(MockSddcManager.Scenario.ALL_ACCEPTED)) {
            VcfCapacityOnboarding client = new VcfCapacityOnboarding(mock.baseUrl(), credentials());

            rejects("an empty host list is refused", client, new VcfCapacityOnboarding.CapacityRequest(
                    POOL_NAME, standardNetworks(), standardAdditions(), List.of()));
            rejects("a host named twice is refused", client, new VcfCapacityOnboarding.CapacityRequest(
                    POOL_NAME, standardNetworks(), standardAdditions(), List.of(
                            new VcfCapacityOnboarding.HostCommission(
                                    "esx-a01.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN"),
                            new VcfCapacityOnboarding.HostCommission(
                                    "ESX-A01.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN"))));
            rejects("a storage type outside the supported values is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, standardNetworks(), standardAdditions(), List.of(
                                    new VcfCapacityOnboarding.HostCommission(
                                            "esx-a01.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN_FASTEST"))));
            rejects("a network type outside the supported values is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, List.of(network("MANAGEMENT", 1631, 9000)), List.of(), standardHosts()));
            rejects("a repeated network type is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, List.of(
                                    network("VSAN", 1631, 9000),
                                    network("VSAN", 1632, 9000)),
                            List.of(), standardHosts()));
            rejects("a vlan id below the supported range is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, List.of(network("VSAN", -1, 9000)), List.of(), standardHosts()));
            rejects("a vlan id above the supported range is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, List.of(network("VSAN", 4095, 9000)), List.of(), standardHosts()));
            rejects("an MTU below the supported range is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, List.of(network("VSAN", 1631, 1279)), List.of(), standardHosts()));
            rejects("an MTU above the supported range is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, List.of(network("VSAN", 1631, 9191)), List.of(), standardHosts()));
            rejects("a vVol protocol on a non-vVol host is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, standardNetworks(), standardAdditions(), List.of(
                                    new VcfCapacityOnboarding.HostCommission(
                                            "esx-a01.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN",
                                            "FC", null, null, null))));
            rejects("a vVol host without a protocol is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, standardNetworks(), standardAdditions(), List.of(
                                    new VcfCapacityOnboarding.HostCommission(
                                            "esx-a01.vcf.lab", ESX_USER, ESX_PASSWORD, "VVOL"))));
            rejects("a vVol host with an unsupported protocol is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, standardNetworks(), standardAdditions(), List.of(
                                    new VcfCapacityOnboarding.HostCommission(
                                            "esx-a01.vcf.lab", ESX_USER, ESX_PASSWORD, "VVOL",
                                            "SAS", null, null, null))));
            rejects("an addition naming a network the change never creates is refused", client,
                    new VcfCapacityOnboarding.CapacityRequest(
                            POOL_NAME, standardNetworks(), List.of(new VcfCapacityOnboarding.IpPoolAddition(
                                    "NFS", new VcfCapacityOnboarding.IpRange("10.0.0.1", "10.0.0.9"))),
                            standardHosts()));
            rejects("a pool with no network is refused", client, new VcfCapacityOnboarding.CapacityRequest(
                    POOL_NAME, List.of(), standardAdditions(), standardHosts()));
            rejects("a blank pool name is refused", client, new VcfCapacityOnboarding.CapacityRequest(
                    "  ", standardNetworks(), standardAdditions(), standardHosts()));

            is("nothing reached the estate", mock.requestLog().size(), 0);
        }
    }

    // ----------------------------------------------------------------- wire assertions

    /** Asserts the shape of the five calls every case makes before commissioning. */
    private static void assertStandardWireShape(MockSddcManager mock, List<MockSddcManager.Recorded> log) {
        assertStandardWireShape(mock, log,
                "{\"username\":\"" + SSO_USER + "\",\"password\":\"" + SSO_PASSWORD + "\"}");
    }

    private static void assertStandardWireShape(MockSddcManager mock, List<MockSddcManager.Recorded> log,
            String expectedTokenBody) {
        MockSddcManager.Recorded token = log.get(0);
        is("createToken is a POST /v1/tokens", token.method() + " " + token.rawTarget(), "POST /v1/tokens");
        is("createToken is matched to its contract operation", token.operationId(), "createToken");
        is("createToken carries no query", token.rawQuery(), null);
        is("createToken sends exactly one Accept header", token.header("accept"), List.of("application/json"));
        is("createToken sends exactly one Content-Type header", token.header("content-type"),
                List.of("application/json"));
        is("createToken sends no Authorization header", token.header("authorization"), List.of());
        is("createToken sends no chunked transfer encoding", token.header("transfer-encoding"), List.of());
        assertContentLength(token);
        is("TokenCreationSpec carries only the members the caller set, in specification order",
                canonical(token), expectedTokenBody);

        MockSddcManager.Recorded pool = log.get(1);
        is("createNetworkPool is a POST /v1/network-pools", pool.method() + " " + pool.rawTarget(),
                "POST /v1/network-pools");
        is("createNetworkPool is matched to its contract operation", pool.operationId(), "createNetworkPool");
        is("createNetworkPool sends exactly one bearer Authorization header", pool.header("authorization"),
                List.of(BEARER));
        is("createNetworkPool sends exactly one Accept header", pool.header("accept"), List.of("application/json"));
        is("createNetworkPool sends exactly one Content-Type header", pool.header("content-type"),
                List.of("application/json"));
        assertContentLength(pool);
        is("NetworkPool omits its read-only members and omits ipPools on the network that has none",
                canonical(pool),
                "{\"name\":\"" + POOL_NAME + "\",\"networks\":["
                        + "{\"type\":\"VMOTION\",\"vlanId\":1631,\"mtu\":9000,\"subnet\":\"172.16.31.0\","
                        + "\"mask\":\"255.255.255.0\",\"gateway\":\"172.16.31.253\","
                        + "\"ipPools\":[{\"start\":\"172.16.31.10\",\"end\":\"172.16.31.40\"}]},"
                        + "{\"type\":\"VSAN\",\"vlanId\":1632,\"mtu\":9000,\"subnet\":\"172.16.32.0\","
                        + "\"mask\":\"255.255.255.0\",\"gateway\":\"172.16.32.253\"}]}");

        MockSddcManager.Recorded networks = log.get(2);
        is("getNetworksOfNetworkPool addresses the pool the estate created",
                networks.method() + " " + networks.rawTarget(),
                "GET /v1/network-pools/" + POOL_ID_PATH + "/networks");
        is("getNetworksOfNetworkPool is matched to its contract operation", networks.operationId(),
                "getNetworksOfNetworkPool");
        is("the read-back carries no query", networks.rawQuery(), null);
        is("the read-back carries no body", networks.body().length, 0);
        is("the read-back declares no content type", networks.header("content-type"), List.of());
        is("the read-back sends exactly one bearer Authorization header", networks.header("authorization"),
                List.of(BEARER));

        if (log.size() > 3) {
            MockSddcManager.Recorded first = log.get(3);
            is("the first IP pool addition addresses the VMOTION network by its own id",
                    first.method() + " " + first.rawTarget(),
                    "POST /v1/network-pools/" + POOL_ID_PATH + "/networks/"
                            + mock.networkId("VMOTION") + "/ip-pools");
            is("the first addition is matched to its contract operation", first.operationId(),
                    "addIpPoolToNetworkOfNetworkPool");
            is("the first addition sends exactly one bearer Authorization header", first.header("authorization"),
                    List.of(BEARER));
            assertContentLength(first);
            is("IpPool carries start then end", canonical(first),
                    "{\"start\":\"172.16.31.60\",\"end\":\"172.16.31.79\"}");
        }
        if (log.size() > 4 && "addIpPoolToNetworkOfNetworkPool".equals(log.get(4).operationId())) {
            MockSddcManager.Recorded second = log.get(4);
            is("the second IP pool addition addresses the VSAN network by its own id",
                    second.method() + " " + second.rawTarget(),
                    "POST /v1/network-pools/" + POOL_ID_PATH + "/networks/"
                            + mock.networkId("VSAN") + "/ip-pools");
            assertContentLength(second);
            is("IpPool carries start then end", canonical(second),
                    "{\"start\":\"172.16.32.10\",\"end\":\"172.16.32.39\"}");
        }
    }

    private static void assertHostsRequest(MockSddcManager.Recorded hosts) {
        is("commissionHosts is a POST /v1/hosts", hosts.method() + " " + hosts.rawTarget(), "POST /v1/hosts");
        is("commissionHosts is matched to its contract operation", hosts.operationId(), "commissionHosts");
        is("commissionHosts sends exactly one bearer Authorization header", hosts.header("authorization"),
                List.of(BEARER));
        is("commissionHosts sends exactly one Content-Type header", hosts.header("content-type"),
                List.of("application/json"));
        assertContentLength(hosts);
        is("every HostCommissionSpec keeps specification order and omits every member the caller left unset",
                canonical(hosts),
                "[{\"fqdn\":\"esx-a01.vcf.lab\",\"username\":\"" + ESX_USER + "\",\"password\":\"" + ESX_PASSWORD
                        + "\",\"storageType\":\"VSAN\",\"networkPoolId\":\"" + POOL_ID
                        + "\",\"sshThumbprint\":\"SHA256:aa11\",\"sslThumbprint\":\"11:22:33:44\"},"
                        + "{\"fqdn\":\"esx-a02.vcf.lab\",\"username\":\"" + ESX_USER + "\",\"password\":\""
                        + ESX_PASSWORD + "\",\"storageType\":\"VSAN\",\"networkPoolId\":\"" + POOL_ID + "\"},"
                        + "{\"fqdn\":\"esx-a03.vcf.lab\",\"username\":\"" + ESX_USER + "\",\"password\":\""
                        + ESX_PASSWORD + "\",\"storageType\":\"VSAN\",\"networkPoolId\":\"" + POOL_ID
                        + "\",\"sslThumbprint\":\"55:66:77:88\"}]");
        isTrue("no unset member is sent as an empty string",
                !hosts.bodyText().contains("\"\""));
        isTrue("no unset member is sent as null", !hosts.bodyText().contains("null"));
        isTrue("networkPoolName is not invented", !hosts.bodyText().contains("networkPoolName"));
    }

    private static void assertContentLength(MockSddcManager.Recorded recorded) {
        List<String> declared = recorded.header("content-length");
        is("a bodied request declares exactly one content length", declared.size(), 1);
        if (declared.size() == 1) {
            is("the declared content length is the UTF-8 byte count of the body",
                    declared.get(0), String.valueOf(recorded.body().length));
        }
        is("a bodied request does not also declare a transfer encoding",
                recorded.header("transfer-encoding"), List.of());
    }

    private static void assertSteps(VcfCapacityOnboarding.OnboardingReport report, List<String[]> expected) {
        List<VcfCapacityOnboarding.Step> steps = report.steps();
        is("the report accounts for every planned step", steps.size(), expected.size());
        if (steps.size() != expected.size()) {
            return;
        }
        for (int i = 0; i < steps.size(); i++) {
            VcfCapacityOnboarding.Step step = steps.get(i);
            String label = "step " + i + " (" + expected.get(i)[0] + ")";
            is(label + " keeps its index", step.index(), i);
            is(label + " names its operation", step.operationId(), expected.get(i)[0]);
            is(label + " reports its outcome", String.valueOf(step.outcome()), expected.get(i)[1]);
            is(label + " reports whether it changed the estate", step.changedState(),
                    Boolean.parseBoolean(expected.get(i)[2]));
            isTrue(label + " carries a readable detail", step.detail() != null && !step.detail().isBlank());
        }
        VcfCapacityOnboarding.Step poolStep = steps.get(1);
        if (poolStep.outcome() == VcfCapacityOnboarding.StepOutcome.SUCCEEDED) {
            isTrue("the createNetworkPool detail names the pool the estate assigned",
                    poolStep.detail().contains(POOL_ID));
        }
        for (VcfCapacityOnboarding.Step step : steps) {
            if (step.outcome() == VcfCapacityOnboarding.StepOutcome.FAILED && report.failure() != null
                    && report.failure().errorCode() != null) {
                isTrue("the failed step detail names the error code the estate returned",
                        step.detail().contains(report.failure().errorCode()));
            }
        }
    }

    private static void assertNoSecretsLeaked(VcfCapacityOnboarding.OnboardingReport report) {
        StringBuilder rendered = new StringBuilder();
        for (VcfCapacityOnboarding.Step step : report.steps()) {
            rendered.append(step.operationId()).append(' ').append(step.detail()).append('\n');
        }
        rendered.append(report.networkPoolId()).append('\n')
                .append(report.networkPoolName()).append('\n')
                .append(report.networkIdsByType()).append('\n')
                .append(report.appliedIpPoolRanges()).append('\n')
                .append(report.commissionTaskId()).append('\n')
                .append(report.commissionTaskStatus()).append('\n');
        if (report.failure() != null) {
            rendered.append(report.failure().operationId()).append('\n')
                    .append(report.failure().errorCode()).append('\n')
                    .append(report.failure().message()).append('\n')
                    .append(report.failure().referenceToken()).append('\n');
        }
        String text = rendered.toString();
        isTrue("the report never repeats the SSO password", !text.contains(SSO_PASSWORD));
        isTrue("the report never repeats an ESXi password", !text.contains(ESX_PASSWORD));
        isTrue("the report never repeats an API key", !text.contains(API_KEY));
        isTrue("the report never repeats the access token", !text.contains(MockSddcManager.ACCESS_TOKEN));
        isTrue("the report never repeats the refresh token id", !text.contains(MockSddcManager.REFRESH_TOKEN_ID));
    }

    // ----------------------------------------------------------------- fixtures

    private static VcfCapacityOnboarding.Credentials credentials() {
        return new VcfCapacityOnboarding.Credentials(SSO_USER, SSO_PASSWORD);
    }

    private static VcfCapacityOnboarding.Credentials credentialsWithOptionalMembers() {
        return new VcfCapacityOnboarding.Credentials(SSO_USER, SSO_PASSWORD, API_KEY, "");
    }

    private static List<VcfCapacityOnboarding.NetworkSpec> standardNetworks() {
        return List.of(
                new VcfCapacityOnboarding.NetworkSpec("VMOTION", 1631, 9000, "172.16.31.0", "255.255.255.0",
                        "172.16.31.253",
                        List.of(new VcfCapacityOnboarding.IpRange("172.16.31.10", "172.16.31.40"))),
                new VcfCapacityOnboarding.NetworkSpec("VSAN", 1632, 9000, "172.16.32.0", "255.255.255.0",
                        "172.16.32.253", null));
    }

    private static VcfCapacityOnboarding.NetworkSpec network(String type, int vlanId, int mtu) {
        return new VcfCapacityOnboarding.NetworkSpec(type, vlanId, mtu, "172.16.31.0", "255.255.255.0",
                "172.16.31.253", null);
    }

    private static List<VcfCapacityOnboarding.IpPoolAddition> standardAdditions() {
        return List.of(
                new VcfCapacityOnboarding.IpPoolAddition("VMOTION",
                        new VcfCapacityOnboarding.IpRange("172.16.31.60", "172.16.31.79")),
                new VcfCapacityOnboarding.IpPoolAddition("VSAN",
                        new VcfCapacityOnboarding.IpRange("172.16.32.10", "172.16.32.39")));
    }

    private static List<VcfCapacityOnboarding.HostCommission> standardHosts() {
        return List.of(
                new VcfCapacityOnboarding.HostCommission("esx-a01.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN",
                        null, null, "SHA256:aa11", "11:22:33:44"),
                new VcfCapacityOnboarding.HostCommission("esx-a02.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN"),
                new VcfCapacityOnboarding.HostCommission("esx-a03.vcf.lab", ESX_USER, ESX_PASSWORD, "VSAN",
                        null, null, null, "55:66:77:88"));
    }

    private static VcfCapacityOnboarding.CapacityRequest standardRequest() {
        return new VcfCapacityOnboarding.CapacityRequest(
                POOL_NAME, standardNetworks(), standardAdditions(), standardHosts());
    }

    private static String[] step(String operationId, String outcome, boolean changedState) {
        return new String[] {operationId, outcome, String.valueOf(changedState)};
    }

    // ----------------------------------------------------------------- harness

    private static void rejects(String what, VcfCapacityOnboarding client,
            VcfCapacityOnboarding.CapacityRequest request) {
        try {
            client.onboard(request);
            record(what + ": expected IllegalArgumentException, the call returned instead");
        } catch (IllegalArgumentException | NullPointerException expected) {
            pass();
        } catch (RuntimeException unexpected) {
            record(what + ": expected IllegalArgumentException, saw " + unexpected);
        }
    }

    private static void runCase(String name, ThrowingRunnable body) {
        int before = failures.size();
        try {
            body.run();
        } catch (Throwable failure) {
            record(name + ": threw " + failure);
        }
        System.out.println((failures.size() == before ? "  ok   " : "  FAIL ") + name);
    }

    private interface ThrowingRunnable {
        void run() throws Exception;
    }

    private static String canonical(MockSddcManager.Recorded recorded) {
        return MockSddcManager.Json.write(MockSddcManager.Json.parse(recorded.bodyText()));
    }

    private static Map<String, Object> readJsonObject(Path path) throws IOException {
        return MockSddcManager.Json.object(
                MockSddcManager.Json.parse(Files.readString(path, StandardCharsets.UTF_8)));
    }

    private static void is(String what, Object actual, Object expected) {
        checks++;
        if (!Objects.equals(normalize(actual), normalize(expected))) {
            record(what + ": expected <" + normalize(expected) + ">, saw <" + normalize(actual) + ">");
        }
    }

    private static Object normalize(Object value) {
        if (value instanceof Integer number) {
            return (long) number;
        }
        if (value instanceof Map<?, ?> map) {
            Map<Object, Object> copy = new LinkedHashMap<>();
            map.forEach((key, entry) -> copy.put(key, normalize(entry)));
            return copy;
        }
        if (value instanceof List<?> items) {
            List<Object> copy = new ArrayList<>();
            items.forEach(item -> copy.add(normalize(item)));
            return copy;
        }
        return value;
    }

    private static void isTrue(String what, boolean condition) {
        checks++;
        if (!condition) {
            record(what + ": expected this to hold");
        }
    }

    private static void pass() {
        checks++;
    }

    private static void record(String failure) {
        failures.add(failure);
    }
}
