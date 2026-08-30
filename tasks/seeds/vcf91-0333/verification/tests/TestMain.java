import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Protected verification harness for the VCF Automation catalog exercise.
 *
 * <p>It builds a catalog dataset and a bearer token at runtime, starts {@link MockVcfAutomation} on
 * an ephemeral loopback port with its route table pinned to docs/contract.json, drives
 * {@link VcfCatalogClient}, and then reads the mock's flushed JSONL request log to assert the exact
 * wire shape of every request the client made.
 *
 * <p>No live VMware endpoint is contacted.
 *
 * <p>Protected file. Do not edit.
 */
public final class TestMain {

    private static final List<String> FAILURES = new ArrayList<>();
    private static int checks;

    private static final Path CONTRACT = Path.of("docs", "contract.json");
    private static final Path LOG_DIR = Path.of("build", "requestlogs");

    private static final String TOKEN = freshToken();

    private static final String[] PROJECTS = {
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
            "44444444-4444-4444-8444-444444444444",
    };

    /**
     * Catalog item names. Duplicates are deliberate: sort=name,asc leaves the relative order of
     * equal names unspecified, so a correct client must break ties locally by id. The pair
     * "Apache-Web" / "apache-web" is deliberate too: a case-insensitive sort orders them
     * differently from the required case-sensitive one.
     */
    private static final String[] NAMES = {
            "Ubuntu 22.04 Server", "Windows Server 2022", "Apache-Web",
            "Redis Cache", "Ubuntu 22.04 Server", "PostgreSQL 15",
            "apache-web", "Windows Server 2022", "CentOS Stream 9",
            "Ubuntu 22.04 Server", "Nginx Gateway", "Windows Server 2022",
            "PostgreSQL 15", "Kafka Broker", "CentOS Stream 9",
            "Redis Cache", "Elasticsearch Node", "Ubuntu 24.04 Server",
            "Nginx Gateway", "MySQL 8", "Kafka Broker",
            "Zookeeper Ensemble", "Elasticsearch Node", "MySQL 8",
            "Grafana Dashboard", "Prometheus Server", "Ubuntu 24.04 Server",
            "Vault Cluster", "Consul Agent", "Grafana Dashboard",
            "RabbitMQ Broker", "Prometheus Server", "MinIO Object Store",
            "Consul Agent", "Harbor Registry", "RabbitMQ Broker",
            "Jenkins Controller", "Harbor Registry", "GitLab Runner",
            "Jenkins Controller", "MinIO Object Store", "Vault Cluster",
            "GitLab Runner", "Zookeeper Ensemble", "Bastion Host",
            "Bastion Host", "Load Balancer",
    };

    /**
     * A fixed permutation used to assign ids, so that id order is unrelated to name order and
     * unrelated to the order the mock pages elements out.
     */
    private static final int[] ID_PERMUTATION = {
            29, 3, 41, 17, 8, 44, 12, 35, 1, 26,
            46, 20, 5, 38, 14, 31, 9, 23, 43, 0,
            36, 18, 6, 45, 11, 28, 2, 40, 15, 33,
            7, 24, 42, 19, 4, 37, 13, 30, 10, 27,
            21, 39, 16, 34, 25, 32, 22,
    };

    public static void main(String[] args) throws Exception {
        if (!Files.isRegularFile(CONTRACT)) {
            System.err.println("FATAL: " + CONTRACT + " is missing; run the verifier from the task root.");
            System.exit(2);
        }
        Files.createDirectories(LOG_DIR);

        List<Map<String, Object>> items = buildItems();
        List<Map<String, Object>> serverOrder = serverOrder(items);

        run("full walk", () -> scenarioFullWalk(serverOrder, items));
        run("single page", () -> scenarioSinglePage(serverOrder, items));
        run("search filter", () -> scenarioSearchFilter(serverOrder, items));
        run("UTF-8 query encoding", () -> scenarioUtf8QueryEncoding(serverOrder));
        run("projects filter", () -> scenarioProjectsFilter(serverOrder, items));
        run("empty result", () -> scenarioEmptyResult(serverOrder));
        run("route pinning", () -> scenarioRoutePinning(serverOrder));
        run("page size validation", () -> scenarioInvalidPageSize(serverOrder));
        run("construction validation", TestMain::scenarioInvalidConstruction);
        run("unauthorized", () -> scenarioUnauthorized(serverOrder));
        run("failure on last page", () -> scenarioFailureOnLastPage(serverOrder));
        run("wrong page number", () -> scenarioWrongPageNumber(serverOrder));
        run("malformed envelope", () -> scenarioResponseNotAnObject(serverOrder));
        run("pagination validation", () -> scenarioMalformedPagination(serverOrder));
        run("catalog item validation", () -> scenarioMalformedItems(serverOrder));

        System.out.println();
        if (FAILURES.isEmpty()) {
            System.out.println("PASS - " + checks + " checks");
            System.exit(0);
        }
        System.out.println("FAIL - " + FAILURES.size() + " of " + checks + " checks failed");
        for (String failure : FAILURES) {
            System.out.println("  * " + failure);
        }
        System.exit(1);
    }

