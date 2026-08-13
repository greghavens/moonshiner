import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

/** Protected acceptance harness for the focused SDDC Manager 9.0 host-commissioning contract. */
public final class TestMain {
    private static final String SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f";
    private static final String SPEC = "specifications/sddc-manager/sddc-manager-openapi.json";
    private static final List<String> OPERATION_IDS =
            List.of("createToken", "commissionHosts", "getTask");

    private static final String SSO_USER = "administrator@vsphere.local";
    private static final String SSO_PASSWORD = "VMw@re1!VMw@re1!";
    private static final String HOST_PASSWORD = "he said \"no\"\\then left";
    private static final String NETWORK_POOL_ID = "0b8d9f2c-1d54-4d1a-8f4b-2a7e5c0d3311";
    private static final String BEARER = "Bearer " + MockSddcManager.ACCESS_TOKEN;

    private static final SddcManagerClient.Credentials PASSWORD_LOGIN =
            new SddcManagerClient.Credentials(SSO_USER, SSO_PASSWORD, null, null);
    private static final String PASSWORD_LOGIN_BODY =
            "{\"username\":\"administrator@vsphere.local\",\"password\":\"VMw@re1!VMw@re1!\"}";

    public static void main(String[] args) throws Exception {
        testProtectedContractProvenance();
        testCommissionIsPolledToTerminalState();
        testUnsetOptionalMembersAreOmitted();
        testTaskIdIsPercentEncodedInPath();
        testTerminalFailureIsReportedFromThePoll();
        testUnfinishedTaskIsBoundedByPollLimit();
        testApiStatusAndProtocolFailures();
        testValidationHappensBeforeAnyRequest();
        testMockServesOnlyContractOperations();
        System.out.println(
                "PASS: SDDC Manager host commissioning polled to a terminal task state");
    }

    private static void testProtectedContractProvenance() throws Exception {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        for (String text : List.of(contract, sources)) {
            check(text.contains(SHA), "pinned 9.0.0.0 repository SHA must be recorded");
            check(text.contains(SPEC), "exact specification path must be recorded");
            for (String operationId : OPERATION_IDS) {
                check(text.contains(operationId), operationId + " operationId must be recorded");
            }
        }
        check(sources.contains("\"repositoryTag\": \"9.0.0.0\""),
                "the 9.0.0.0 release tag must be recorded");
        check(!contract.contains("9.1.0.0") && !sources.contains("9.1.0.0"),
                "the 9.1 revision of the specification is not the contract source");
        eq(OPERATION_IDS.size(), occurrences(sources, "\"specJsonPointer\": \"/paths/"),
                "one specification source record per operationId");
        check(sources.contains("\"documentationPageUsedAsContractSource\": false"),
                "contract source must be the OpenAPI specification");
        eq(OPERATION_IDS.size(), occurrences(contract, "\"operationId\""),
                "focused contract must contain exactly three operations");
    }

    private static void testCommissionIsPolledToTerminalState() throws Exception {
        RecordingSleeper sleeper = new RecordingSleeper();
        try (MockSddcManager mock = new MockSddcManager()) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 6, 200L, sleeper);
            SddcManagerClient.CommissionOutcome outcome = client.commissionHosts(List.of(
                    host("sfo01-m01-esx05.rainpole.io", "VSAN", null, null, null, "AA:BB:CC"),
                    host("sfo01-m01-esx06.rainpole.io", "VVOL", "FC", "sfo01-np01",
                            "11:22:33", null)));

            eq(MockSddcManager.TASK_ID, outcome.taskId(), "accepted task id");
            eq("Commissioning Hosts", outcome.taskName(), "task name from the terminal poll");
            eq("SUCCESSFUL", outcome.status(), "normalized terminal status");
            eq(4, outcome.pollCount(), "every getTask poll is counted");
            eq(List.of("2f7a8c10-1f24-4b6d-9d5b-0a5c1e8a3f77",
                            "d1c0b9a8-5c6e-4f2b-8a13-7e9f4c2d6b05"),
                    outcome.resourceIds(), "resources of the terminal task");
            eq(List.of(200L, 200L, 200L), sleeper.waits,
                    "wait between non-terminal polls only, never after the terminal poll");

            List<MockSddcManager.RecordedRequest> requests = mock.requests();
            eq(6, requests.size(), "token, commission, and four polls");

