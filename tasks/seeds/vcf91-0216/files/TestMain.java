import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

public final class TestMain {
    private static final String INITIAL_TOKEN = "fixture-access-token-initial";
    private static final String REFRESH_ID = "fixture-refresh/\"17\"\nline";
    private static final String SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";
    private static final String SPEC =
            "specifications/vcf-installer/vcf-installer-openapi.json";

    public static void main(String[] args) throws Exception {
        testProtectedContractProvenance();
        testRefreshesInterruptedPageWithoutLosingWork();
        testOptionalOmissionAndExplicitValues();
        testRefreshFailuresAndSingleRefreshBound();
        testRefreshStateIsPerInvocationAndResultsAreFresh();
        testStatusAndMediaTypeHandling();
        testPaginationAndResponseProtocol();
        testTaskProtocol();
        testValidationBeforeWire();
        testMockServesOnlyFocusedOperations();
        System.out.println("PASS: VCF Installer paged task retrieval with access-token refresh");
    }

    private static void testProtectedContractProvenance() throws Exception {
        String contract = Files.readString(Path.of("docs", "contract.json"));
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        for (String text : List.of(contract, sources)) {
            check(text.contains(SHA), "pinned repository SHA must be recorded");
            check(text.contains(SPEC), "exact specification path must be recorded");
            check(text.contains("getTasks"), "getTasks operationId must be recorded");
            check(text.contains("refreshAccessToken"),
                    "refreshAccessToken operationId must be recorded");
        }
        eq(2, occurrences(sources, "\"specJsonPointer\""),
                "one specification source record per operationId");
        check(sources.contains("\"documentationPageUsedAsContractSource\": false"),
                "contract source must be the OpenAPI specification");
        eq(2, occurrences(contract, "\"operationId\""),
                "focused contract must contain exactly two operations");
    }