    // ---------------------------------------------------------------- scenario driver

    @FunctionalInterface
    private interface Scenario {
        void run() throws Exception;
    }

    /**
     * Runs one scenario in isolation. An unexpected escape - typically a client that is not
     * implemented yet, or one that throws where it should have returned - is recorded as a failure
     * so that every remaining scenario still runs and the report stays a checklist.
     */
    private static void run(String name, Scenario scenario) {
        try {
            scenario.run();
        } catch (Throwable t) {
            checks++;
            String message = t.getMessage();
            FAILURES.add("scenario '" + name + "' aborted with " + t.getClass().getName()
                    + (message == null ? "" : ": " + message));
        }
    }

    // ---------------------------------------------------------------- scenarios

    private static void scenarioFullWalk(List<Map<String, Object>> serverOrder,
                                         List<Map<String, Object>> items) throws Exception {
        String name = "full walk, page size 20";
        int pageSize = 20;
        try (MockVcfAutomation mock = mock("full-walk", serverOrder, MockVcfAutomation.Mode.NORMAL)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl() + "/", TOKEN);
            List<String> actual = client.listCatalogItems(pageSize, "   ", List.of());

            eq(name + ": complete stable collection", expectedLines(items), actual);

            List<Map<String, Object>> log = readLog(mock.logPath());
            int expectedPages = pages(items.size(), pageSize);
            eq(name + ": request count", expectedPages, log.size());
            for (int page = 0; page < Math.min(expectedPages, log.size()); page++) {
                Map<String, Object> entry = log.get(page);
                eq(name + ": raw target of request " + (page + 1),
                        target(page, pageSize, null, List.of()), str(entry.get("target")));
                assertWireShape(name + ", request " + (page + 1), entry, 200);
                assertParamsAbsent(name + ", request " + (page + 1), entry,
                        List.of("search", "projects"));
            }
        }
    }

    private static void scenarioSinglePage(List<Map<String, Object>> serverOrder,
                                           List<Map<String, Object>> items) throws Exception {
        String name = "single page, page size 100";
        int pageSize = 100;
        try (MockVcfAutomation mock = mock("single-page", serverOrder, MockVcfAutomation.Mode.NORMAL)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            List<String> actual = client.listCatalogItems(pageSize, null, null);

            eq(name + ": complete stable collection", expectedLines(items), actual);

            List<Map<String, Object>> log = readLog(mock.logPath());
            eq(name + ": one request only, no speculative extra page", 1, log.size());
            if (!log.isEmpty()) {
                eq(name + ": raw target", target(0, pageSize, null, List.of()), str(log.get(0).get("target")));
                assertWireShape(name, log.get(0), 200);
            }
        }
    }

    private static void scenarioSearchFilter(List<Map<String, Object>> serverOrder,
                                             List<Map<String, Object>> items) throws Exception {
        String name = "search filter";
        int pageSize = 10;
        String search = "db&web";
        List<Map<String, Object>> matching = items.stream()
                .filter(item -> containsSearch(item, search))
                .toList();

        gt(name + ": fixture spans several pages", matching.size(), pageSize);

        try (MockVcfAutomation mock = mock("search", serverOrder, MockVcfAutomation.Mode.NORMAL)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            List<String> actual = client.listCatalogItems(pageSize, search, null);

            eq(name + ": filtered stable collection", expectedLines(matching), actual);

            List<Map<String, Object>> log = readLog(mock.logPath());
            int expectedPages = pages(matching.size(), pageSize);
            eq(name + ": request count", expectedPages, log.size());
            for (int page = 0; page < Math.min(expectedPages, log.size()); page++) {
                Map<String, Object> entry = log.get(page);
                eq(name + ": raw target of request " + (page + 1) + " (ampersand percent-encoded, "
                                + "filter repeated on every page)",
                        target(page, pageSize, search, List.of()), str(entry.get("target")));
                assertWireShape(name + ", request " + (page + 1), entry, 200);
                assertParamsAbsent(name + ", request " + (page + 1), entry, List.of("projects"));
                eq(name + ": decoded search value of request " + (page + 1),
                        List.of(search), paramValues(entry, "search"));
            }
        }
    }

    private static void scenarioProjectsFilter(List<Map<String, Object>> serverOrder,
                                               List<Map<String, Object>> items) throws Exception {
        String name = "projects filter";
        int pageSize = 5;
        List<String> projects = List.of(PROJECTS[0], PROJECTS[2]);
        List<Map<String, Object>> matching = items.stream()
                .filter(item -> containsAnyProject(item, projects))
                .toList();

        gt(name + ": fixture spans several pages", matching.size(), pageSize * 2);

        try (MockVcfAutomation mock = mock("projects", serverOrder, MockVcfAutomation.Mode.NORMAL)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            List<String> actual = client.listCatalogItems(pageSize, null, projects);

            eq(name + ": filtered stable collection", expectedLines(matching), actual);

            List<Map<String, Object>> log = readLog(mock.logPath());
            int expectedPages = pages(matching.size(), pageSize);
            eq(name + ": request count", expectedPages, log.size());
            for (int page = 0; page < Math.min(expectedPages, log.size()); page++) {
                Map<String, Object> entry = log.get(page);
                eq(name + ": raw target of request " + (page + 1) + " (repeated projects parameter "
                                + "in caller order)",
                        target(page, pageSize, null, projects), str(entry.get("target")));
                assertWireShape(name + ", request " + (page + 1), entry, 200);
                assertParamsAbsent(name + ", request " + (page + 1), entry, List.of("search"));
                eq(name + ": decoded projects values of request " + (page + 1),
                        projects, paramValues(entry, "projects"));
            }
        }
    }

    private static void scenarioUtf8QueryEncoding(List<Map<String, Object>> serverOrder)
            throws Exception {
        String name = "UTF-8 and reserved-character search encoding";
        String search = "☃+/%";
        try (MockVcfAutomation mock = mock("utf8-encoding", serverOrder,
                MockVcfAutomation.Mode.NORMAL)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            eq(name + ": unmatched search returns an empty collection", List.<String>of(),
                    client.listCatalogItems(20, search, null));

            List<Map<String, Object>> log = readLog(mock.logPath());
            eq(name + ": one request", 1, log.size());
            if (!log.isEmpty()) {
                eq(name + ": exact UTF-8 raw target", target(0, 20, search, List.of()),
                        str(log.get(0).get("target")));
                eq(name + ": decoded search round-trips", List.of(search),
                        paramValues(log.get(0), "search"));
                assertWireShape(name, log.get(0), 200);
            }
        }
    }

    private static void scenarioEmptyResult(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "search matching nothing";
        String search = "zzz-no-such-catalog-item";
        try (MockVcfAutomation mock = mock("empty", serverOrder, MockVcfAutomation.Mode.NORMAL)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            List<String> actual = client.listCatalogItems(20, search, null);

            eq(name + ": empty collection", List.<String>of(), actual);

            List<Map<String, Object>> log = readLog(mock.logPath());
            eq(name + ": one request only", 1, log.size());
            if (!log.isEmpty()) {
                eq(name + ": raw target", target(0, 20, search, List.of()), str(log.get(0).get("target")));
                assertWireShape(name, log.get(0), 200);
            }
        }
    }

    /** Proves the mock is pinned to the contract rather than serving whatever it is asked for. */
    private static void scenarioRoutePinning(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "contract route pinning";
        try (MockVcfAutomation mock = mock("route-pinning", serverOrder, MockVcfAutomation.Mode.NORMAL)) {
            HttpClient probe = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();

            eq(name + ": excluded admin route is refused", 404,
                    probeStatus(probe, mock.baseUrl() + "/catalog/api/admin/items?page=0&size=20"));
            eq(name + ": excluded per-item route is refused", 404,
                    probeStatus(probe, mock.baseUrl()
                            + "/catalog/api/items/11111111-1111-4111-8111-111111111111"));
            eq(name + ": unknown route is refused", 404,
                    probeStatus(probe, mock.baseUrl() + "/deployment/api/deployments?page=0&size=20"));

            HttpRequest post = HttpRequest.newBuilder(URI.create(mock.baseUrl() + "/catalog/api/items"))
                    .header("Authorization", "Bearer " + TOKEN)
                    .POST(HttpRequest.BodyPublishers.ofString("{}"))
                    .build();
            eq(name + ": undocumented method on the contract path is refused", 405,
                    probe.send(post, HttpResponse.BodyHandlers.ofString()).statusCode());

            HttpRequest defaulted = HttpRequest.newBuilder(
                            URI.create(mock.baseUrl() + "/catalog/api/items"))
                    .header("Authorization", "Bearer " + TOKEN)
                    .GET()
                    .build();
            eq(name + ": the contract path requires explicit page and size", 400,
                    probe.send(defaulted, HttpResponse.BodyHandlers.ofString()).statusCode());
        }
    }

    private static void scenarioInvalidPageSize(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "page size validation";
        for (int pageSize : new int[]{0, -1, Integer.MIN_VALUE}) {
            try (MockVcfAutomation mock = mock("page-size-" + pageSize, serverOrder,
                    MockVcfAutomation.Mode.NORMAL)) {
                VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
                Throwable thrown = capture(() -> client.listCatalogItems(pageSize, null, null));
                assertVcfException(name + ": page size " + pageSize + " is rejected", thrown);
                eq(name + ": page size " + pageSize + " sends no request",
                        0, readLog(mock.logPath()).size());
            }
        }
    }

    private static void scenarioInvalidConstruction() {
        String name = "construction validation";
        assertVcfException(name + ": null base url", capture(() -> new VcfCatalogClient(null, TOKEN)));
        assertVcfException(name + ": blank base url", capture(() -> new VcfCatalogClient("   ", TOKEN)));
        assertVcfException(name + ": non-absolute base url",
                capture(() -> new VcfCatalogClient("relative-host", TOKEN)));
        assertVcfException(name + ": null token",
                capture(() -> new VcfCatalogClient("http://127.0.0.1:1", null)));
        assertVcfException(name + ": blank token",
                capture(() -> new VcfCatalogClient("http://127.0.0.1:1", "  ")));
        assertVcfException(name + ": null timeout",
                capture(() -> new VcfCatalogClient("http://127.0.0.1:1", TOKEN, null)));
        assertVcfException(name + ": non-positive timeout",
                capture(() -> new VcfCatalogClient("http://127.0.0.1:1", TOKEN, Duration.ZERO)));
        assertVcfException(name + ": negative timeout",
                capture(() -> new VcfCatalogClient(
                        "http://127.0.0.1:1", TOKEN, Duration.ofNanos(-1))));
    }

    private static void scenarioUnauthorized(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "unauthorized";
        try (MockVcfAutomation mock = mock("unauthorized", serverOrder,
                MockVcfAutomation.Mode.ALWAYS_UNAUTHORIZED)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            Throwable thrown = capture(() -> client.listCatalogItems(20, null, null));
            assertVcfException(name + ": 401 is surfaced as a failure", thrown);
            yes(name + ": the access token never appears in the failure", !mentionsToken(thrown));

            List<Map<String, Object>> log = readLog(mock.logPath());
            eq(name + ": the client stops after the rejected request", 1, log.size());
            if (!log.isEmpty()) {
                assertWireShape(name, log.get(0), 401);
            }
        }
    }

    private static void scenarioFailureOnLastPage(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "server failure part-way through the walk";
        try (MockVcfAutomation mock = mock("fail-last-page", serverOrder,
                MockVcfAutomation.Mode.FAIL_ON_LAST_PAGE)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            Throwable thrown = capture(() -> client.listCatalogItems(20, null, null));
            assertVcfException(name + ": an incomplete walk throws rather than returning a partial "
                    + "collection", thrown);
        }
    }

    private static void scenarioWrongPageNumber(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "page number mismatch";
        try (MockVcfAutomation mock = mock("wrong-page-number", serverOrder,
                MockVcfAutomation.Mode.WRONG_PAGE_NUMBER)) {
            VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
            Throwable thrown = capture(() -> client.listCatalogItems(20, null, null));
            assertVcfException(name + ": a response echoing the wrong page number is rejected", thrown);

            lt(name + ": the client does not loop indefinitely", readLog(mock.logPath()).size(), 25);
        }
    }

    private static void scenarioResponseNotAnObject(List<Map<String, Object>> serverOrder) throws Exception {
        String name = "malformed response envelope";
        Map<String, MockVcfAutomation.Mode> cases = new LinkedHashMap<>();
        cases.put("invalid JSON", MockVcfAutomation.Mode.RESPONSE_INVALID_JSON);
        cases.put("bare array instead of PageCatalogItem",
                MockVcfAutomation.Mode.RESPONSE_NOT_AN_OBJECT);
        for (Map.Entry<String, MockVcfAutomation.Mode> testCase : cases.entrySet()) {
            try (MockVcfAutomation mock = mock("malformed-envelope-" + testCase.getValue(),
                    serverOrder, testCase.getValue())) {
                VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
                Throwable thrown = capture(() -> client.listCatalogItems(20, null, null));
                assertVcfException(name + ": " + testCase.getKey() + " is rejected", thrown);
            }
        }
    }

    private static void scenarioMalformedPagination(List<Map<String, Object>> serverOrder)
            throws Exception {
        Map<String, MockVcfAutomation.Mode> cases = new LinkedHashMap<>();
        cases.put("wrong-size", MockVcfAutomation.Mode.WRONG_PAGE_SIZE);
        cases.put("changed-totals", MockVcfAutomation.Mode.CHANGED_TOTALS);
        cases.put("changed-total-pages", MockVcfAutomation.Mode.CHANGED_TOTAL_PAGES);
        cases.put("inconsistent-total-pages", MockVcfAutomation.Mode.INCONSISTENT_TOTAL_PAGES);
        cases.put("overfull-page", MockVcfAutomation.Mode.OVERFULL_PAGE);
        cases.put("empty-middle-page", MockVcfAutomation.Mode.EMPTY_MIDDLE_PAGE);
        cases.put("missing-content", MockVcfAutomation.Mode.MISSING_CONTENT);
        cases.put("missing-number", MockVcfAutomation.Mode.MISSING_NUMBER);
        cases.put("out-of-range-number", MockVcfAutomation.Mode.OUT_OF_RANGE_NUMBER);
        cases.put("missing-size", MockVcfAutomation.Mode.MISSING_SIZE);
        cases.put("non-integral-size", MockVcfAutomation.Mode.NON_INTEGRAL_SIZE);
        cases.put("out-of-range-size", MockVcfAutomation.Mode.OUT_OF_RANGE_SIZE);
        cases.put("missing-total-elements", MockVcfAutomation.Mode.MISSING_TOTAL_ELEMENTS);
        cases.put("out-of-range-total-elements",
                MockVcfAutomation.Mode.OUT_OF_RANGE_TOTAL_ELEMENTS);
        cases.put("missing-total-pages", MockVcfAutomation.Mode.MISSING_TOTAL_PAGES);
        cases.put("non-integral-total-pages", MockVcfAutomation.Mode.NON_INTEGRAL_TOTAL_PAGES);
        cases.put("out-of-range-total-pages", MockVcfAutomation.Mode.OUT_OF_RANGE_TOTAL_PAGES);

        for (Map.Entry<String, MockVcfAutomation.Mode> testCase : cases.entrySet()) {
            String label = testCase.getKey();
            try (MockVcfAutomation mock = mock("pagination-" + label, serverOrder,
                    testCase.getValue())) {
                VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
                Throwable thrown = capture(() -> client.listCatalogItems(5, null, null));
                assertVcfException("pagination validation: " + label + " is rejected", thrown);

                int requests = readLog(mock.logPath()).size();
                int expectedMaximum = testCase.getValue() == MockVcfAutomation.Mode.CHANGED_TOTALS
                        || testCase.getValue() == MockVcfAutomation.Mode.CHANGED_TOTAL_PAGES
                        || testCase.getValue() == MockVcfAutomation.Mode.EMPTY_MIDDLE_PAGE ? 2 : 1;
                yes("pagination validation: " + label + " fails at the first malformed page",
                        requests <= expectedMaximum);
            }
        }
    }

    private static void scenarioMalformedItems(List<Map<String, Object>> serverOrder)
            throws Exception {
        Map<String, MockVcfAutomation.Mode> cases = new LinkedHashMap<>();
        cases.put("non-object-element", MockVcfAutomation.Mode.NON_OBJECT_ITEM);
        cases.put("blank-id", MockVcfAutomation.Mode.BLANK_ITEM_ID);
        cases.put("blank-name", MockVcfAutomation.Mode.BLANK_ITEM_NAME);
        cases.put("duplicate-id", MockVcfAutomation.Mode.DUPLICATE_ITEM_ID);

        for (Map.Entry<String, MockVcfAutomation.Mode> testCase : cases.entrySet()) {
            String label = testCase.getKey();
            try (MockVcfAutomation mock = mock("item-" + label, serverOrder,
                    testCase.getValue())) {
                VcfCatalogClient client = new VcfCatalogClient(mock.baseUrl(), TOKEN);
                Throwable thrown = capture(() -> client.listCatalogItems(20, null, null));
                assertVcfException("catalog item validation: " + label + " is rejected", thrown);
                eq("catalog item validation: " + label + " stops on the malformed first page",
                        1, readLog(mock.logPath()).size());
            }
        }
    }

    // ---------------------------------------------------------------- wire assertions

    private static void assertWireShape(String context, Map<String, Object> entry, int expectedStatus) {
        eq(context + ": method", "GET", str(entry.get("method")));
        eq(context + ": path", "/catalog/api/items", str(entry.get("path")));
        eq(context + ": empty request body", 0, intOf(entry.get("bodyBytes")));
        eq(context + ": response status", expectedStatus, intOf(entry.get("status")));

        eq(context + ": exactly one Authorization: Bearer header",
                List.of("Bearer " + TOKEN), headerValues(entry, "authorization"));
        eq(context + ": exactly one Accept: application/json header",
                List.of("application/json"), headerValues(entry, "accept"));
        eq(context + ": no Content-Type on a bodyless GET",
                List.<String>of(), headerValues(entry, "content-type"));
        eq(context + ": no Content-Length on a bodyless GET",
                List.<String>of(), headerValues(entry, "content-length"));

        assertParamsAbsent(context, entry, neverSentParameters());
    }

    /** Asserts that each named parameter is absent entirely, not merely blank. */
    private static void assertParamsAbsent(String context, Map<String, Object> entry, List<String> names) {
        for (String name : names) {
            List<String> values = paramValues(entry, name);
            eq(context + ": unset optional parameter '" + name
                    + "' is omitted entirely rather than sent empty", List.<String>of(), values);
        }
    }

    /** The parameters the contract itself declares this client never sends. */
    private static List<String> neverSentParameters() {
        List<String> never = new ArrayList<>();
        Object parsed;
        try {
            parsed = Json.parse(Files.readString(CONTRACT, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + CONTRACT, e);
        }
        if (parsed instanceof Map<?, ?> contract && contract.get("operations") instanceof List<?> ops) {
            for (Object op : ops) {
                if (!(op instanceof Map<?, ?> operation)) {
                    continue;
                }
                if (!(operation.get("query_parameters") instanceof List<?> params)) {
                    continue;
                }
                for (Object p : params) {
                    if (p instanceof Map<?, ?> param
                            && param.get("contract_usage") instanceof String usage
                            && usage.startsWith("never sent")
                            && param.get("name") instanceof String pname) {
                        never.add(pname);
                    }
                }
            }
        }
        return never;
    }

    // ---------------------------------------------------------------- fixtures

    private static List<Map<String, Object>> buildItems() {
        List<Map<String, Object>> items = new ArrayList<>();
        for (int i = 0; i < NAMES.length; i++) {
            int key = ID_PERMUTATION[i];
            Map<String, Object> item = new LinkedHashMap<>();
            String id = String.format("%08x-4a1c-4d2e-9f30-%012x", key, key);
            // The contract requires case-sensitive uniqueness. These are distinct ids even though
            // they differ only in case, so a client must not normalize before duplicate detection.
            if (i == 0) {
                id = "aaaaaaaa-4a1c-4d2e-9f30-aaaaaaaaaaaa";
            } else if (i == 1) {
                id = "AAAAAAAA-4A1C-4D2E-9F30-AAAAAAAAAAAA";
            }
            item.put("id", id);
            item.put("name", NAMES[i]);

            List<Object> projectIds = new ArrayList<>();
            projectIds.add(PROJECTS[i % PROJECTS.length]);
            if (i % 3 == 0) {
                projectIds.add(PROJECTS[(i + 2) % PROJECTS.length]);
            }
            item.put("projectIds", projectIds);

            Map<String, Object> type = new LinkedHashMap<>();
            // Every ResourceReference member is optional, so one item in five carries no id and
            // the client must project it as "-" rather than failing or emitting "null".
            if (i % 10 == 4) {
                type.put("id", "   ");
            } else if (i % 5 != 4) {
                type.put("id", i % 2 == 0 ? "com.vmw.vro.workflow" : "com.vmw.blueprint");
            }
            type.put("name", i % 2 == 0 ? "vRO Workflow" : "Cloud Template");
            item.put("type", type);

            item.put("description", i % 2 == 0
                    ? "Composite tier db&web for automated provisioning"
                    : "Standalone tier for automated provisioning");
            item.put("sourceName", "Content Source " + (i % 4));
            item.put("createdAt", String.format("2026-0%d-1%dT0%d:00:00.000Z",
                    (i % 8) + 1, i % 10, i % 10));
            items.add(item);
        }
        return items;
    }

    /**
     * The order the service pages elements out for sort=name,asc. Names ascend, but equal names are
     * emitted in descending id order, which is exactly the instability the client must correct.
     */
    private static List<Map<String, Object>> serverOrder(List<Map<String, Object>> items) {
        List<Map<String, Object>> ordered = new ArrayList<>(items);
        ordered.sort(Comparator
                .comparing((Map<String, Object> item) -> str(item.get("name")))
                .thenComparing((Map<String, Object> item) -> str(item.get("id")),
                        Comparator.reverseOrder()));
        return ordered;
    }

    /** The required client output: name then id, ascending and case-sensitive. */
    private static List<String> expectedLines(List<Map<String, Object>> items) {
        List<Map<String, Object>> ordered = new ArrayList<>(items);
        ordered.sort(Comparator
                .comparing((Map<String, Object> item) -> str(item.get("name")))
                .thenComparing((Map<String, Object> item) -> str(item.get("id"))));
        List<String> lines = new ArrayList<>();
        for (Map<String, Object> item : ordered) {
            String typeId = "-";
            if (item.get("type") instanceof Map<?, ?> type
                    && type.get("id") instanceof String id && !id.isBlank()) {
                typeId = id;
            }
            lines.add(str(item.get("name")) + "\t" + str(item.get("id")) + "\t" + typeId);
        }
        return lines;
    }

    private static boolean containsSearch(Map<String, Object> item, String search) {
        String needle = search.toLowerCase(Locale.ROOT);
        return str(item.get("name")).toLowerCase(Locale.ROOT).contains(needle)
                || str(item.get("description")).toLowerCase(Locale.ROOT).contains(needle);
    }

    private static boolean containsAnyProject(Map<String, Object> item, List<String> projects) {
        if (!(item.get("projectIds") instanceof List<?> owned)) {
            return false;
        }
        for (Object candidate : owned) {
            if (candidate instanceof String s && projects.contains(s)) {
                return true;
            }
        }
        return false;
    }

    private static MockVcfAutomation mock(String label,
                                          List<Map<String, Object>> serverOrder,
                                          MockVcfAutomation.Mode mode) throws IOException {
        return new MockVcfAutomation(CONTRACT, LOG_DIR.resolve(label + ".jsonl"),
                TOKEN, serverOrder, mode);
    }

    private static String target(int page, int size, String search, List<String> projects) {
        StringBuilder query = new StringBuilder();
        query.append("page=").append(page);
        query.append("&size=").append(size);
        query.append("&sort=name%2Casc");
        if (search != null && !search.isBlank()) {
            query.append("&search=").append(percentEncode(search));
        }
        for (String project : projects) {
            query.append("&projects=").append(percentEncode(project));
        }
        return "/catalog/api/items?" + query;
    }

    private static String percentEncode(String value) {
        StringBuilder out = new StringBuilder();
        for (byte b : value.getBytes(StandardCharsets.UTF_8)) {
            int c = b & 0xff;
            boolean unreserved = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                    || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '*';
            if (unreserved) {
                out.append((char) c);
            } else if (c == ' ') {
                out.append('+');
            } else {
                out.append('%').append(String.format("%02X", c));
            }
        }
        return out.toString();
    }

    private static int pages(int total, int pageSize) {
        return total == 0 ? 1 : (total + pageSize - 1) / pageSize;
    }

    private static String freshToken() {
        // Runtime-only: never committed, so a hardcoded expectation cannot satisfy the harness.
        return "vcfa-" + Long.toHexString(System.nanoTime()) + "-"
                + Integer.toHexString(new Object().hashCode());
    }

    // ---------------------------------------------------------------- log reading

    private static List<Map<String, Object>> readLog(Path path) throws IOException {
        List<Map<String, Object>> entries = new ArrayList<>();
        if (!Files.isRegularFile(path)) {
            return entries;
        }
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (line.isBlank()) {
                continue;
            }
            if (Json.parse(line) instanceof Map<?, ?> map) {
                Map<String, Object> entry = new LinkedHashMap<>();
                map.forEach((k, v) -> entry.put(String.valueOf(k), v));
                entries.add(entry);
            }
        }
        return entries;
    }

    private static List<String> headerValues(Map<String, Object> entry, String lowercaseName) {
        List<String> values = new ArrayList<>();
        if (entry.get("headers") instanceof Map<?, ?> headers
                && headers.get(lowercaseName) instanceof List<?> found) {
            for (Object value : found) {
                values.add(String.valueOf(value));
            }
        }
        return values;
    }

    private static List<String> paramValues(Map<String, Object> entry, String name) {
        List<String> values = new ArrayList<>();
        if (entry.get("params") instanceof List<?> params) {
            for (Object p : params) {
                if (p instanceof Map<?, ?> param && name.equals(param.get("name"))) {
                    values.add(String.valueOf(param.get("value")));
                }
            }
        }
        return values;
    }

    private static int probeStatus(HttpClient probe, String url) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create(url))
                .header("Authorization", "Bearer " + TOKEN)
                .GET()
                .build();
        return probe.send(request, HttpResponse.BodyHandlers.ofString()).statusCode();
    }

    // ---------------------------------------------------------------- assertions

    private static Throwable capture(Runnable action) {
        try {
            action.run();
            return null;
        } catch (Throwable t) {
            return t;
        }
    }

    private static void assertVcfException(String description, Throwable thrown) {
        checks++;
        if (thrown == null) {
            FAILURES.add(description + " -- nothing was thrown");
        } else if (!(thrown instanceof VcfCatalogClient.VcfAutomationException)) {
            FAILURES.add(description + " -- expected VcfCatalogClient.VcfAutomationException, got "
                    + thrown.getClass().getName() + ": " + thrown.getMessage());
        }
    }

    private static boolean mentionsToken(Throwable thrown) {
        for (Throwable t = thrown; t != null; t = t.getCause()) {
            String message = t.getMessage();
            if (message != null && message.contains(TOKEN)) {
                return true;
            }
            if (t.getCause() == t) {
                break;
            }
        }
        return false;
    }

    private static void eq(String description, Object expected, Object actual) {
        checks++;
        if (!java.util.Objects.equals(expected, actual)) {
            FAILURES.add(description + "\n      expected: " + render(expected)
                    + "\n      actual:   " + render(actual));
        }
    }

    private static void yes(String description, boolean condition) {
        checks++;
        if (!condition) {
            FAILURES.add(description);
        }
    }

    private static void gt(String description, int value, int floor) {
        checks++;
        if (value <= floor) {
            FAILURES.add(description + " -- expected more than " + floor + ", got " + value);
        }
    }

    private static void lt(String description, int value, int ceiling) {
        checks++;
        if (value >= ceiling) {
            FAILURES.add(description + " -- expected fewer than " + ceiling + ", got " + value);
        }
    }

    private static String render(Object value) {
        if (value instanceof List<?> list) {
            if (list.size() > 6) {
                StringBuilder out = new StringBuilder("(" + list.size() + " entries) [");
                for (int i = 0; i < 3; i++) {
                    out.append(escape(String.valueOf(list.get(i)))).append(", ");
                }
                out.append("... , ").append(escape(String.valueOf(list.get(list.size() - 1))));
                return out.append("]").toString();
            }
        }
        return escape(String.valueOf(value));
    }

    private static String escape(String value) {
        return value.replace("\t", "\\t").replace("\n", "\\n");
    }

    private static String str(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static int intOf(Object value) {
        if (value instanceof Number n) {
            return n.intValue();
        }
        return Integer.MIN_VALUE;
    }
}