            assertJsonPost(requests.get(0), "/v1/tokens", PASSWORD_LOGIN_BODY, null);
            assertJsonPost(requests.get(1), "/v1/hosts",
                    "[{\"fqdn\":\"sfo01-m01-esx05.rainpole.io\","
                            + "\"username\":\"root\","
                            + "\"password\":\"he said \\\"no\\\"\\\\then left\","
                            + "\"storageType\":\"VSAN\","
                            + "\"networkPoolId\":\"" + NETWORK_POOL_ID + "\","
                            + "\"sslThumbprint\":\"AA:BB:CC\"},"
                            + "{\"fqdn\":\"sfo01-m01-esx06.rainpole.io\","
                            + "\"username\":\"root\","
                            + "\"password\":\"he said \\\"no\\\"\\\\then left\","
                            + "\"storageType\":\"VVOL\","
                            + "\"vvolStorageProtocolType\":\"FC\","
                            + "\"networkPoolId\":\"" + NETWORK_POOL_ID + "\","
                            + "\"networkPoolName\":\"sfo01-np01\","
                            + "\"sshThumbprint\":\"11:22:33\"}]",
                    BEARER);
            for (int index = 2; index < 6; index++) {
                assertPoll(requests.get(index), "/v1/tasks/" + MockSddcManager.TASK_ID);
            }
            eq(4L, requests.stream().filter(request -> request.method().equals("GET")).count(),
                    "the accepted 202 status is never believed without polling");
        }
    }

    private static void testUnsetOptionalMembersAreOmitted() throws Exception {
        assertTokenBody(
                new SddcManagerClient.Credentials(null, null, null, null),
                "{}");
        assertTokenBody(
                new SddcManagerClient.Credentials(
                        "token-user", "token-password", "api-key", "id-token"),
                "{\"username\":\"token-user\",\"password\":\"token-password\","
                        + "\"apiKey\":\"api-key\",\"idToken\":\"id-token\"}");

        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                new MockSddcManager.Reply(200, MockSddcManager.pendingTask("Successful"))))) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl() + "/",
                    new SddcManagerClient.Credentials(null, null, "api-key-8821", null),
                    4, 0L, new RecordingSleeper());
            SddcManagerClient.CommissionOutcome outcome = client.commissionHosts(List.of(
                    new SddcManagerClient.HostCommission(
                            "sfo01-m01-esx07.rainpole.io", "root", "VMw@re1!", "VMFS",
                            null, NETWORK_POOL_ID, null, null, null)));
            eq(1, outcome.pollCount(), "a terminal first poll stops immediately");
            eq(List.of(), outcome.resourceIds(), "absent Task.resources yields no resource ids");

            List<MockSddcManager.RecordedRequest> requests = mock.requests();
            eq("{\"apiKey\":\"api-key-8821\"}", requests.get(0).bodyText(),
                    "only the supplied TokenCreationSpec member is sent");
            String commissionBody = requests.get(1).bodyText();
            eq("[{\"fqdn\":\"sfo01-m01-esx07.rainpole.io\",\"username\":\"root\","
                            + "\"password\":\"VMw@re1!\",\"storageType\":\"VMFS\","
                            + "\"networkPoolId\":\"" + NETWORK_POOL_ID + "\"}]",
                    commissionBody, "unset HostCommissionSpec members are omitted entirely");
            for (String omitted : List.of("username\":\"\"", "password\":null", "apiKey",
                    "idToken", "vvolStorageProtocolType", "networkPoolName", "sshThumbprint",
                    "sslThumbprint", "null", "\"\"")) {
                check(!commissionBody.contains(omitted),
                        "unset member must be absent rather than empty: " + omitted);
            }
        }

        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                new MockSddcManager.Reply(200, MockSddcManager.pendingTask("COMPLETED_WITH_WARNING"))))) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 4, 0L, new RecordingSleeper());
            SddcManagerClient.CommissionOutcome outcome = client.commissionHosts(List.of(
                    new SddcManagerClient.HostCommission(
                            "sfo01-m01-esx08.rainpole.io", "root", "VMw@re1!", "NFS",
                            null, NETWORK_POOL_ID, "", "", null)));
            eq("COMPLETED_WITH_WARNING", outcome.status(),
                    "a warning completion is a successful terminal state");
            eq("[{\"fqdn\":\"sfo01-m01-esx08.rainpole.io\",\"username\":\"root\","
                            + "\"password\":\"VMw@re1!\",\"storageType\":\"NFS\","
                            + "\"networkPoolId\":\"" + NETWORK_POOL_ID + "\","
                            + "\"networkPoolName\":\"\",\"sshThumbprint\":\"\"}]",
                    mock.requests().get(1).bodyText(),
                    "an explicitly empty optional member is preserved, not dropped");
        }
    }

    private static void testTaskIdIsPercentEncodedInPath() throws Exception {
        String awkwardId = "commission task 7/9";
        String accepted = MockSddcManager.task(awkwardId, "Commissioning Hosts", "HOST_COMMISSION",
                "Successful", "2026-05-04T09:15:00.000Z", null, null, null);
        String polled = MockSddcManager.task(awkwardId, "Commissioning Hosts", "HOST_COMMISSION",
                "SKIPPED", "2026-05-04T09:15:00.000Z", "2026-05-04T09:16:00.000Z", null, null);
        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, accepted),
                new MockSddcManager.Reply(200, polled)))) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 4, 0L, new RecordingSleeper());
            SddcManagerClient.CommissionOutcome outcome =
                    client.commissionHosts(List.of(host(
                            "sfo01-m01-esx09.rainpole.io", "VSAN", null, null, null, null)));
            eq("SKIPPED", outcome.status(), "SKIPPED is a successful terminal state");
            assertPoll(mock.requests().get(2), "/v1/tasks/commission%20task%207%2F9");
        }
    }

    private static void testTerminalFailureIsReportedFromThePoll() throws Exception {
        String failed = MockSddcManager.task(MockSddcManager.TASK_ID, "Commissioning Hosts",
                "HOST_COMMISSION", "Failed", "2026-05-04T09:15:00.000Z",
                "2026-05-04T09:18:00.000Z", null,
                List.of(MockSddcManager.error("HOST_COMMISSION_SSL_THUMBPRINT_MISMATCH",
                        "Rejected credential " + SSO_PASSWORD + " and token "
                                + MockSddcManager.ACCESS_TOKEN,
                        "ref-72c1")));
        RecordingSleeper sleeper = new RecordingSleeper();
        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, MockSddcManager.pendingTask("Successful")),
                new MockSddcManager.Reply(200, failed)))) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 5, 100L, sleeper);
            try {
                client.commissionHosts(List.of(host(
                        "sfo01-m01-esx05.rainpole.io", "VSAN", null, null, null, "AA:BB:CC")));
                fail("expected TaskFailedException");
            } catch (SddcManagerClient.TaskFailedException exception) {
                eq(MockSddcManager.TASK_ID, exception.taskId(), "failed task id");
                eq("FAILED", exception.taskStatus(), "normalized failure status");
                eq("HOST_COMMISSION_SSL_THUMBPRINT_MISMATCH", exception.errorCode(),
                        "error code from the first task error");
                eq("ref-72c1", exception.referenceToken(), "reference token from the first error");
                assertNoSecrets(exception.getMessage(), "task failure");
            }
            eq(3, mock.requests().size(), "a terminal failure stops polling immediately");
            eq(List.of(), sleeper.waits, "no wait follows a terminal poll");
        }

        String cancelled = MockSddcManager.task(MockSddcManager.TASK_ID, "Commissioning Hosts",
                "HOST_COMMISSION", "Cancelled", "2026-05-04T09:15:00.000Z",
                "2026-05-04T09:18:00.000Z", null, null);
        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                new MockSddcManager.Reply(200, cancelled)))) {
            try {
                new SddcManagerClient(mock.baseUrl(), PASSWORD_LOGIN, 5, 100L,
                        new RecordingSleeper()).commissionHosts(List.of(host(
                                "sfo01-m01-esx05.rainpole.io", "VSAN",
                                null, null, null, null)));
                fail("expected TaskFailedException for CANCELLED");
            } catch (SddcManagerClient.TaskFailedException exception) {
                eq("CANCELLED", exception.taskStatus(), "normalized cancelled status");
                eq(null, exception.errorCode(), "cancelled task without errors has no error code");
                eq(null, exception.referenceToken(),
                        "cancelled task without errors has no reference token");
            }
            eq(3, mock.requests().size(), "CANCELLED stops polling immediately");
        }
    }

    private static void testUnfinishedTaskIsBoundedByPollLimit() throws Exception {
        for (String status : List.of("In Progress", "PENDING")) {
            RecordingSleeper sleeper = new RecordingSleeper();
            try (MockSddcManager mock = new MockSddcManager(List.of(
                    new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                            MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                    new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                    new MockSddcManager.Reply(200, MockSddcManager.pendingTask(status)),
                    new MockSddcManager.Reply(200, MockSddcManager.pendingTask(status)),
                    new MockSddcManager.Reply(200, MockSddcManager.pendingTask(status))))) {
                SddcManagerClient client = new SddcManagerClient(
                        mock.baseUrl(), PASSWORD_LOGIN, 3, 500L, sleeper);
                try {
                    client.commissionHosts(List.of(host(
                            "sfo01-m01-esx05.rainpole.io", "VSAN", null, null, null, null)));
                    fail("expected TaskTimeoutException for status " + status);
                } catch (SddcManagerClient.TaskTimeoutException exception) {
                    eq(MockSddcManager.TASK_ID, exception.taskId(), "timed out task id");
                    eq(3, exception.pollCount(), "poll limit is the bound on " + status);
                }
                eq(5, mock.requests().size(), "no poll beyond the limit");
                eq(List.of(500L, 500L), sleeper.waits,
                        "no wait follows the final permitted poll");
            }
        }

        String secretTask = MockSddcManager.task(
                MockSddcManager.ACCESS_TOKEN, "Commissioning Hosts", "HOST_COMMISSION",
                "PENDING", "2026-05-04T09:15:00.000Z", null, null, null);
        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, secretTask),
                new MockSddcManager.Reply(200, secretTask)))) {
            try {
                new SddcManagerClient(mock.baseUrl(), PASSWORD_LOGIN, 1, 0L,
                        new RecordingSleeper()).commissionHosts(List.of(host(
                                "sfo01-m01-esx05.rainpole.io", "VSAN",
                                null, null, null, null)));
                fail("expected TaskTimeoutException for secret-shaped task id");
            } catch (SddcManagerClient.TaskTimeoutException exception) {
                eq(MockSddcManager.ACCESS_TOKEN, exception.taskId(), "timed out task id field");
                assertNoSecrets(exception.getMessage(), "task timeout");
            }
        }
    }

    private static void testApiStatusAndProtocolFailures() throws Exception {
        assertApiFailure(
                List.of(new MockSddcManager.Reply(200, "{\"errorCode\":\"WRONG_STATUS\"}")),
                "createToken", 200, "WRONG_STATUS", 1);
        assertApiFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(400,
                                "{\"errorCode\":\"HOST_ALREADY_COMMISSIONED\"}")),
                "commissionHosts", 400, "HOST_ALREADY_COMMISSIONED", 2);
        assertApiFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(200, MockSddcManager.pendingTask("PENDING"))),
                "commissionHosts", 200, null, 2);
        assertApiFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                        new MockSddcManager.Reply(404, "{\"errorCode\":\"TASK_NOT_FOUND\"}")),
                "getTask", 404, "TASK_NOT_FOUND", 3);

        assertProtocolFailure(
                List.of(new MockSddcManager.Reply(201, "{\"refreshToken\":{\"id\":\"only\"}}")),
                "createToken");
        assertProtocolFailure(
                List.of(new MockSddcManager.Reply(201,
                        MockSddcManager.tokenPair("broken\nheader", "r"))),
                "createToken");
        assertProtocolFailure(
                List.of(new MockSddcManager.Reply(201, "text/plain",
                        MockSddcManager.tokenPair(MockSddcManager.ACCESS_TOKEN, "r"))),
                "createToken");
        assertProtocolFailure(
                List.of(new MockSddcManager.Reply(201, "application/jsonp",
                        MockSddcManager.tokenPair(MockSddcManager.ACCESS_TOKEN, "r"))),
                "createToken");
        assertProtocolFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(202, "text/plain",
                                MockSddcManager.pendingTask("PENDING"))),
                "commissionHosts");
        assertProtocolFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(202,
                                "{\"name\":\"Commissioning Hosts\",\"status\":\"Pending\","
                                        + "\"creationTimestamp\":\"2026-05-04T09:15:00.000Z\"}")),
                "commissionHosts");
        assertProtocolFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                        new MockSddcManager.Reply(200, MockSddcManager.task(
                                "a-different-task", "Commissioning Hosts", "HOST_COMMISSION",
                                "Successful", "2026-05-04T09:15:00.000Z", null, null, null))),
                "getTask");
        assertProtocolFailure(
                List.of(
                        new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                        new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                        new MockSddcManager.Reply(200, "text/plain",
                                MockSddcManager.pendingTask("Successful"))),
                "getTask");
        for (String unclassified : List.of("Frobnicating", "NOT_APPLICABLE", "UNKNOWN")) {
            assertProtocolFailure(
                    List.of(
                            new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                                    MockSddcManager.ACCESS_TOKEN,
                                    MockSddcManager.REFRESH_TOKEN_ID)),
                            new MockSddcManager.Reply(202,
                                    MockSddcManager.pendingTask("PENDING")),
                            new MockSddcManager.Reply(200,
                                    MockSddcManager.pendingTask(unclassified))),
                    "getTask");
        }
    }

    private static void testValidationHappensBeforeAnyRequest() throws Exception {
        RecordingSleeper sleeper = new RecordingSleeper();
        for (ThrowingRunnable invalid : List.<ThrowingRunnable>of(
                () -> new SddcManagerClient(" ", PASSWORD_LOGIN, 3, 0L, sleeper),
                () -> new SddcManagerClient("ftp://127.0.0.1", PASSWORD_LOGIN, 3, 0L, sleeper),
                () -> new SddcManagerClient("http://user@127.0.0.1", PASSWORD_LOGIN, 3, 0L, sleeper),
                () -> new SddcManagerClient("http://127.0.0.1/api", PASSWORD_LOGIN, 3, 0L, sleeper),
                () -> new SddcManagerClient("http://127.0.0.1?x=1", PASSWORD_LOGIN, 3, 0L, sleeper),
                () -> new SddcManagerClient("http://127.0.0.1#f", PASSWORD_LOGIN, 3, 0L, sleeper),
                () -> new SddcManagerClient("http://127.0.0.1", PASSWORD_LOGIN, 0, 0L, sleeper),
                () -> new SddcManagerClient(
                        "http://127.0.0.1", PASSWORD_LOGIN, 3, -1L, sleeper))) {
            expect(IllegalArgumentException.class, invalid, "constructor validation");
        }
        expect(NullPointerException.class,
                () -> new SddcManagerClient("http://127.0.0.1", null, 3, 0L, sleeper),
                "null credentials");
        expect(NullPointerException.class,
                () -> new SddcManagerClient("http://127.0.0.1", PASSWORD_LOGIN, 3, 0L, null),
                "null sleeper");

        try (MockSddcManager mock = new MockSddcManager(List.of())) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 3, 0L, sleeper);
            expect(NullPointerException.class,
                    () -> client.commissionHosts(null), "null host list");
            for (ThrowingRunnable invalid : List.<ThrowingRunnable>of(
                    () -> client.commissionHosts(List.of()),
                    () -> client.commissionHosts(List.of(host(
                            "  ", "VSAN", null, null, null, null))),
                    () -> client.commissionHosts(List.of(new SddcManagerClient.HostCommission(
                            "sfo01-m01-esx05.rainpole.io", " ", "VMw@re1!", "VSAN",
                            null, NETWORK_POOL_ID, null, null, null))),
                    () -> client.commissionHosts(List.of(new SddcManagerClient.HostCommission(
                            "sfo01-m01-esx05.rainpole.io", "root", null, "VSAN",
                            null, NETWORK_POOL_ID, null, null, null))),
                    () -> client.commissionHosts(List.of(new SddcManagerClient.HostCommission(
                            "sfo01-m01-esx05.rainpole.io", "root", "VMw@re1!", " ",
                            null, NETWORK_POOL_ID, null, null, null))),
                    () -> client.commissionHosts(List.of(new SddcManagerClient.HostCommission(
                            "sfo01-m01-esx05.rainpole.io", "root", "VMw@re1!", "VSAN",
                            null, null, null, null, null))),
                    () -> client.commissionHosts(List.of(host(
                            "sfo01-m01-esx05.rainpole.io", "vsan", null, null, null, null))),
                    () -> client.commissionHosts(List.of(host(
                            "sfo01-m01-esx05.rainpole.io", "VVOL", null, null, null, null))),
                    () -> client.commissionHosts(List.of(host(
                            "sfo01-m01-esx05.rainpole.io", "VVOL", "SAS", null, null, null))),
                    () -> client.commissionHosts(List.of(host(
                            "sfo01-m01-esx05.rainpole.io", "VSAN", "FC", null, null, null))),
                    () -> client.commissionHosts(List.of(
                            host("sfo01-m01-esx05.rainpole.io", "VSAN", null, null, null, null),
                            host("SFO01-M01-ESX05.rainpole.io", "NFS", null, null, null, null))))) {
                expect(IllegalArgumentException.class, invalid, "host specification validation");
            }
            expect(NullPointerException.class,
                    () -> client.commissionHosts(Arrays.asList(
                            (SddcManagerClient.HostCommission) null)),
                    "null host specification");
            for (String storageType : List.of(
                    "VSAN", "VSAN_ESA", "VSAN_REMOTE", "VSAN_MAX",
                    "NFS", "VMFS_FC", "VVOL", "VMFS")) {
                SddcManagerClient.validateHosts(List.of(host(
                        "accepted-" + storageType.toLowerCase() + ".example.test",
                        storageType, storageType.equals("VVOL") ? "ISCSI" : null,
                        null, null, null)));
            }
            for (String protocol : List.of("ISCSI", "NFS", "FC")) {
                SddcManagerClient.validateHosts(List.of(host(
                        "accepted-" + protocol.toLowerCase() + ".example.test",
                        "VVOL", protocol, null, null, null)));
            }
            eq(0, mock.requests().size(), "argument validation must happen before the wire");
            eq(List.of(), sleeper.waits, "rejected input must not wait");
        }
    }

    private static void testMockServesOnlyContractOperations() throws Exception {
        try (MockSddcManager mock = new MockSddcManager(List.of())) {
            for (String outside : List.of("/v1/hosts/validations", "/v1/tasks", "/v1/bundles")) {
                HttpResponse<String> response = HttpClient.newHttpClient().send(
                        HttpRequest.newBuilder(URI.create(mock.baseUrl() + outside))
                                .GET().build(),
                        HttpResponse.BodyHandlers.ofString());
                eq(404, response.statusCode(),
                        "route outside the focused contract must not be served: " + outside);
            }
        }
    }

    private static SddcManagerClient.HostCommission host(
            String fqdn,
            String storageType,
            String vvolStorageProtocolType,
            String networkPoolName,
            String sshThumbprint,
            String sslThumbprint) {
        return new SddcManagerClient.HostCommission(
                fqdn, "root", HOST_PASSWORD, storageType, vvolStorageProtocolType,
                NETWORK_POOL_ID, networkPoolName, sshThumbprint, sslThumbprint);
    }

    private static void assertTokenBody(
            SddcManagerClient.Credentials credentials, String expectedBody) throws Exception {
        try (MockSddcManager mock = new MockSddcManager(List.of(
                new MockSddcManager.Reply(201, MockSddcManager.tokenPair(
                        MockSddcManager.ACCESS_TOKEN, MockSddcManager.REFRESH_TOKEN_ID)),
                new MockSddcManager.Reply(202, MockSddcManager.pendingTask("PENDING")),
                new MockSddcManager.Reply(200, MockSddcManager.pendingTask("Successful"))))) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), credentials, 1, 0L, new RecordingSleeper());
            client.commissionHosts(List.of(host(
                    "sfo01-m01-esx07.rainpole.io", "VSAN", null, null, null, null)));
            eq(expectedBody, mock.requests().get(0).bodyText(),
                    "TokenCreationSpec declaration order and omission");
        }
    }

    private static void assertApiFailure(
            List<MockSddcManager.Reply> replies,
            String operationId,
            int statusCode,
            String errorCode,
            int expectedRequests) throws Exception {
        try (MockSddcManager mock = new MockSddcManager(replies)) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 3, 0L, new RecordingSleeper());
            try {
                client.commissionHosts(List.of(host(
                        "sfo01-m01-esx05.rainpole.io", "VSAN", null, null, null, null)));
                fail("expected VcfApiException from " + operationId);
            } catch (SddcManagerClient.VcfApiException exception) {
                eq(operationId, exception.operationId(), "failing operationId");
                eq(statusCode, exception.statusCode(), operationId + " status");
                eq(errorCode, exception.errorCode(), operationId + " error code");
                assertNoSecrets(exception.getMessage(), operationId);
            }
            eq(expectedRequests, mock.requests().size(), operationId + " stops on failure");
        }
    }

    private static void assertProtocolFailure(
            List<MockSddcManager.Reply> replies, String operationId) throws Exception {
        try (MockSddcManager mock = new MockSddcManager(replies)) {
            SddcManagerClient client = new SddcManagerClient(
                    mock.baseUrl(), PASSWORD_LOGIN, 3, 0L, new RecordingSleeper());
            try {
                client.commissionHosts(List.of(host(
                        "sfo01-m01-esx05.rainpole.io", "VSAN", null, null, null, null)));
                fail("expected ProtocolException from " + operationId);
            } catch (SddcManagerClient.ProtocolException exception) {
                eq(operationId, exception.operationId(), "protocol failure operationId");
                assertNoSecrets(exception.getMessage(), operationId);
            }
        }
    }

    private static void assertJsonPost(
            MockSddcManager.RecordedRequest request,
            String rawTarget,
            String expectedBody,
            String authorization) {
        byte[] expected = expectedBody.getBytes(StandardCharsets.UTF_8);
        eq("POST", request.method(), rawTarget + " method");
        eq(rawTarget, request.rawTarget(), "raw request target");
        eq(null, request.rawQuery(), rawTarget + " carries no query");
        eq(List.of("application/json"), request.headerValues("Accept"),
                rawTarget + " single Accept header");
        eq(List.of("application/json"), request.headerValues("Content-Type"),
                rawTarget + " single Content-Type header");
        eq(List.of(Integer.toString(expected.length)), request.headerValues("Content-Length"),
                rawTarget + " fixed content length");
        eq(List.of(), request.headerValues("Transfer-Encoding"),
                rawTarget + " transfer encoding omitted");
        eq(authorization == null ? List.of() : List.of(authorization),
                request.headerValues("Authorization"), rawTarget + " authorization");
        check(Arrays.equals(expected, request.body()),
                rawTarget + " exact body bytes: expected <" + expectedBody
                        + "> but was <" + request.bodyText() + ">");
    }

    private static void assertPoll(MockSddcManager.RecordedRequest request, String rawTarget) {
        eq("GET", request.method(), "getTask method");
        eq(rawTarget, request.rawTarget(), "getTask raw target");
        eq(null, request.rawQuery(), "getTask carries no query");
        eq(List.of(BEARER), request.headerValues("Authorization"),
                "getTask single Authorization header");
        eq(List.of("application/json"), request.headerValues("Accept"),
                "getTask single Accept header");
        eq(List.of(), request.headerValues("Content-Type"), "getTask Content-Type omitted");
        eq(List.of(), request.headerValues("Transfer-Encoding"),
                "getTask transfer encoding omitted");
        eq(0, request.body().length, "getTask body empty");
        List<String> lengths = request.headerValues("Content-Length");
        check(lengths.isEmpty() || lengths.equals(List.of("0")),
                "getTask must not have a positive content length: " + lengths);
    }

    private static void assertNoSecrets(String message, String label) {
        for (String secret : List.of(SSO_PASSWORD, HOST_PASSWORD, MockSddcManager.ACCESS_TOKEN,
                MockSddcManager.REFRESH_TOKEN_ID, "api-key-8821")) {
            check(message == null || !message.contains(secret),
                    label + " message must not leak a secret");
        }
    }

    private static int occurrences(String text, String needle) {
        int count = 0;
        for (int at = 0; (at = text.indexOf(needle, at)) >= 0; at += needle.length()) {
            count++;
        }
        return count;
    }

    private static void expect(
            Class<? extends Throwable> type, ThrowingRunnable action, String label) {
        try {
            action.run();
            fail("expected " + type.getSimpleName() + " for " + label);
        } catch (Throwable thrown) {
            if (!type.isInstance(thrown)) {
                throw new AssertionError(label + " threw " + thrown, thrown);
            }
        }
    }

    private static void eq(Object expected, Object actual, String label) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(
                    label + ": expected <" + expected + "> but was <" + actual + ">");
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static void fail(String message) {
        throw new AssertionError(message);
    }

    private static final class RecordingSleeper implements SddcManagerClient.Sleeper {
        private final List<Long> waits = new ArrayList<>();

        @Override
        public void pause(long millis) {
            waits.add(millis);
        }
    }

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
