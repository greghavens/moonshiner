import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Acceptance harness: loopback fake of the Salesforce GraphQL endpoint
 * (POST /services/data/v67.0/graphql), enforcing the contract pinned in
 * docs/contract.json. No vendor network, no real credentials.
 * Run with: java TestMain.java
 */
public class TestMain {

    static final String TOKEN = "00Dxx-dummy-graphql-token-5ac2f9"; // dummy; must never leak
    static final String VERSION = "v67.0";
    static final String GRAPHQL_PATH = "/services/data/" + VERSION + "/graphql";
    static final int PAGE_SIZE = 2;

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    // ------------------------------------------------------------ mini json
    // Self-contained JSON support so the protected harness never depends on
    // code the candidate can edit.

    static final class MiniJson {
        private final String s;
        private int i;

        private MiniJson(String s) { this.s = s; }

        static Object parse(String text) {
            MiniJson p = new MiniJson(text);
            Object v = p.value();
            p.ws();
            if (p.i != p.s.length()) throw new IllegalArgumentException("trailing json at " + p.i);
            return v;
        }

        private void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        private Object value() {
            ws();
            char c = s.charAt(i);
            if (c == '{') return object();
            if (c == '[') return array();
            if (c == '"') return string();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            return number();
        }

        private Map<String, Object> object() {
            Map<String, Object> m = new LinkedHashMap<>();
            i++; ws();
            if (s.charAt(i) == '}') { i++; return m; }
            while (true) {
                ws();
                String k = string();
                ws();
                if (s.charAt(i) != ':') throw new IllegalArgumentException("expected : at " + i);
                i++;
                m.put(k, value());
                ws();
                char c = s.charAt(i++);
                if (c == '}') return m;
                if (c != ',') throw new IllegalArgumentException("expected , or } at " + (i - 1));
            }
        }

        private List<Object> array() {
            List<Object> l = new ArrayList<>();
            i++; ws();
            if (s.charAt(i) == ']') { i++; return l; }
            while (true) {
                l.add(value());
                ws();
                char c = s.charAt(i++);
                if (c == ']') return l;
                if (c != ',') throw new IllegalArgumentException("expected , or ] at " + (i - 1));
            }
        }