    private static void testRefreshesInterruptedPageWithoutLosingWork() throws Exception {
        VcfInstallerClient.TaskQuery query = query(
                null, "In Progress", null, null, null, 0L,
                null, null, null, Boolean.FALSE, 2);
        try (MockVcfInstaller mock = new MockVcfInstaller()) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            List<VcfInstallerClient.Task> tasks = client.listAllTasks(query);

            eq(List.of(
                    "task-zero-a", "task-zero-b", "task-one-a", "task-one-b", "task-two-a"),
                    tasks.stream().map(VcfInstallerClient.Task::id).toList(),
                    "all tasks retained in page order");
            eq(null, tasks.get(0).type(), "absent optional Task.type remains null");
            eq("BUNDLE_DOWNLOAD", tasks.get(1).type(), "present optional Task.type retained");
            eq("Inventory", tasks.get(0).name(), "Task.name retained");
            eq("Successful", tasks.get(0).status(), "Task.status retained");
            eq("2026-07-01T10:00:00Z", tasks.get(0).creationTimestamp(),
                    "Task.creationTimestamp retained");
            expect(UnsupportedOperationException.class,
                    () -> tasks.add(tasks.get(0)), "returned list is unmodifiable");

            List<MockVcfInstaller.RecordedRequest> requests = mock.requests();
            eq(5, requests.size(), "page, expired page, refresh, retried page, final page");
            String base = "/v1/tasks?taskStatus=In%20Progress&completedAfter=0"
                    + "&doLiveRefresh=false&pageSize=2";
            assertGet(requests.get(0), base, INITIAL_TOKEN);
            assertGet(requests.get(1), base + "&pageNumber=1", INITIAL_TOKEN);
            assertRefresh(requests.get(2), "\"fixture-refresh/\\\"17\\\"\\nline\"");
            assertGet(requests.get(3), base + "&pageNumber=1",
                    MockVcfInstaller.FRESH_ACCESS_TOKEN);
            assertGet(requests.get(4), base + "&pageNumber=2",
                    MockVcfInstaller.FRESH_ACCESS_TOKEN);
            eq(1L, requests.stream()
                    .filter(request -> request.rawTarget().contains("pageNumber=1"))
                    .filter(request -> request.headerValues("Authorization")
                            .equals(List.of("Bearer " + INITIAL_TOKEN)))
                    .count(), "expired page attempted once with initial token");
            eq(1L, requests.stream()
                    .filter(request -> request.rawTarget().contains("pageNumber=1"))
                    .filter(request -> request.headerValues("Authorization")
                            .equals(List.of("Bearer " + MockVcfInstaller.FRESH_ACCESS_TOKEN)))
                    .count(), "same page retried once with fresh token");
            eq(1L, requests.stream()
                    .filter(request -> request.rawTarget().contains("pageSize=2")
                            && !request.rawTarget().contains("pageNumber="))
                    .count(), "page zero must not restart after refresh");
        }
    }

    private static void testOptionalOmissionAndExplicitValues() throws Exception {
        String empty = MockVcfInstaller.page(0, 0, 0, 0, List.of());
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(200, empty)))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl() + "/", INITIAL_TOKEN, REFRESH_ID);
            List<VcfInstallerClient.Task> tasks = client.listAllTasks(
                    query(null, null, null, null, null, null,
                            null, null, null, null, 100));
            eq(List.of(), tasks, "empty collection");
            eq(1, mock.requests().size(), "one empty page request");
            assertGet(mock.requests().get(0), "/v1/tasks?pageSize=100", INITIAL_TOKEN);
            for (String omitted : List.of(
                    "limit", "taskStatus", "taskType", "resourceId", "resourceType",
                    "completedAfter", "orderDirection", "orderBy", "taskName",
                    "doLiveRefresh", "pageNumber")) {
                check(!mock.requests().get(0).rawTarget().contains(omitted),
                        "unset optional query member must be absent: " + omitted);
            }
        }

        String twoTasks = MockVcfInstaller.page(0, 2, 2, 1, List.of(
                MockVcfInstaller.task("explicit-a", "A", null, "Pending", "t0"),
                MockVcfInstaller.task("explicit-b", "B", "", "Queued", "t1")));
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(200, twoTasks)))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            List<VcfInstallerClient.Task> tasks = client.listAllTasks(query(
                    0, "", "TYPE/A", "node 1", "μ", 0L,
                    "", "name+time", "two words", Boolean.FALSE, 2));
            eq("", tasks.get(1).type(), "explicit empty Task.type retained");
            assertGet(mock.requests().get(0),
                    "/v1/tasks?limit=0&taskStatus=&taskType=TYPE%2FA&resourceId=node%201"
                            + "&resourceType=%CE%BC&completedAfter=0&orderDirection="
                            + "&orderBy=name%2Btime&taskName=two%20words&doLiveRefresh=false"
                            + "&pageSize=2",
                    INITIAL_TOKEN);
        }

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(200, empty)))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            client.listAllTasks(query(
                    null, "a&b=c%~😀", null, null, null, null,
                    null, null, null, Boolean.TRUE, 1));
            assertGet(mock.requests().get(0),
                    "/v1/tasks?taskStatus=a%26b%3Dc%25~%F0%9F%98%80"
                            + "&doLiveRefresh=true&pageSize=1",
                    INITIAL_TOKEN);
        }
    }

    private static void testRefreshFailuresAndSingleRefreshBound() throws Exception {
        VcfInstallerClient.TaskQuery one = query(
                null, null, null, null, null, null,
                null, null, null, null, 1);

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(401, "{\"errorCode\":\"EXPIRED\"}"),
                new MockVcfInstaller.Reply(500, "{\"errorCode\":\"REFRESH_FAILED\"}")))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            try {
                client.listAllTasks(one);
                fail("expected refresh VcfApiException");
            } catch (VcfInstallerClient.VcfApiException exception) {
                eq("refreshAccessToken", exception.operationId(), "refresh failure operationId");
                eq(500, exception.statusCode(), "refresh failure status");
                check(!exception.getMessage().contains(INITIAL_TOKEN),
                        "API failure must not leak access token");
                check(!exception.getMessage().contains(REFRESH_ID),
                        "API failure must not leak refresh token ID");
            }
            eq(2, mock.requests().size(), "refresh failure stops immediately");
        }

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(401, "{}"),
                new MockVcfInstaller.Reply(200,
                        "\"" + MockVcfInstaller.FRESH_ACCESS_TOKEN + "\""),
                new MockVcfInstaller.Reply(401, "{\"errorCode\":\"EXPIRED_AGAIN\"}")))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            try {
                client.listAllTasks(one);
                fail("expected final second 401");
            } catch (VcfInstallerClient.VcfApiException exception) {
                eq("getTasks", exception.operationId(), "second 401 operationId");
                eq(401, exception.statusCode(), "second 401 status");
            }
            eq(1L, mock.requests().stream()
                    .filter(request -> request.method().equals("PATCH")).count(),
                    "only one refresh is allowed per invocation");
        }

        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(401, "{}"),
                        new MockVcfInstaller.Reply(200, "{}")),
                one, "refreshAccessToken", "refresh success must be a JSON string");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(401, "{}"),
                        new MockVcfInstaller.Reply(200, "\"bad\\nheader\"")),
                one, "refreshAccessToken", "refreshed access token must be header-safe");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(401, "{}"),
                        new MockVcfInstaller.Reply(200, "\"badĀheader\"")),
                one, "refreshAccessToken",
                "refreshed access token must be representable as an HTTP header value");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(401, "{}"),
                        new MockVcfInstaller.Reply(200, "\"   \"")),
                one, "refreshAccessToken", "refreshed access token must be nonblank");

        String richRefreshId = "quote\" slash/ backslash\\\b\f\n\r\t\u0001 μ";
        String empty = MockVcfInstaller.page(0, 0, 0, 0, List.of());
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(401, "{}"),
                new MockVcfInstaller.Reply(200,
                        "\"" + MockVcfInstaller.FRESH_ACCESS_TOKEN + "\""),
                new MockVcfInstaller.Reply(200, empty)))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, richRefreshId);
            client.listAllTasks(one);
            assertRefresh(mock.requests().get(1),
                    "\"quote\\\" slash/ backslash\\\\\\b\\f\\n\\r\\t\\u0001 μ\"");
        }
    }

    private static void testRefreshStateIsPerInvocationAndResultsAreFresh() throws Exception {
        VcfInstallerClient.TaskQuery one = query(
                null, null, null, null, null, null,
                null, null, null, null, 1);
        String empty = MockVcfInstaller.page(0, 0, 0, 0, List.of());
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(401, "{}"),
                new MockVcfInstaller.Reply(200,
                        "\"" + MockVcfInstaller.FRESH_ACCESS_TOKEN + "\""),
                new MockVcfInstaller.Reply(200, empty),
                new MockVcfInstaller.Reply(401, "{}"),
                new MockVcfInstaller.Reply(200,
                        "\"" + MockVcfInstaller.FRESH_ACCESS_TOKEN + "\""),
                new MockVcfInstaller.Reply(200, empty)))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            List<VcfInstallerClient.Task> first = client.listAllTasks(one);
            List<VcfInstallerClient.Task> second = client.listAllTasks(one);

            check(first != second, "each invocation returns a fresh list, including when empty");
            eq(2L, mock.requests().stream()
                    .filter(request -> request.method().equals("PATCH")).count(),
                    "refresh allowance resets for each invocation");
            assertGet(mock.requests().get(0), "/v1/tasks?pageSize=1", INITIAL_TOKEN);
            assertGet(mock.requests().get(2), "/v1/tasks?pageSize=1",
                    MockVcfInstaller.FRESH_ACCESS_TOKEN);
            assertGet(mock.requests().get(3), "/v1/tasks?pageSize=1",
                    MockVcfInstaller.FRESH_ACCESS_TOKEN);
            assertGet(mock.requests().get(5), "/v1/tasks?pageSize=1",
                    MockVcfInstaller.FRESH_ACCESS_TOKEN);
        }
    }

    private static void testStatusAndMediaTypeHandling() throws Exception {
        VcfInstallerClient.TaskQuery one = query(
                null, null, null, null, null, null,
                null, null, null, null, 1);
        String responseSecret = "response-body-secret";
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of(
                new MockVcfInstaller.Reply(403,
                        "{\"errorCode\":\"" + responseSecret + "\"}")))) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            try {
                client.listAllTasks(one);
                fail("expected non-401 getTasks VcfApiException");
            } catch (VcfInstallerClient.VcfApiException exception) {
                eq("getTasks", exception.operationId(), "getTasks failure operationId");
                eq(403, exception.statusCode(), "getTasks actual failure status");
                check(!exception.getMessage().contains(responseSecret),
                        "API exception message must not expose the response body");
            }
            eq(1, mock.requests().size(), "non-401 status must not trigger refresh");
        }

        assertProtocol(
                List.of(new MockVcfInstaller.Reply(
                        200, null, MockVcfInstaller.page(0, 0, 0, 0, List.of()))),
                one, "getTasks", "successful page must declare a JSON media type");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(401, "{}"),
                        new MockVcfInstaller.Reply(
                                200, "text/plain",
                                "\"" + MockVcfInstaller.FRESH_ACCESS_TOKEN + "\"")),
                one, "refreshAccessToken", "successful refresh must be JSON");
    }

    private static void testPaginationAndResponseProtocol() throws Exception {
        VcfInstallerClient.TaskQuery sizeTwo = query(
                null, null, null, null, null, null,
                null, null, null, null, 2);
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        "{\"pageMetadata\":{\"pageNumber\":0,\"pageSize\":0,"
                                + "\"totalElements\":0,\"totalPages\":0}}")),
                sizeTwo, "getTasks", "elements is required by the exercise protocol");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        "{\"elements\":[],\"pageMetadata\":{\"pageNumber\":false,"
                                + "\"pageSize\":0,\"totalElements\":0,\"totalPages\":0}}")),
                sizeTwo, "getTasks", "boolean is not an integer");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        MockVcfInstaller.page(1, 0, 0, 0, List.of()))),
                sizeTwo, "getTasks", "page number must match the request");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        MockVcfInstaller.page(0, 0, -1, 0, List.of()))),
                sizeTwo, "getTasks", "pagination metadata must not be negative");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        MockVcfInstaller.page(0, 1, 0, 0, List.of()))),
                sizeTwo, "getTasks", "metadata page size must equal element count");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        MockVcfInstaller.page(0, 0, 1, 1, List.of()))),
                sizeTwo, "getTasks", "empty elements cannot claim a nonempty total");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        MockVcfInstaller.page(0, 0, 0, 1, List.of()))),
                sizeTwo, "getTasks", "totalPages must agree with requested page size");

        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(200,
                                MockVcfInstaller.page(0, 2, 3, 2, List.of(
                                        MockVcfInstaller.task(
                                                "stable-a", "A", null, "Pending", "t0"),
                                        MockVcfInstaller.task(
                                                "stable-b", "B", null, "Pending", "t1")))),
                        new MockVcfInstaller.Reply(200,
                                MockVcfInstaller.page(1, 2, 4, 2, List.of(
                                        MockVcfInstaller.task(
                                                "changed-a", "C", null, "Pending", "t2"),
                                        MockVcfInstaller.task(
                                                "changed-b", "D", null, "Pending", "t3"))))),
                sizeTwo, "getTasks", "pagination totals must remain stable");

        String duplicate = MockVcfInstaller.task(
                "duplicate", "A", null, "Pending", "t0");
        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(200,
                                MockVcfInstaller.page(0, 2, 3, 2, List.of(
                                        duplicate,
                                        MockVcfInstaller.task(
                                                "other", "B", null, "Pending", "t1")))),
                        new MockVcfInstaller.Reply(200,
                                MockVcfInstaller.page(1, 1, 3, 2, List.of(duplicate)))),
                sizeTwo, "getTasks", "duplicate task IDs across pages");

        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        MockVcfInstaller.page(0, 1, 3, 2, List.of(
                                MockVcfInstaller.task(
                                        "short", "A", null, "Pending", "t0"))))),
                sizeTwo, "getTasks", "non-final page must be full");

        assertProtocol(
                List.of(
                        new MockVcfInstaller.Reply(200,
                                MockVcfInstaller.page(0, 2, 3, 2, List.of(
                                        MockVcfInstaller.task(
                                                "count-a", "A", null, "Pending", "t0"),
                                        MockVcfInstaller.task(
                                                "count-b", "B", null, "Pending", "t1")))),
                        new MockVcfInstaller.Reply(200,
                                MockVcfInstaller.page(1, 0, 3, 2, List.of()))),
                sizeTwo, "getTasks", "final count must equal totalElements");

        assertProtocol(
                List.of(new MockVcfInstaller.Reply(
                        200, "text/plain", MockVcfInstaller.page(0, 0, 0, 0, List.of()))),
                sizeTwo, "getTasks", "successful page must be JSON");
    }

    private static void testTaskProtocol() throws Exception {
        VcfInstallerClient.TaskQuery one = query(
                null, null, null, null, null, null,
                null, null, null, null, 1);
        for (String task : List.of(
                MockVcfInstaller.task("", "Name", null, "Pending", "t0"),
                MockVcfInstaller.task("id", " ", null, "Pending", "t0"),
                MockVcfInstaller.task("id", "Name", null, "", "t0"),
                MockVcfInstaller.task("id", "Name", null, "Pending", " "))) {
            assertProtocol(
                    List.of(new MockVcfInstaller.Reply(200,
                            MockVcfInstaller.page(0, 1, 1, 1, List.of(task)))),
                    one, "getTasks", "required Task text must be nonblank");
        }
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        "{\"elements\":[{\"id\":\"id\",\"name\":\"Name\","
                                + "\"type\":false,\"status\":\"Pending\","
                                + "\"creationTimestamp\":\"t0\"}],"
                                + "\"pageMetadata\":{\"pageNumber\":0,\"pageSize\":1,"
                                + "\"totalElements\":1,\"totalPages\":1}}")),
                one, "getTasks", "Task.type must be a string when present");
        assertProtocol(
                List.of(new MockVcfInstaller.Reply(200,
                        "{\"elements\":[null],\"pageMetadata\":{\"pageNumber\":0,"
                                + "\"pageSize\":1,\"totalElements\":1,"
                                + "\"totalPages\":1}}")),
                one, "getTasks", "each Task must be an object");
    }

    private static void testValidationBeforeWire() throws Exception {
        for (ThrowingRunnable invalid : List.<ThrowingRunnable>of(
                () -> new VcfInstallerClient(null, INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient(" ", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient("relative", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient("ftp://127.0.0.1", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient("http:///", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://user@127.0.0.1", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1/api", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1?query=true", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1#fragment", INITIAL_TOKEN, REFRESH_ID),
                () -> new VcfInstallerClient("http://127.0.0.1", " ", REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1", "bad\nheader", REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1", "bad\u007fheader", REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1", "badĀheader", REFRESH_ID),
                () -> new VcfInstallerClient(
                        "http://127.0.0.1", INITIAL_TOKEN, null),
                () -> new VcfInstallerClient("http://127.0.0.1", INITIAL_TOKEN, " "))) {
            expect(IllegalArgumentException.class, invalid, "constructor validation");
        }

        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            expect(NullPointerException.class,
                    () -> client.listAllTasks(null), "null query");
            expect(IllegalArgumentException.class,
                    () -> client.listAllTasks(query(
                            null, null, null, null, null, null,
                            null, null, null, null, 0)), "zero page size");
            expect(IllegalArgumentException.class,
                    () -> client.listAllTasks(query(
                            null, null, null, null, null, null,
                            null, null, null, null, 101)), "overlarge page size");
            eq(0, mock.requests().size(), "argument validation must happen before wire");
        }
    }

    private static void testMockServesOnlyFocusedOperations() throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(List.of())) {
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(URI.create(mock.baseUrl() + "/v1/system"))
                            .GET().build(),
                    HttpResponse.BodyHandlers.ofString());
            eq(404, response.statusCode(), "unlisted getSystem route must not be served");
        }
    }

    private static VcfInstallerClient.TaskQuery query(
            Integer limit,
            String taskStatus,
            String taskType,
            String resourceId,
            String resourceType,
            Long completedAfter,
            String orderDirection,
            String orderBy,
            String taskName,
            Boolean doLiveRefresh,
            int pageSize) {
        return new VcfInstallerClient.TaskQuery(
                limit, taskStatus, taskType, resourceId, resourceType, completedAfter,
                orderDirection, orderBy, taskName, doLiveRefresh, pageSize);
    }

    private static void assertProtocol(
            List<MockVcfInstaller.Reply> replies,
            VcfInstallerClient.TaskQuery query,
            String operationId,
            String label) throws Exception {
        try (MockVcfInstaller mock = new MockVcfInstaller(replies)) {
            VcfInstallerClient client =
                    new VcfInstallerClient(mock.baseUrl(), INITIAL_TOKEN, REFRESH_ID);
            try {
                client.listAllTasks(query);
                fail("expected ProtocolException: " + label);
            } catch (VcfInstallerClient.ProtocolException exception) {
                eq(operationId, exception.operationId(), label + " operationId");
                check(!exception.getMessage().contains(INITIAL_TOKEN),
                        label + " must not leak access token");
                check(!exception.getMessage().contains(REFRESH_ID),
                        label + " must not leak refresh token ID");
            }
        }
    }

    private static void assertGet(
            MockVcfInstaller.RecordedRequest request, String rawTarget, String token) {
        eq("GET", request.method(), "getTasks method");
        eq(rawTarget, request.rawTarget(), "getTasks raw target");
        eq(List.of("Bearer " + token), request.headerValues("Authorization"),
                "single current Authorization header");
        eq(List.of("application/json"), request.headerValues("Accept"),
                "single getTasks Accept header");
        eq(List.of(), request.headerValues("Content-Type"),
                "getTasks Content-Type omitted");
        eq(List.of(), request.headerValues("Transfer-Encoding"),
                "getTasks transfer encoding omitted");
        eq(0, request.body().length, "getTasks body empty");
        List<String> lengths = request.headerValues("Content-Length");
        check(lengths.isEmpty() || lengths.equals(List.of("0")),
                "getTasks must not have positive content length: " + lengths);
    }

    private static void assertRefresh(
            MockVcfInstaller.RecordedRequest request, String expectedBody) {
        byte[] expected = expectedBody.getBytes(StandardCharsets.UTF_8);
        eq("PATCH", request.method(), "refresh method");
        eq("/v1/tokens/access-token/refresh", request.rawTarget(), "refresh raw target");
        eq(null, request.rawQuery(), "refresh query omitted");
        eq(List.of(), request.headerValues("Authorization"),
                "expired Authorization header omitted from refresh");
        eq(List.of("application/json"), request.headerValues("Accept"),
                "single refresh Accept header");
        eq(List.of("application/json"), request.headerValues("Content-Type"),
                "single refresh Content-Type header");
        eq(List.of(Integer.toString(expected.length)), request.headerValues("Content-Length"),
                "refresh fixed content length");
        eq(List.of(), request.headerValues("Transfer-Encoding"),
                "refresh transfer encoding omitted");
        check(Arrays.equals(expected, request.body()), "exact refresh JSON-string body bytes");
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

    @FunctionalInterface
    private interface ThrowingRunnable {
        void run() throws Exception;
    }
}
