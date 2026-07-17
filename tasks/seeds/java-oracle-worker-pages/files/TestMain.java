import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance harness: loopback fake Oracle Fusion Cloud HCM tenant serving
 * the workers REST contract pinned in docs/contract.json (11.13.18.05 base
 * path, items/count/hasMore/limit/offset envelope, effectiveDate as-of rows,
 * expanded child envelopes, application/vnd.oracle.adf.error+json errors).
 * No real tenant, no real credentials, no sleeps.
 * Run with: java TestMain.java
 * Protected — do not modify this file or anything under docs/.
 */
public class TestMain {

    static final String BASE_PATH = "/hcmRestApi/resources/11.13.18.05";
    static final String USER = "HCM_INT_SVC";
    static final String PASS = "dummy-hcm-secret-77";
    static final String AUTH = "Basic " + java.util.Base64.getEncoder()
            .encodeToString((USER + ":" + PASS).getBytes(StandardCharsets.UTF_8));

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    // --------------------------------------------------------------- fixture

    static final String[][] ROSTER_JAN = {
            {"300000000000101", "E-0101", "Ada Verne"},
            {"300000000000102", "E-0102", "Bruno Silva"},
            {"300000000000103", "E-0103", "Pat Chen"},
            {"300000000000104", "E-0104", "Divya Rao"},
            {"300000000000105", "E-0105", "Egon Marsh"},
            {"300000000000106", "E-0106", "Fen Wu"},
            {"300000000000107", "E-0107", "Gita Iyer"},
            {"300000000000108", "E-0108", "Hugo Ortiz"},
            {"300000000000109", "E-0109", "Ines Farah"},
            {"300000000000110", "E-0110", "Jonas Beck"},
    };
    // As of 2026-07-01: E-0105's work relationship has ended (row no longer
    // effective) and E-0103's date-effective name row changed.
    static final String[][] ROSTER_JUL = {
            {"300000000000101", "E-0101", "Ada Verne"},
            {"300000000000102", "E-0102", "Bruno Silva"},
            {"300000000000103", "E-0103", "Pat Chen-Okafor"},
            {"300000000000104", "E-0104", "Divya Rao"},
            {"300000000000106", "E-0106", "Fen Wu"},
            {"300000000000107", "E-0107", "Gita Iyer"},
            {"300000000000108", "E-0108", "Hugo Ortiz"},
            {"300000000000109", "E-0109", "Ines Farah"},
            {"300000000000110", "E-0110", "Jonas Beck"},
    };

    static final int SERVER_PAGE_CLAMP = 4;   // tenant clamps any requested limit
    static final long ESTIMATED_TOTAL = 57;   // deliberately wrong estimate

    static final String ROSTER_QS_PREFIX =
            "onlyData=true&fields=PersonId,PersonNumber,DisplayName&effectiveDate=";
    static final String ROSTER_QS_SUFFIX =
            "&orderBy=PersonNumber:asc&totalResults=true&limit=100&offset=";
    static final String EXPAND_QS =
            "onlyData=true&expand=workRelationships&finder=PrimaryKey;PersonId=300000000000123";

    static final String ERROR_BODY = """
            {
              "title": "Bad Request",
              "status": "400",
              "o:errorDetails": [
                {
                  "detail": "The effective date 1900-01-01 is earlier than the earliest permitted effective start date.",
                  "o:errorCode": "PER-1531234",
                  "o:errorPath": "/effectiveDate"
                },
                {
                  "detail": "The worker collection cannot be built for the requested date range.",
                  "o:errorCode": "27008",
                  "o:errorPath": ""
                }
              ]
            }
            """;

    // ------------------------------------------------------------------ mock

    record Seen(String query, String auth, String frameworkVersion, String accept) {}

    static final List<Seen> LOG = new ArrayList<>();

    static String workerJson(String[] w) {
        return "{\"PersonId\":" + w[0] + ",\"PersonNumber\":\"" + w[1]
                + "\",\"DisplayName\":\"" + w[2] + "\"}";
    }