        private String string() {
            if (s.charAt(i) != '"') throw new IllegalArgumentException("expected string at " + i);
            i++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') return sb.toString();
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u':
                            sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                            i += 4;
                            break;
                        default: throw new IllegalArgumentException("bad escape \\" + e);
                    }
                } else {
                    sb.append(c);
                }
            }
        }

        private Object number() {
            int start = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            return Double.parseDouble(s.substring(start, i));
        }

        static String write(Object v) {
            StringBuilder sb = new StringBuilder();
            writeTo(v, sb);
            return sb.toString();
        }

        private static void writeTo(Object v, StringBuilder sb) {
            if (v == null) { sb.append("null"); return; }
            if (v instanceof String str) {
                sb.append('"');
                for (char c : str.toCharArray()) {
                    switch (c) {
                        case '"': sb.append("\\\""); break;
                        case '\\': sb.append("\\\\"); break;
                        case '\n': sb.append("\\n"); break;
                        case '\r': sb.append("\\r"); break;
                        case '\t': sb.append("\\t"); break;
                        default:
                            if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                            else sb.append(c);
                    }
                }
                sb.append('"');
                return;
            }
            if (v instanceof Map<?, ?> m) {
                sb.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> e : m.entrySet()) {
                    if (!first) sb.append(',');
                    first = false;
                    writeTo(String.valueOf(e.getKey()), sb);
                    sb.append(':');
                    writeTo(e.getValue(), sb);
                }
                sb.append('}');
                return;
            }
            if (v instanceof List<?> l) {
                sb.append('[');
                for (int k = 0; k < l.size(); k++) {
                    if (k > 0) sb.append(',');
                    writeTo(l.get(k), sb);
                }
                sb.append(']');
                return;
            }
            if (v instanceof Double d && d == Math.floor(d) && !d.isInfinite()
                    && Math.abs(d) < 1e15) {
                sb.append((long) (double) d);
                return;
            }
            sb.append(v);
        }
    }

    // ------------------------------------------------------------ fake org

    record Recorded(String method, String path, Map<String, String> headers,
                    String rawBody, Map<String, Object> body) { }

    static final class FakeGraphqlOrg {
        final List<Recorded> requests = new ArrayList<>();
        String mode = "scan"; // scan | partialErrors | dataNull | http401
        HttpServer server;
        String baseUrl;

        void start() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void stop() { server.stop(0); }

        static Map<String, Object> fieldValue(Object value) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("value", value);
            return m;
        }

        static Map<String, Object> node(String id, String name, String industry,
                                        Double revenue, String website) {
            Map<String, Object> n = new LinkedHashMap<>();
            n.put("Id", id);
            n.put("Name", fieldValue(name));
            n.put("Industry", fieldValue(industry));
            n.put("AnnualRevenue", fieldValue(revenue));
            n.put("Website", fieldValue(website));
            return n;
        }

        static Map<String, Object> edge(Map<String, Object> node, String cursor) {
            Map<String, Object> e = new LinkedHashMap<>();
            e.put("node", node);
            e.put("cursor", cursor);
            return e;
        }

        static Map<String, Object> connection(List<Object> edges, boolean hasNext,
                                              String endCursor, long total) {
            Map<String, Object> pageInfo = new LinkedHashMap<>();
            pageInfo.put("hasNextPage", hasNext);
            pageInfo.put("endCursor", endCursor);
            Map<String, Object> conn = new LinkedHashMap<>();
            conn.put("edges", edges);
            conn.put("pageInfo", pageInfo);
            conn.put("totalCount", (double) total);
            return conn;
        }

        static Map<String, Object> envelope(Map<String, Object> connection,
                                            List<Object> errors) {
            Map<String, Object> query = new LinkedHashMap<>();
            query.put("Account", connection);
            Map<String, Object> uiapi = new LinkedHashMap<>();
            uiapi.put("query", query);
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("uiapi", uiapi);
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("data", data);
            if (errors != null) body.put("errors", errors);
            return body;
        }

        static Map<String, Object> gqlError(String message, List<Object> path,
                                            String classification) {
            Map<String, Object> ext = new LinkedHashMap<>();
            ext.put("classification", classification);
            Map<String, Object> err = new LinkedHashMap<>();
            err.put("message", message);
            if (path != null) err.put("path", path);
            err.put("extensions", ext);
            return err;
        }

        static final Map<String, Object> N1 =
                node("001KB000001aaaAAA", "Acme HQ", "Energy", 1200000.0, null);
        static final Map<String, Object> N2 =
                node("001KB000002bbbAAA", "Blue Harbor Logistics", "Marine", null,
                        "https://blueharbor.example");
        static final Map<String, Object> N3 =
                node("001KB000003cccAAA", "Cinder Ridge Mining", "Mining", 74000000.0,
                        "https://cinderridge.example");
        static final Map<String, Object> N4 =
                node("001KB000004dddAAA", "Dockside Ops", null, 5000.0,
                        "https://dockside.example");
        static final Map<String, Object> N5 =
                node("001KB000005eeeAAA", "Ember Analytics", "Technology", 990000.5,
                        "https://ember.example");

        void handle(HttpExchange ex) throws IOException {
            String raw = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            Map<String, String> headers = new LinkedHashMap<>();
            for (String key : List.of("Authorization", "Content-type", "Accept")) {
                if (ex.getRequestHeaders().containsKey(key)) {
                    headers.put(key.toLowerCase(), ex.getRequestHeaders().getFirst(key));
                }
            }
            Map<String, Object> parsed = null;
            if (!raw.isEmpty()) {
                Object v = MiniJson.parse(raw);
                if (v instanceof Map<?, ?> m) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> mm = (Map<String, Object>) m;
                    parsed = mm;
                }
            }
            synchronized (requests) {
                requests.add(new Recorded(ex.getRequestMethod(), ex.getRequestURI().getPath(),
                        headers, raw, parsed));
            }

            if (!("Bearer " + TOKEN).equals(ex.getRequestHeaders().getFirst("Authorization"))
                    || "http401".equals(mode)) {
                respond(ex, 401, "[{\"message\":\"Session expired or invalid\","
                        + "\"errorCode\":\"INVALID_SESSION_ID\"}]");
                return;
            }
            if (!GRAPHQL_PATH.equals(ex.getRequestURI().getPath())
                    || !"POST".equals(ex.getRequestMethod())) {
                respond(ex, 404, "[{\"message\":\"The requested resource does not exist\","
                        + "\"errorCode\":\"NOT_FOUND\"}]");
                return;
            }

            Object after = null;
            if (parsed != null && parsed.get("variables") instanceof Map<?, ?> vars) {
                after = vars.get("after");
            }

            Map<String, Object> body;
            switch (mode) {
                case "partialErrors" -> body = envelope(
                        connection(new ArrayList<>(List.of(
                                edge(N1, "cur-aa1"), edge(N2, "cur-aa2"))),
                                false, "cur-aa2", 2),
                        new ArrayList<>(List.of(gqlError(
                                "DataFetchingException while resolving AnnualRevenue",
                                new ArrayList<>(List.of("uiapi", "query", "Account",
                                        "edges", 1.0, "node", "AnnualRevenue")),
                                "DataFetchingException"))));
                case "dataNull" -> {
                    Map<String, Object> b = new LinkedHashMap<>();
                    b.put("data", null);
                    b.put("errors", new ArrayList<>(List.of(gqlError(
                            "Validation error of type FieldUndefined: Field 'Revenue__c' "
                                    + "in type 'Account' is undefined",
                            null, "ValidationError"))));
                    body = b;
                }
                default -> {
                    if (after == null) {
                        body = envelope(connection(new ArrayList<>(List.of(
                                edge(N1, "cur-aa1"), edge(N2, "cur-aa2"))),
                                true, "cur-aa2", 5), null);
                    } else if ("cur-aa2".equals(after)) {
                        body = envelope(connection(new ArrayList<>(List.of(
                                edge(N3, "cur-bb3"), edge(N4, "cur-bb4"))),
                                true, "cur-bb4", 5), null);
                    } else if ("cur-bb4".equals(after)) {
                        body = envelope(connection(new ArrayList<>(List.of(
                                edge(N5, "cur-cc5"))),
                                false, "cur-cc5", 5), null);
                    } else {
                        Map<String, Object> b = new LinkedHashMap<>();
                        b.put("data", null);
                        b.put("errors", new ArrayList<>(List.of(gqlError(
                                "Invalid cursor: " + after, null, "ExecutionAborted"))));
                        body = b;
                    }
                }
            }
            respond(ex, 200, MiniJson.write(body));
        }

        void respond(HttpExchange ex, int status, String body) throws IOException {
            byte[] payload = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            ex.sendResponseHeaders(status, payload.length);
            ex.getResponseBody().write(payload);
            ex.close();
        }
    }

    // ------------------------------------------------------------ tests

    static String stripped(String doc) {
        return doc.replaceAll("\\s+", "");
    }

    static void t1_scan_protocol_and_decoding() throws Exception {
        FakeGraphqlOrg fake = new FakeGraphqlOrg();
        fake.start();
        try {
            AccountsExporter exporter =
                    new AccountsExporter(fake.baseUrl, VERSION, TOKEN, PAGE_SIZE);
            AccountsExport export = exporter.fetchAll();

            checkEq(fake.requests.size(), 3,
                    "a 5-record scan at page size 2 is exactly three requests");
            for (Recorded r : fake.requests) {
                checkEq(r.method(), "POST", "GraphQL requests are POSTs");
                checkEq(r.path(), GRAPHQL_PATH,
                        "the endpoint is the versioned graphql path");
                checkEq(r.headers().get("authorization"), "Bearer " + TOKEN,
                        "every request carries the Bearer token");
                check(String.valueOf(r.headers().get("content-type"))
                                .startsWith("application/json"),
                        "every request declares Content-Type: application/json");
                check(r.body() != null && r.body().get("query") instanceof String,
                        "the POST body carries the operation document under 'query'");
                check(r.body().get("variables") instanceof Map,
                        "the POST body carries a 'variables' map");
            }

            String doc = (String) fake.requests.get(0).body().get("query");
            String flat = stripped(doc);
            check(flat.startsWith("query"), "the document is a query operation");
            for (String token : List.of(
                    "$first:Int", "$after:String", "uiapi{", "query{", "Account(",
                    "first:$first", "after:$after", "edges{", "node{", "Id",
                    "Name{value", "Industry{value", "AnnualRevenue{value",
                    "Website{value", "cursor", "pageInfo{", "hasNextPage",
                    "endCursor", "totalCount")) {
                check(flat.contains(token),
                        "operation document must contain '" + token + "' — got:\n" + doc);
            }
            for (Recorded r : fake.requests) {
                checkEq(r.body().get("query"), doc,
                        "the operation document must be identical on every page");
                check(!((String) r.body().get("query")).contains("cur-"),
                        "cursors travel in variables, never spliced into the document");
            }

            @SuppressWarnings("unchecked")
            Map<String, Object> vars1 = (Map<String, Object>) fake.requests.get(0).body().get("variables");
            @SuppressWarnings("unchecked")
            Map<String, Object> vars2 = (Map<String, Object>) fake.requests.get(1).body().get("variables");
            @SuppressWarnings("unchecked")
            Map<String, Object> vars3 = (Map<String, Object>) fake.requests.get(2).body().get("variables");
            check(vars1.get("first") instanceof Double d && d.intValue() == PAGE_SIZE,
                    "variables.first carries the page size");
            check(!vars1.containsKey("after") || vars1.get("after") == null,
                    "the first page sends after as null or omits it");
            checkEq(vars2.get("after"), "cur-aa2",
                    "page 2 passes page 1's endCursor as after");
            checkEq(vars3.get("after"), "cur-bb4",
                    "page 3 passes page 2's endCursor as after");

            check(export.totalCount == 5, "totalCount must be decoded (got "
                    + export.totalCount + ")");
            checkEq(export.records.size(), 5, "all pages decode into one list");
            check(export.warnings.isEmpty(), "a clean scan reports no warnings");

            AccountRecord a1 = export.records.get(0);
            checkEq(a1.id, "001KB000001aaaAAA", "Id decodes from the node scalar");
            checkEq(a1.name, "Acme HQ", "Name decodes through its value field");
            checkEq(a1.industry, "Energy", "Industry decodes through its value field");
            checkEq(a1.annualRevenue, 1200000.0, "AnnualRevenue decodes as a number");
            check(a1.website == null,
                    "a {value: null} field decodes to null (empty, not hidden)");

            AccountRecord a2 = export.records.get(1);
            checkEq(a2.name, "Blue Harbor Logistics", "second node decodes in order");
            check(a2.annualRevenue == null, "null AnnualRevenue value stays null");

            checkEq(export.records.get(2).name, "Cinder Ridge Mining",
                    "page 2 records follow page 1");
            check(export.records.get(3).industry == null,
                    "null Industry value stays null");
            checkEq(export.records.get(4).name, "Ember Analytics",
                    "the final page's record is last");
            checkEq(export.records.get(4).annualRevenue, 990000.5,
                    "fractional revenue survives decoding");

            List<String> ids = new ArrayList<>();
            for (AccountRecord r : export.records) ids.add(r.id);
            checkEq(ids.stream().distinct().count(), 5L,
                    "no record may be duplicated across pages");
        } finally {
            fake.stop();
        }
    }

    static void t2_errors_alongside_partial_data() throws Exception {
        FakeGraphqlOrg fake = new FakeGraphqlOrg();
        fake.start();
        fake.mode = "partialErrors";
        try {
            AccountsExporter exporter =
                    new AccountsExporter(fake.baseUrl, VERSION, TOKEN, PAGE_SIZE);
            AccountsExport export = exporter.fetchAll();
            checkEq(export.records.size(), 2,
                    "partial data must be kept when errors accompany it");
            checkEq(export.warnings.size(), 1,
                    "the accompanying GraphQL errors surface as warnings");
            GraphqlError w = export.warnings.get(0);
            check(w.message.contains("AnnualRevenue"),
                    "warning keeps the error message");
            checkEq(w.errorType, "DataFetchingException",
                    "warning keeps the ErrorType classification");
            check(w.path != null && !w.path.isEmpty(),
                    "warning keeps the error path");
        } finally {
            fake.stop();
        }
    }

    static void t3_total_failure_raises() throws Exception {
        FakeGraphqlOrg fake = new FakeGraphqlOrg();
        fake.start();
        fake.mode = "dataNull";
        try {
            AccountsExporter exporter =
                    new AccountsExporter(fake.baseUrl, VERSION, TOKEN, PAGE_SIZE);
            try {
                exporter.fetchAll();
                check(false, "null data with errors must raise GraphqlQueryException");
            } catch (GraphqlQueryException e) {
                checkEq(e.errors.size(), 1, "the GraphQL errors ride on the exception");
                check(e.errors.get(0).message.contains("Revenue__c"),
                        "the failure keeps the server's message");
                checkEq(e.errors.get(0).errorType, "ValidationError",
                        "FLS/undefined fields classify as ValidationError");
                check(!String.valueOf(e.getMessage()).contains(TOKEN)
                                && !e.toString().contains(TOKEN),
                        "the access token must never appear in exception text");
            }
        } finally {
            fake.stop();
        }
    }

    static void t4_http_error_envelope() throws Exception {
        FakeGraphqlOrg fake = new FakeGraphqlOrg();
        fake.start();
        fake.mode = "http401";
        try {
            AccountsExporter exporter =
                    new AccountsExporter(fake.baseUrl, VERSION, TOKEN, PAGE_SIZE);
            try {
                exporter.fetchAll();
                check(false, "a 401 must raise GraphqlHttpException");
            } catch (GraphqlHttpException e) {
                checkEq(e.status, 401, "the HTTP status rides on the exception");
                checkEq(e.errorCode, "INVALID_SESSION_ID",
                        "the REST envelope's errorCode is decoded");
                check(!String.valueOf(e.getMessage()).contains(TOKEN)
                                && !e.toString().contains(TOKEN),
                        "the access token must never appear in exception text");
            }
        } finally {
            fake.stop();
        }
    }

    static void t5_wrong_version_is_not_found() throws Exception {
        FakeGraphqlOrg fake = new FakeGraphqlOrg();
        fake.start();
        try {
            AccountsExporter exporter =
                    new AccountsExporter(fake.baseUrl, "v52.0", TOKEN, PAGE_SIZE);
            try {
                exporter.fetchAll();
                check(false, "a stale API version path must surface the 404 envelope");
            } catch (GraphqlHttpException e) {
                checkEq(e.status, 404, "only the pinned graphql endpoint exists");
                checkEq(e.errorCode, "NOT_FOUND", "the REST envelope is decoded");
            }
        } finally {
            fake.stop();
        }
    }

    @SuppressWarnings("unchecked")
    static void t6_fixtures_wired() throws Exception {
        Map<String, Object> contract = (Map<String, Object>) MiniJson.parse(
                Files.readString(Path.of("docs", "contract.json"), StandardCharsets.UTF_8));
        Map<String, Object> sources = (Map<String, Object>) MiniJson.parse(
                Files.readString(Path.of("docs", "official_sources.json"), StandardCharsets.UTF_8));
        checkEq(contract.get("api_version"), VERSION,
                "the pinned stable API version is v67.0");
        Map<String, Object> research = (Map<String, Object>) sources.get("research");
        checkEq(research.get("required"), Boolean.TRUE, "research is required");
        List<Object> officialSources = (List<Object>) research.get("official_sources");
        check(officialSources.size() >= 2, "at least two official sources");
        for (Object src : officialSources) {
            String url = String.valueOf(((Map<String, Object>) src).get("url"));
            check(url.startsWith("https://developer.salesforce.com/"),
                    "provenance must point at first-party Salesforce docs");
        }
        Map<String, Object> response = (Map<String, Object>) contract.get("response");
        Map<String, Object> gqlErrors = (Map<String, Object>) response.get("graphql_errors");
        String classification = MiniJson.write(gqlErrors);
        check(classification.contains("ValidationError")
                        && classification.contains("DataFetchingException"),
                "the contract pins the documented ErrorType values");
    }

    public static void main(String[] args) throws Exception {
        t1_scan_protocol_and_decoding();
        System.out.println("ok  t1_scan_protocol_and_decoding");
        t2_errors_alongside_partial_data();
        System.out.println("ok  t2_errors_alongside_partial_data");
        t3_total_failure_raises();
        System.out.println("ok  t3_total_failure_raises");
        t4_http_error_envelope();
        System.out.println("ok  t4_http_error_envelope");
        t5_wrong_version_is_not_found();
        System.out.println("ok  t5_wrong_version_is_not_found");
        t6_fixtures_wired();
        System.out.println("ok  t6_fixtures_wired");
        System.out.println("PASS  6 tests, " + checks + " assertions");
    }
}
