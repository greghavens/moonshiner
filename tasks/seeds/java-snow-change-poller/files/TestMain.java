import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.TimeZone;

/**
 * Acceptance harness: loopback fake ServiceNow instance (Table API subset for
 * change_request, contract pinned in docs/contract.json) plus checks for the
 * existing SnowTableClient behavior and the new ChangePoller feature.
 * Run with: java TestMain.java
 */
public class TestMain {

    static final String USERNAME = "poller.bot";
    static final String PASSWORD = "dummy-cred-91ab77"; // dummy; must never leak
    static final String EXPECTED_AUTH = "Basic " + Base64.getEncoder()
            .encodeToString((USERNAME + ":" + PASSWORD).getBytes(StandardCharsets.UTF_8));
    static final String T0 = "2026-05-01 07:00:00";
    static final String TABLE_PATH = "/api/now/table/change_request";
    static final int MAX_RETRIES = 3; // synced with docs/contract.json rate_limit.max_retries

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    // ---------------------------------------------------------------- fake

    static String sid(String tag) {
        StringBuilder sb = new StringBuilder(tag);
        while (sb.length() < 32) sb.append('0');
        return sb.toString();
    }

    static final String GROUP_NET = sid("g11");
    static final String GROUP_DB = sid("g22");

    static final class Fault {
        final int status;
        final int retryAfter;
        Fault(int status, int retryAfter) {
            this.status = status;
            this.retryAfter = retryAfter;
        }
    }

    static final class Recorded {
        final String method;
        final String path;
        final String rawQuery;
        final Map<String, String> params;
        final Map<String, String> headers;
        Recorded(String method, String path, String rawQuery,
                 Map<String, String> params, Map<String, String> headers) {
            this.method = method;
            this.path = path;
            this.rawQuery = rawQuery;
            this.params = params;
            this.headers = headers;
        }
    }