    static String pageJson(String[][] roster, int offset) {
        StringBuilder items = new StringBuilder();
        int count = 0;
        for (int i = offset; i < roster.length && count < SERVER_PAGE_CLAMP; i++, count++) {
            if (count > 0) items.append(',');
            items.append(workerJson(roster[i]));
        }
        boolean hasMore = offset + count < roster.length;
        return "{\"items\":[" + items + "],\"count\":" + count
                + ",\"hasMore\":" + hasMore + ",\"limit\":" + SERVER_PAGE_CLAMP
                + ",\"offset\":" + offset + ",\"totalResults\":" + ESTIMATED_TOTAL + "}";
    }

    static final String EXPANDED_WORKER = """
            {"items":[{"PersonId":300000000000123,"PersonNumber":"E-0123","DisplayName":"Rosa Lind",\
            "workRelationships":{"items":[\
            {"PeriodOfServiceId":300100200300123,"LegalEmployerName":"Vertex Global Services","WorkerType":"E","PrimaryFlag":true,"StartDate":"2019-04-01"},\
            {"PeriodOfServiceId":300100200300124,"LegalEmployerName":"Vertex Field Ops","WorkerType":"C","PrimaryFlag":false,"StartDate":"2016-02-15"}],\
            "count":2,"hasMore":false,"limit":25,"offset":0}}],\
            "count":1,"hasMore":false,"limit":25,"offset":0}""";

    static void respond(HttpExchange ex, int status, String contentType, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", contentType);
        ex.getResponseHeaders().set("REST-Framework-Version", "4");
        ex.sendResponseHeaders(status, b.length);
        try (OutputStream os = ex.getResponseBody()) { os.write(b); }
    }

    static void handleWorkers(HttpExchange ex) throws IOException {
        String q = ex.getRequestURI().getRawQuery();
        LOG.add(new Seen(q,
                ex.getRequestHeaders().getFirst("Authorization"),
                ex.getRequestHeaders().getFirst("REST-Framework-Version"),
                ex.getRequestHeaders().getFirst("Accept")));
        if (!"GET".equals(ex.getRequestMethod())) {
            respond(ex, 405, "application/vnd.oracle.adf.error+json",
                    "{\"title\":\"Method Not Allowed\",\"status\":\"405\",\"o:errorDetails\":[]}");
            return;
        }
        if (q != null && q.contains("effectiveDate=1900-01-01")) {
            respond(ex, 400, "application/vnd.oracle.adf.error+json", ERROR_BODY);
            return;
        }
        if (EXPAND_QS.equals(q)) {
            respond(ex, 200, "application/json", EXPANDED_WORKER);
            return;
        }
        for (String date : new String[]{"2026-01-01", "2026-07-01"}) {
            String[][] roster = date.equals("2026-01-01") ? ROSTER_JAN : ROSTER_JUL;
            for (int offset = 0; offset <= 12; offset += SERVER_PAGE_CLAMP) {
                if ((ROSTER_QS_PREFIX + date + ROSTER_QS_SUFFIX + offset).equals(q)) {
                    respond(ex, 200, "application/json", pageJson(roster, offset));
                    return;
                }
            }
        }
        respond(ex, 404, "application/vnd.oracle.adf.error+json",
                "{\"title\":\"Not Found\",\"status\":\"404\",\"o:errorDetails\":[{\"detail\":\"Unpinned request: "
                        + (q == null ? "" : q.replace('"', '\'')) + "\",\"o:errorCode\":\"TEST-0000\",\"o:errorPath\":\"\"}]}");
    }

    static void checkWindowHeaders(List<Seen> window, String label) {
        for (Seen s : window) {
            checkEq(s.auth(), AUTH, label + ": Authorization header on every request");
            checkEq(s.frameworkVersion(), "4", label + ": REST-Framework-Version header on every request");
            checkEq(s.accept(), "application/json", label + ": Accept header on every request");
        }
    }

    // ------------------------------------------------------------------ main

    public static void main(String[] args) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext(BASE_PATH + "/workers", TestMain::handleWorkers);
        server.start();
        String base = "http://127.0.0.1:" + server.getAddress().getPort() + BASE_PATH;
        try {
            run(base);
        } finally {
            server.stop(0);
        }
        System.out.println("OK — " + checks + " checks passed");
    }

    static void run(String base) throws Exception {

        // ---------------------------------------------- A. query serialization
        checkEq(WorkersQuery.workers()
                        .onlyData(true)
                        .fields("PersonId", "PersonNumber", "DisplayName")
                        .effectiveDate("2026-01-01")
                        .orderBy("PersonNumber:asc")
                        .totalResults(true)
                        .limit(100)
                        .offset(0)
                        .toQueryString(),
                ROSTER_QS_PREFIX + "2026-01-01" + ROSTER_QS_SUFFIX + "0",
                "A1 roster query string");
        checkEq(WorkersQuery.workers()
                        .finder("findReports", "PersonId=101", "DirectReportsFlag=true")
                        .limit(25)
                        .offset(50)
                        .toQueryString(),
                "finder=findReports;PersonId=101,DirectReportsFlag=true&limit=25&offset=50",
                "A2 multi-variable finder: semicolon after name, comma between variables");
        checkEq(WorkersQuery.workers()
                        .finder("findReports", "AssignmentName=Senior Analyst")
                        .toQueryString(),
                "finder=findReports;AssignmentName=Senior%20Analyst",
                "A3 finder variable values are percent-encoded");
        checkEq(WorkersQuery.workers()
                        .onlyData(true)
                        .expand("workRelationships")
                        .finder("PrimaryKey", "PersonId=300000000000123")
                        .toQueryString(),
                EXPAND_QS,
                "A4 expand+finder query string");
        checkEq(WorkersQuery.workers().toQueryString(), "",
                "A5 empty builder yields empty query string");
        checkEq(WorkersQuery.workers().totalResults(false).onlyData(false).toQueryString(), "",
                "A6 false booleans are omitted, not serialized");
        boolean threw = false;
        try {
            WorkersQuery.workers().fields("PersonId").expand("workRelationships");
        } catch (IllegalStateException e) { threw = true; }
        check(threw, "A7 fields then expand must throw IllegalStateException (documented: cannot be combined)");
        threw = false;
        try {
            WorkersQuery.workers().expand("workRelationships").fields("PersonId");
        } catch (IllegalStateException e) { threw = true; }
        check(threw, "A8 expand then fields must throw IllegalStateException (documented: cannot be combined)");

        HcmWorkersClient client = new HcmWorkersClient(base, USER, PASS);

        // ------------------------------------------- B. paged as-of-Jan roster
        LOG.clear();
        RosterExport.Result jan = RosterExport.effectiveRoster(client, "2026-01-01", 100);
        checkEq(LOG.size(), 3, "B1 exactly three page requests (no probe after hasMore=false)");
        checkEq(LOG.get(0).query(), ROSTER_QS_PREFIX + "2026-01-01" + ROSTER_QS_SUFFIX + "0",
                "B2 first page request");
        checkEq(LOG.get(1).query(), ROSTER_QS_PREFIX + "2026-01-01" + ROSTER_QS_SUFFIX + "4",
                "B3 second page offset advances by the SERVER-returned count, not the requested limit");
        checkEq(LOG.get(2).query(), ROSTER_QS_PREFIX + "2026-01-01" + ROSTER_QS_SUFFIX + "8",
                "B4 third page offset");
        checkWindowHeaders(LOG, "B5");
        checkEq(jan.lines().size(), 10, "B6 ten workers exported despite totalResults estimate of 57");
        checkEq(jan.lines().get(0), "E-0101|Ada Verne", "B7 first roster line");
        checkEq(jan.lines().get(2), "E-0103|Pat Chen", "B8 as-of-January name row");
        checkEq(jan.lines().get(4), "E-0105|Egon Marsh", "B9 January roster includes E-0105");
        checkEq(jan.lines().get(9), "E-0110|Jonas Beck", "B10 last roster line");
        checkEq(jan.estimatedTotalResults(), Long.valueOf(ESTIMATED_TOTAL),
                "B11 estimated totalResults is surfaced verbatim");
        checkEq(jan.pagesFetched(), 3, "B12 pages fetched");

        // ------------------------------------------- C. paged as-of-Jul roster
        LOG.clear();
        RosterExport.Result jul = RosterExport.effectiveRoster(client, "2026-07-01", 100);
        checkEq(LOG.size(), 3, "C1 exactly three page requests for the July roster");
        checkEq(LOG.get(0).query(), ROSTER_QS_PREFIX + "2026-07-01" + ROSTER_QS_SUFFIX + "0",
                "C2 effectiveDate parameter carries the as-of date");
        checkEq(LOG.get(2).query(), ROSTER_QS_PREFIX + "2026-07-01" + ROSTER_QS_SUFFIX + "8",
                "C3 short final page offset");
        checkWindowHeaders(LOG, "C4");
        checkEq(jul.lines().size(), 9, "C5 July roster has nine effective workers");
        checkEq(jul.lines().get(2), "E-0103|Pat Chen-Okafor",
                "C6 date-effective name row as of July");
        check(!jul.lines().toString().contains("E-0105"),
                "C7 worker whose row is no longer effective in July is absent");
        checkEq(jul.pagesFetched(), 3, "C8 pages fetched for July");

        // ------------------------------------------- D. expanded child fetch
        LOG.clear();
        WorkersPage page = client.fetchPage(WorkersQuery.workers()
                .onlyData(true)
                .expand("workRelationships")
                .finder("PrimaryKey", "PersonId=300000000000123"));
        checkEq(LOG.size(), 1, "D1 one request for the expanded fetch");
        checkEq(LOG.get(0).query(), EXPAND_QS, "D2 expanded fetch query string");
        checkWindowHeaders(LOG, "D3");
        checkEq(page.count(), 1, "D4 page count");
        checkEq(page.hasMore(), false, "D5 page hasMore");
        checkEq(page.limit(), 25, "D6 server-returned limit");
        checkEq(page.offset(), 0L, "D7 server-returned offset");
        WorkerRecord rosa = page.items().get(0);
        checkEq(rosa.str("DisplayName"), "Rosa Lind", "D8 worker attribute");
        checkEq(rosa.num("PersonId"), 300000000000123L, "D9 numeric attribute");
        List<WorkerRecord> rels = rosa.inline("workRelationships");
        checkEq(rels.size(), 2, "D10 expanded child envelope is unwrapped to its items");
        checkEq(rels.get(0).str("LegalEmployerName"), "Vertex Global Services",
                "D11 first relationship legal employer");
        checkEq(rels.get(0).str("WorkerType"), "E", "D12 first relationship worker type");
        checkEq(rels.get(1).str("LegalEmployerName"), "Vertex Field Ops",
                "D13 second relationship legal employer");
        checkEq(rels.get(1).num("PeriodOfServiceId"), 300100200300124L,
                "D14 second relationship period of service id");

        // ------------------------------------------- E. structured Oracle error
        LOG.clear();
        OracleRestException oops = null;
        try {
            RosterExport.effectiveRoster(client, "1900-01-01", 100);
        } catch (OracleRestException e) {
            oops = e;
        }
        check(oops != null, "E1 non-2xx must raise OracleRestException");
        checkEq(LOG.size(), 1, "E2 failing scan stops after the failing page");
        checkEq(oops.httpStatus(), 400, "E3 http status");
        checkEq(oops.title(), "Bad Request", "E4 error document title");
        checkEq(oops.errorStatus(), "400", "E5 error document status is the string form");
        checkEq(oops.details().size(), 2, "E6 every o:errorDetails entry is preserved");
        checkEq(oops.details().get(0).errorCode(), "PER-1531234", "E7 first detail code");
        checkEq(oops.details().get(0).errorPath(), "/effectiveDate", "E8 first detail path");
        check(oops.details().get(0).detail().contains("1900-01-01"),
                "E9 first detail message preserved");
        checkEq(oops.details().get(1).errorCode(), "27008", "E10 second detail code");
        checkEq(oops.details().get(1).errorPath(), "", "E11 second detail path");
        check(oops.details().get(1).detail().contains("cannot be built"),
                "E12 second detail message preserved");
        check(oops.getMessage().contains("Bad Request"),
                "E13 exception message carries the document title");
        check(oops.getMessage().contains("PER-1531234"),
                "E14 exception message carries the first application error code");
        String full = oops.getMessage() + "|" + String.valueOf(oops.toString());
        check(!full.contains(PASS), "E15 password never appears in exception text");
        check(!full.contains(AUTH.substring("Basic ".length())),
                "E16 basic credential material never appears in exception text");

        // ------------------------------------------- F. client is reusable
        LOG.clear();
        RosterExport.Result again = RosterExport.effectiveRoster(client, "2026-01-01", 100);
        checkEq(again.lines(), jan.lines(), "F1 client stays usable after an error");
        checkEq(LOG.size(), 3, "F2 rerun pages again from offset 0");
    }
}