    static String jsonEscape(String s) {
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default: sb.append(c);
            }
        }
        return sb.toString();
    }

    static String envelope(String message, String detail) {
        return "{\"error\":{\"message\":\"" + jsonEscape(message)
                + "\",\"detail\":\"" + jsonEscape(detail)
                + "\"},\"status\":\"failure\"}";
    }

    static final class FakeInstance {
        final List<Map<String, String>> rows = new ArrayList<>();
        final Map<String, String> groupNames = new HashMap<>();
        final List<Recorded> requests = new ArrayList<>();
        final Map<Integer, Fault> faultAt = new HashMap<>(); // 1-based table-GET index
        Fault alwaysFault = null;
        int alwaysFaultFrom = 0;
        int tableGets = 0;
        HttpServer server;
        String baseUrl;

        void row(String sysId, String number, String shortDescription,
                 String assignmentGroup, String approval, String sysUpdatedOn) {
            Map<String, String> r = new LinkedHashMap<>();
            r.put("sys_id", sysId);
            r.put("number", number);
            r.put("short_description", shortDescription);
            r.put("assignment_group", assignmentGroup);
            r.put("approval", approval);
            r.put("sys_updated_on", sysUpdatedOn);
            rows.add(r);
        }

        void start() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void stop() {
            server.stop(0);
        }

        int getCount() {
            return tableGets;
        }

        void handle(HttpExchange ex) throws IOException {
            String rawQuery = ex.getRequestURI().getRawQuery();
            Map<String, String> params = new LinkedHashMap<>();
            if (rawQuery != null) {
                for (String pair : rawQuery.split("&")) {
                    int i = pair.indexOf('=');
                    if (i > 0) {
                        params.put(URLDecoder.decode(pair.substring(0, i), StandardCharsets.UTF_8),
                                URLDecoder.decode(pair.substring(i + 1), StandardCharsets.UTF_8));
                    }
                }
            }
            Map<String, String> headers = new LinkedHashMap<>();
            ex.getRequestHeaders().forEach((k, v) ->
                    headers.put(k.toLowerCase(Locale.ROOT), v.isEmpty() ? "" : v.get(0)));
            requests.add(new Recorded(ex.getRequestMethod(), ex.getRequestURI().getPath(),
                    rawQuery == null ? "" : rawQuery, params, headers));
            ex.getRequestBody().readAllBytes();

            if (!EXPECTED_AUTH.equals(headers.get("authorization"))) {
                send(ex, 401, envelope("User Not Authenticated",
                        "Required to provide Auth information"), null);
                return;
            }
            if (!TABLE_PATH.equals(ex.getRequestURI().getPath())
                    || !"GET".equals(ex.getRequestMethod())) {
                send(ex, 400, envelope("Unsupported operation",
                        ex.getRequestMethod() + " " + ex.getRequestURI().getPath()), null);
                return;
            }
            tableGets++;
            Fault fault = null;
            if (alwaysFault != null && tableGets >= alwaysFaultFrom) {
                fault = alwaysFault;
            } else if (faultAt.containsKey(tableGets)) {
                fault = faultAt.remove(tableGets);
            }
            if (fault != null) {
                Map<String, String> extra = new LinkedHashMap<>();
                if (fault.retryAfter > 0) {
                    extra.put("Retry-After", String.valueOf(fault.retryAfter));
                }
                send(ex, fault.status, envelope("Fault " + fault.status,
                        "injected fault " + fault.status), extra);
                return;
            }
            list(ex, params);
        }

        void list(HttpExchange ex, Map<String, String> params) throws IOException {
            String query = params.getOrDefault("sysparm_query", "");
            String orderField = "sys_id";
            List<String[]> conds = new ArrayList<>(); // {op, field, value}
            for (String term : query.isEmpty() ? new String[0] : query.split("\\^")) {
                if (term.startsWith("ORDERBY")) {
                    orderField = term.substring("ORDERBY".length());
                } else if (term.contains(">")) {
                    int i = term.indexOf('>');
                    conds.add(new String[]{">", term.substring(0, i), term.substring(i + 1)});
                } else if (term.contains("=")) {
                    int i = term.indexOf('=');
                    conds.add(new String[]{"=", term.substring(0, i), term.substring(i + 1)});
                } else {
                    send(ex, 400, envelope("Invalid query", "cannot evaluate term " + term), null);
                    return;
                }
            }
            List<Map<String, String>> matched = new ArrayList<>();
            for (Map<String, String> row : rows) {
                boolean ok = true;
                for (String[] c : conds) {
                    String got = row.getOrDefault(c[1], "");
                    if (c[0].equals("=") && !got.equals(c[2])) ok = false;
                    if (c[0].equals(">") && got.compareTo(c[2]) <= 0) ok = false;
                }
                if (ok) matched.add(row);
            }
            final String of = orderField;
            matched.sort((a, b) -> a.getOrDefault(of, "").compareTo(b.getOrDefault(of, "")));

            int total = matched.size();
            int offset = Integer.parseInt(params.getOrDefault("sysparm_offset", "0"));
            int limit = Integer.parseInt(params.getOrDefault("sysparm_limit", "10000"));
            List<Map<String, String>> page = matched.subList(
                    Math.min(offset, total), Math.min(offset + limit, total));

            boolean exclude = "true".equals(params.get("sysparm_exclude_reference_link"));
            String displayValue = params.getOrDefault("sysparm_display_value", "false");
            List<String> fields = params.containsKey("sysparm_fields")
                    ? List.of(params.get("sysparm_fields").split(","))
                    : new ArrayList<>(rows.isEmpty() ? List.of() : rows.get(0).keySet());

            StringBuilder body = new StringBuilder("{\"result\":[");
            for (int r = 0; r < page.size(); r++) {
                if (r > 0) body.append(',');
                body.append('{');
                for (int f = 0; f < fields.size(); f++) {
                    if (f > 0) body.append(',');
                    String field = fields.get(f);
                    String raw = page.get(r).getOrDefault(field, "");
                    body.append('"').append(jsonEscape(field)).append("\":");
                    if (field.equals("assignment_group") && !raw.isEmpty()) {
                        String shown = "true".equals(displayValue)
                                ? groupNames.getOrDefault(raw, raw) : raw;
                        if (exclude) {
                            body.append('"').append(jsonEscape(shown)).append('"');
                        } else {
                            body.append("{\"link\":\"").append(jsonEscape(
                                            baseUrl + "/api/now/table/sys_user_group/" + raw))
                                    .append("\",\"value\":\"").append(jsonEscape(shown)).append("\"}");
                        }
                    } else {
                        body.append('"').append(jsonEscape(raw)).append('"');
                    }
                }
                body.append('}');
            }
            body.append("]}");
            Map<String, String> extra = new LinkedHashMap<>();
            extra.put("X-Total-Count", String.valueOf(total));
            extra.put("Link", "<" + baseUrl + TABLE_PATH + "?sysparm_offset=0>;rel=\"first\"");
            send(ex, 200, body.toString(), extra);
        }

        void send(HttpExchange ex, int status, String body, Map<String, String> extra)
                throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            if (extra != null) {
                extra.forEach((k, v) -> ex.getResponseHeaders().set(k, v));
            }
            ex.sendResponseHeaders(status, bytes.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
        }
    }

    static FakeInstance seeded() {
        FakeInstance inst = new FakeInstance();
        inst.groupNames.put(GROUP_NET, "Network Ops");
        inst.groupNames.put(GROUP_DB, "Database Ops");
        // approved, updated after the checkpoint — deliberately seeded out of order
        inst.row(sid("c07"), "CHG0000107", "Rotate edge TLS certs", GROUP_NET, "approved", "2026-05-01 07:35:00");
        inst.row(sid("c01"), "CHG0000101", "Patch db-3 kernel", GROUP_DB, "approved", "2026-05-01 07:05:00");
        inst.row(sid("c05"), "CHG0000105", "Resize payments ASG", GROUP_NET, "approved", "2026-05-01 07:25:00");
        inst.row(sid("c02"), "CHG0000102", "Failover test eu-1", GROUP_NET, "approved", "2026-05-01 07:10:00");
        inst.row(sid("c06"), "CHG0000106", "Upgrade rabbitmq cluster", GROUP_DB, "approved", "2026-05-01 07:30:00");
        inst.row(sid("c03"), "CHG0000103", "Rotate API gateway keys", GROUP_DB, "approved", "2026-05-01 07:15:00");
        inst.row(sid("c04"), "CHG0000104", "Enable slow query log", GROUP_DB, "approved", "2026-05-01 07:20:00");
        // approved but updated exactly AT the checkpoint: already seen, must not repeat
        inst.row(sid("b01"), "CHG0000095", "Archive old runbooks", GROUP_NET, "approved", T0);
        // approved but before the checkpoint
        inst.row(sid("a01"), "CHG0000090", "Swap dead disk in r12", GROUP_NET, "approved", "2026-05-01 06:30:00");
        // updated after the checkpoint but not approved
        inst.row(sid("n01"), "CHG0000110", "Reboot core switch", GROUP_NET, "requested", "2026-05-01 07:12:00");
        inst.row(sid("n02"), "CHG0000111", "Drop legacy index", GROUP_DB, "rejected", "2026-05-01 07:22:00");
        return inst;
    }

    static SnowTableClient client(FakeInstance inst) {
        return new SnowTableClient(inst.baseUrl, USERNAME, PASSWORD);
    }

    static final String EXPECTED_QUERY =
            "approval=approved^sys_updated_on>" + T0 + "^ORDERBYsys_updated_on";
    static final String EXPECTED_FIELDS =
            "sys_id,number,short_description,assignment_group,approval,sys_updated_on";

    static List<String> numbers(List<ChangeRecord> records) {
        List<String> out = new ArrayList<>();
        for (ChangeRecord r : records) out.add(r.number);
        return out;
    }

    // ---------------------------------------------------------------- tests

    static void testExistingClientSinglePage() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            SnowTableClient.Page page = client(inst).fetchPage(
                    "change_request", "approval=approved^ORDERBYsys_updated_on",
                    List.of("sys_id", "number", "assignment_group", "sys_updated_on"),
                    5, 0, false, false);
            checkEq(page.records.size(), 5, "fetchPage honors sysparm_limit");
            checkEq(page.totalCount, 9, "totalCount comes from X-Total-Count");
            Map<String, Object> first = page.records.get(0);
            checkEq(first.get("number"), "CHG0000090", "results ordered by sys_updated_on");
            Object group = first.get("assignment_group");
            check(group instanceof Map,
                    "without sysparm_exclude_reference_link a reference field is a {link,value} object");
            Map<?, ?> gm = (Map<?, ?>) group;
            checkEq(gm.get("value"), GROUP_NET, "reference object value carries the sys_id");
            check(String.valueOf(gm.get("link"))
                            .startsWith(inst.baseUrl + "/api/now/table/sys_user_group/"),
                    "reference object link points at the referenced row's Table API URL");
            Recorded r = inst.requests.get(0);
            checkEq(r.path, TABLE_PATH, "collection path");
            checkEq(r.headers.get("accept"), "application/json", "Accept header on every request");
            checkEq(r.headers.get("authorization"), EXPECTED_AUTH, "Basic credentials on every request");
            check(r.rawQuery.contains("%5EORDERBY"),
                    "the caret in sysparm_query must be percent-encoded as %5E on the wire");
        } finally {
            inst.stop();
        }
    }

    static void testExistingClientErrorEnvelope() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            inst.faultAt.put(1, new Fault(400, 0));
            SnowApiException thrown = null;
            try {
                client(inst).fetchPage("change_request", "approval=approved",
                        null, 5, 0, false, true);
            } catch (SnowApiException e) {
                thrown = e;
            }
            check(thrown != null, "a 400 error envelope must raise SnowApiException");
            checkEq(thrown.statusCode, 400, "statusCode from the response");
            checkEq(thrown.error, "Fault 400", "error message from the envelope");
            checkEq(thrown.detail, "injected fault 400", "detail from the envelope");
            check(thrown.getMessage().contains("Fault 400"),
                    "getMessage surfaces ServiceNow's message");
            check(!thrown.getMessage().contains(PASSWORD)
                            && !String.valueOf(thrown).contains(PASSWORD),
                    "credentials must never appear in exception text");
        } finally {
            inst.stop();
        }
    }

    static void testFullScanAdvancesCheckpoint() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            List<Integer> sleeps = new ArrayList<>();
            ChangePoller poller = new ChangePoller(client(inst), 3, sleeps::add);
            PollResult res = poller.poll(T0);

            checkEq(res.complete, true, "an uninterrupted scan reports complete");
            checkEq(res.failure, null, "no failure on a clean scan");
            checkEq(res.records.size(), 7, "all approved changes after the checkpoint");
            checkEq(numbers(res.records), List.of("CHG0000101", "CHG0000102", "CHG0000103",
                            "CHG0000104", "CHG0000105", "CHG0000106", "CHG0000107"),
                    "records in sys_updated_on order across pages");
            checkEq(res.checkpoint, "2026-05-01 07:35:00",
                    "checkpoint advances to the max sys_updated_on seen");
            check(!numbers(res.records).contains("CHG0000095"),
                    "a record updated exactly AT the checkpoint must not repeat (strict >)");
            check(!numbers(res.records).contains("CHG0000110")
                            && !numbers(res.records).contains("CHG0000111"),
                    "unapproved changes are excluded server-side by the query");
            checkEq(res.records.get(0).assignmentGroup, GROUP_DB,
                    "with exclude_reference_link=true assignment_group is the plain sys_id");
            check(sleeps.isEmpty(), "no rate-limit sleeps on a clean scan");

            checkEq(inst.getCount(), 3, "7 records at page size 3 is exactly 3 GETs");
            List<String> offsets = new ArrayList<>();
            for (Recorded r : inst.requests) {
                offsets.add(r.params.get("sysparm_offset"));
                checkEq(r.params.get("sysparm_query"), EXPECTED_QUERY,
                        "poller query: approved + strictly-after checkpoint + ORDERBY");
                checkEq(r.params.get("sysparm_display_value"), "false",
                        "poller must request raw values");
                checkEq(r.params.get("sysparm_exclude_reference_link"), "true",
                        "poller must exclude reference links");
                checkEq(r.params.get("sysparm_fields"), EXPECTED_FIELDS,
                        "poller must project exactly the pinned field list");
                checkEq(r.params.get("sysparm_limit"), "3", "limit equals the page size");
                check(r.rawQuery.contains("%5E"), "encoded caret on the wire");
                check(r.rawQuery.contains("sys_updated_on%3E"),
                        "the '>' comparator must be percent-encoded as %3E");
            }
            checkEq(offsets, List.of("0", "3", "6"), "offset advances by the page size");

            PollResult idle = poller.poll(res.checkpoint);
            checkEq(idle.records.size(), 0, "nothing new after the advanced checkpoint");
            checkEq(idle.complete, true, "an empty scan is still complete");
            checkEq(idle.checkpoint, res.checkpoint,
                    "an empty scan keeps the checkpoint unchanged");
            checkEq(inst.getCount(), 4, "an empty scan is a single GET");
        } finally {
            inst.stop();
        }
    }

    static void testForbiddenKeepsPartialResultsAndCheckpoint() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            inst.faultAt.put(2, new Fault(403, 0));
            List<Integer> sleeps = new ArrayList<>();
            ChangePoller poller = new ChangePoller(client(inst), 3, sleeps::add);
            PollResult res = poller.poll(T0);
            checkEq(res.complete, false, "a 403 mid-scan means the scan is incomplete");
            checkEq(res.failure, "FORBIDDEN", "403 maps to failure=FORBIDDEN");
            checkEq(numbers(res.records), List.of("CHG0000101", "CHG0000102", "CHG0000103"),
                    "records fetched before the 403 must be preserved");
            checkEq(res.checkpoint, T0, "checkpoint must NOT advance on an incomplete scan");
            check(sleeps.isEmpty(), "403 is not retried");
            checkEq(inst.getCount(), 2, "the scan stops at the 403");
        } finally {
            inst.stop();
        }
    }

    static void testUnauthorizedFirstPage() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            inst.faultAt.put(1, new Fault(401, 0));
            List<Integer> sleeps = new ArrayList<>();
            ChangePoller poller = new ChangePoller(client(inst), 3, sleeps::add);
            PollResult res = poller.poll(T0);
            checkEq(res.failure, "UNAUTHORIZED", "401 maps to failure=UNAUTHORIZED");
            checkEq(res.complete, false, "401 means incomplete");
            checkEq(res.records.size(), 0, "nothing was fetched");
            checkEq(res.checkpoint, T0, "checkpoint unchanged");
            check(sleeps.isEmpty(), "401 is not retried");
        } finally {
            inst.stop();
        }
    }

    static void testRateLimitRetryMidScan() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            inst.faultAt.put(2, new Fault(429, 5));
            List<Integer> sleeps = new ArrayList<>();
            ChangePoller poller = new ChangePoller(client(inst), 3, sleeps::add);
            PollResult res = poller.poll(T0);
            checkEq(res.complete, true, "a single 429 must not abort the scan");
            checkEq(res.records.size(), 7, "the retried page completes the scan");
            checkEq(res.checkpoint, "2026-05-01 07:35:00", "checkpoint advances after recovery");
            checkEq(sleeps, List.of(5), "sleeper called once with the Retry-After seconds");
            checkEq(inst.getCount(), 4, "one extra GET for the retried page");
        } finally {
            inst.stop();
        }
    }

    static void testRateLimitExhaustionKeepsPriorResults() throws Exception {
        FakeInstance inst = seeded();
        inst.start();
        try {
            inst.alwaysFault = new Fault(429, 2);
            inst.alwaysFaultFrom = 2; // page 1 succeeds, everything after is throttled
            List<Integer> sleeps = new ArrayList<>();
            ChangePoller poller = new ChangePoller(client(inst), 3, sleeps::add);
            PollResult res = poller.poll(T0);
            checkEq(res.complete, false, "exhausted retries mean an incomplete scan");
            checkEq(res.failure, "RATE_LIMITED", "exhausted 429 maps to failure=RATE_LIMITED");
            checkEq(numbers(res.records), List.of("CHG0000101", "CHG0000102", "CHG0000103"),
                    "records fetched before throttling must be preserved");
            checkEq(res.checkpoint, T0, "checkpoint must NOT advance");
            checkEq(sleeps, List.of(2, 2, 2),
                    "sleeper called max_retries times with the Retry-After seconds");
            checkEq(inst.getCount(), 1 + MAX_RETRIES + 1,
                    "page one, the throttled attempt, and its retries");
        } finally {
            inst.stop();
        }
    }

    @SuppressWarnings("unchecked")
    static void testProtectedDocsFixtures() throws Exception {
        Map<String, Object> contract = (Map<String, Object>) Json.parse(
                Files.readString(Path.of("docs", "contract.json")));
        Map<String, Object> sources = (Map<String, Object>) Json.parse(
                Files.readString(Path.of("docs", "official_sources.json")));
        Map<String, Object> research = (Map<String, Object>) sources.get("research");
        checkEq(research.get("required"), Boolean.TRUE, "research provenance is mandatory");
        List<Object> officialSources = (List<Object>) research.get("official_sources");
        check(officialSources.size() >= 2, "at least two official sources required");
        for (Object o : officialSources) {
            Map<String, Object> src = (Map<String, Object>) o;
            String url = String.valueOf(src.get("url"));
            check(url.startsWith("https://"), "official source must be https: " + url);
            check(url.contains("servicenow.com"), "official source must be first-party: " + url);
            check(!String.valueOf(src.get("used_for")).isEmpty(), "used_for must be recorded");
        }
        check(((List<Object>) sources.get("verified_facts")).size() >= 4,
                "verified facts must be summarized");
        Map<String, Object> rateLimit = (Map<String, Object>) contract.get("rate_limit");
        checkEq(((Double) rateLimit.get("max_retries")).intValue(), MAX_RETRIES,
                "harness retry budget synced with the contract fixture");
        checkEq(rateLimit.get("retry_after_header"), "Retry-After", "contract pins Retry-After");
        Map<String, Object> params = (Map<String, Object>) contract.get("params");
        check(String.valueOf(params.get("sysparm_exclude_reference_link")).startsWith("true"),
                "contract pins exclude_reference_link=true");
    }

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        testProtectedDocsFixtures();
        System.out.println("ok  testProtectedDocsFixtures");
        testExistingClientSinglePage();
        System.out.println("ok  testExistingClientSinglePage");
        testExistingClientErrorEnvelope();
        System.out.println("ok  testExistingClientErrorEnvelope");
        testFullScanAdvancesCheckpoint();
        System.out.println("ok  testFullScanAdvancesCheckpoint");
        testForbiddenKeepsPartialResultsAndCheckpoint();
        System.out.println("ok  testForbiddenKeepsPartialResultsAndCheckpoint");
        testUnauthorizedFirstPage();
        System.out.println("ok  testUnauthorizedFirstPage");
        testRateLimitRetryMidScan();
        System.out.println("ok  testRateLimitRetryMidScan");
        testRateLimitExhaustionKeepsPriorResults();
        System.out.println("ok  testRateLimitExhaustionKeepsPriorResults");
        System.out.println("PASS  " + checks + " checks");
    }
}
