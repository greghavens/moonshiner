import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Acceptance harness: loopback fake S/4HANA gateway serving the Business
 * Partner (A2X) OData V2 wire contract pinned in docs/contract.json
 * (d-envelope JSON, __next paging, deferred/inline navigation, SAP error
 * documents). No real system, no real credentials, no sleeps.
 * Run with: java TestMain.java
 * Protected — do not modify this file or anything under docs/.
 */
public class TestMain {

    static final String SERVICE_PATH = "/sap/opu/odata/sap/API_BUSINESS_PARTNER";
    static final String USER = "BP_A2X_COMM_USER";
    static final String PASS = "dummy-fixture-secret-c88";
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

    // ------------------------------------------------------------- mini JSON

    static final class Json {
        final String s;
        int i;

        Json(String s) { this.s = s; }

        static Object parse(String text) {
            Json p = new Json(text);
            Object v = p.value();
            p.ws();
            if (p.i != p.s.length()) throw new IllegalArgumentException("trailing json at " + p.i);
            return v;
        }

        void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        Object value() {
            ws();
            char c = s.charAt(i);
            if (c == '{') return object();
            if (c == '[') return array();
            if (c == '"') return string();
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("null", i)) { i += 4; return null; }
            int j = i;
            while (j < s.length() && "-+.eE0123456789".indexOf(s.charAt(j)) >= 0) j++;
            double d = Double.parseDouble(s.substring(i, j));
            i = j;
            return d;
        }

        Map<String, Object> object() {
            Map<String, Object> m = new java.util.LinkedHashMap<>();
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

        List<Object> array() {
            List<Object> a = new ArrayList<>();
            i++; ws();
            if (s.charAt(i) == ']') { i++; return a; }
            while (true) {
                a.add(value());
                ws();
                char c = s.charAt(i++);
                if (c == ']') return a;
                if (c != ',') throw new IllegalArgumentException("expected , or ] at " + (i - 1));
            }
        }

        String string() {
            if (s.charAt(i) != '"') throw new IllegalArgumentException("expected string at " + i);
            StringBuilder b = new StringBuilder();
            i++;
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') return b.toString();
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case '"' -> b.append('"');
                        case '\\' -> b.append('\\');
                        case '/' -> b.append('/');
                        case 'n' -> b.append('\n');
                        case 't' -> b.append('\t');
                        case 'r' -> b.append('\r');
                        case 'b' -> b.append('\b');
                        case 'f' -> b.append('\f');
                        case 'u' -> {
                            b.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                            i += 4;
                        }
                        default -> throw new IllegalArgumentException("bad escape \\" + e);
                    }
                } else {
                    b.append(c);
                }
            }
        }
    }

    // ------------------------------------------------------------- mock tenant

    record Recorded(String method, String rawUrl, Map<String, String> headers) {}

    record Scripted(int status, String body, Map<String, String> headers) {
        static Scripted json(int status, String body) { return new Scripted(status, body, Map.of()); }
    }

    interface Serve {
        Scripted apply(int n, Recorded rec);
    }

    static final class MockGateway implements AutoCloseable {
        final List<Recorded> requests = new ArrayList<>();
        final HttpServer server;
        final String origin;
        final Serve serve;

        MockGateway(Serve serve) throws IOException {
            this.serve = serve;
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            origin = "http://127.0.0.1:" + server.getAddress().getPort();
            server.createContext("/", this::handle);
            server.start();
        }

        String serviceRoot() { return origin + SERVICE_PATH; }

        void handle(HttpExchange ex) throws IOException {
            Map<String, String> headers = new java.util.LinkedHashMap<>();
            ex.getRequestHeaders().forEach((k, v) -> headers.put(k.toLowerCase(java.util.Locale.ROOT), String.join(",", v)));
            Recorded rec = new Recorded(ex.getRequestMethod(), ex.getRequestURI().toString(), headers);
            int n;
            synchronized (requests) {
                n = requests.size();
                requests.add(rec);
            }
            Scripted s;
            try {
                s = serve.apply(n, rec);
            } catch (Throwable t) {
                s = Scripted.json(599, "{\"mockScriptError\":\"" + t + "\"}");
            }
            byte[] body = (s.body() == null ? "" : s.body()).getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            s.headers().forEach((k, v) -> ex.getResponseHeaders().set(k, v));
            ex.sendResponseHeaders(s.status(), body.length == 0 ? -1 : body.length);
            try (OutputStream out = ex.getResponseBody()) {
                if (body.length > 0) out.write(body);
            }
            ex.close();
        }

        @Override
        public void close() { server.stop(0); }
    }

    // ------------------------------------------------------------- fixtures

    static String partner(String id, String name, String category, String created, String addresses) {
        return """
                {"__metadata":{"id":"%s","type":"API_BUSINESS_PARTNER.A_BusinessPartnerType"},
                 "BusinessPartner":"%s",
                 "BusinessPartnerFullName":"%s",
                 "BusinessPartnerCategory":"%s",
                 "CreationDate":"%s",
                 "to_BusinessPartnerAddress":%s}"""
                .formatted("A_BusinessPartner('" + id + "')", id, name, category, created, addresses);
    }

    static String inlineAddresses(String... cityCountry) {
        StringBuilder b = new StringBuilder("{\"results\":[");
        for (int k = 0; k < cityCountry.length; k++) {
            String[] parts = cityCountry[k].split("/");
            if (k > 0) b.append(',');
            b.append("""
                    {"__metadata":{"type":"API_BUSINESS_PARTNER.A_BusinessPartnerAddressType"},
                     "AddressID":"%d","CityName":"%s","Country":"%s"}""".formatted(28840 + k, parts[0], parts[1]));
        }
        return b.append("]}").toString();
    }

    static String deferredAddresses(String origin, String id) {
        return "{\"__deferred\":{\"uri\":\"" + origin + SERVICE_PATH
                + "/A_BusinessPartner('" + id + "')/to_BusinessPartnerAddress?srvMarker=defer-" + id + "\"}}";
    }

    static String feed(String extra, String... entries) {
        return "{\"d\":{\"results\":[" + String.join(",", entries) + "]" + extra + "}}";
    }

    static final String SAP_ERROR = """
            {"error":{
              "code":"/IWBEP/CM_MGW_RT/021",
              "message":{"lang":"en","value":"Filtering on property 'IndustrySector' is not supported"},
              "innererror":{
                "application":{"component_id":"LO-MD-BP","service_namespace":"/SAP/","service_id":"API_BUSINESS_PARTNER","service_version":"0001"},
                "transactionid":"C8A11D6E44FD00E0",
                "errordetails":[
                  {"code":"/IWBEP/CX_MGW_TECH_EXCEPTION","message":"Filtering on property 'IndustrySector' is not supported","propertyref":"IndustrySector","severity":"error"}
                ]}}}""";

    // ------------------------------------------------------------- scenarios

    static void testQueryBuilding() {
        String path = ODataQuery.entitySet("A_BusinessPartner")
                .select("BusinessPartner", "BusinessPartnerFullName", "BusinessPartnerCategory")
                .filterEq("BusinessPartnerCategory", "2")
                .filterEq("SearchTerm1", "WHOLESALE")
                .expand("to_BusinessPartnerAddress")
                .top(200)
                .inlinecountAllPages()
                .toPath();
        checkEq(path,
                "/A_BusinessPartner?$format=json"
                        + "&$select=BusinessPartner,BusinessPartnerFullName,BusinessPartnerCategory"
                        + "&$filter=BusinessPartnerCategory%20eq%20'2'%20and%20SearchTerm1%20eq%20'WHOLESALE'"
                        + "&$expand=to_BusinessPartnerAddress"
                        + "&$top=200&$inlinecount=allpages",
                "documented option order and %20 space encoding");

        checkEq(ODataQuery.entitySet("A_BusinessPartner").toPath(),
                "/A_BusinessPartner?$format=json",
                "the json format pin is always present");

        checkEq(ODataQuery.entitySet("A_BusinessPartner").top(50).skip(100).toPath(),
                "/A_BusinessPartner?$format=json&$top=50&$skip=100",
                "$top before $skip");

        String quoted = ODataQuery.entitySet("A_BusinessPartner")
                .filterEq("BusinessPartnerName", "O'Hara Dairy").toPath();
        checkEq(quoted,
                "/A_BusinessPartner?$format=json&$filter=BusinessPartnerName%20eq%20'O''Hara%20Dairy'",
                "single quote inside an OData V2 string literal doubles");

        String nested = ODataQuery.entitySet("A_BusinessPartner")
                .expand("to_BusinessPartnerAddress", "to_BusinessPartnerAddress/to_EmailAddress")
                .toPath();
        checkEq(nested,
                "/A_BusinessPartner?$format=json&$expand=to_BusinessPartnerAddress,to_BusinessPartnerAddress/to_EmailAddress",
                "multi-level $expand joins with commas, slash for depth");
    }

    static void testPagingAndHeaders() throws Exception {
        try (MockGateway gw = new MockGateway((n, rec) -> {
            checkEq(rec.headers().get("authorization"), AUTH, "basic auth on request " + n);
            checkEq(rec.headers().get("accept"), "application/json", "Accept pinned on request " + n);
            switch (n) {
                case 0 -> {
                    checkEq(rec.rawUrl(),
                            SERVICE_PATH + "/A_BusinessPartner?$format=json&$top=2&$inlinecount=allpages",
                            "first request is service path + built query");
                    return Scripted.json(200, feed(
                            ",\"__count\":\"5\",\"__next\":\"" + gwOrigin(rec) + SERVICE_PATH
                                    + "/A_BusinessPartner?$format=json&$skiptoken=2&srvMarker=page-2\"",
                            partner("1000234", "Miller Farms GmbH", "2", "/Date(1767225600000)/", inlineAddresses("Hamburg/DE")),
                            partner("1000388", "Nordsee Logistik AG", "2", "/Date(1767312000000)/", inlineAddresses("Bremen/DE"))));
                }
                case 1 -> {
                    check(rec.rawUrl().contains("srvMarker=page-2"),
                            "__next link must be followed verbatim (srvMarker lost: " + rec.rawUrl() + ")");
                    return Scripted.json(200, feed(
                            ",\"__next\":\"" + gwOrigin(rec) + SERVICE_PATH
                                    + "/A_BusinessPartner?$format=json&$skiptoken=4&srvMarker=page-3\"",
                            partner("1000501", "Vega Dairy Co-op", "2", "/Date(1767398400000)/", inlineAddresses("Kiel/DE")),
                            partner("1000502", "Alster Fruit Trading", "2", "/Date(1767398400000)/", inlineAddresses("Hamburg/DE"))));
                }
                case 2 -> {
                    check(rec.rawUrl().contains("srvMarker=page-3"), "second __next also verbatim");
                    return Scripted.json(200, feed("",
                            partner("1000610", "Baltic Grain Trading", "2", "/Date(1767484800000)/", inlineAddresses("Rostock/DE"))));
                }
                default -> {
                    return Scripted.json(599, "{}");
                }
            }
        })) {
            SapODataClient client = new SapODataClient(gw.serviceRoot(), USER, PASS);
            FetchResult result = client.fetchAll(
                    ODataQuery.entitySet("A_BusinessPartner").top(2).inlinecountAllPages());
            checkEq(gw.requests.size(), 3, "three pages, three requests");
            checkEq(result.entities().size(), 5, "all pages accumulated");
            checkEq(result.inlineCount(), 5L, "__count decoded from the first page");
            checkEq(result.entities().get(0).str("BusinessPartner"), "1000234", "page order preserved (first)");
            checkEq(result.entities().get(4).str("BusinessPartner"), "1000610", "page order preserved (last)");
        }
    }

    static String gwOrigin(Recorded rec) {
        // Loopback origin for building absolute __next links inside scripts.
        return "http://" + rec.headers().get("host");
    }

    static void testEntityNavigationAndDates() throws Exception {
        try (MockGateway gw = new MockGateway((n, rec) -> {
            if (n == 0) {
                return Scripted.json(200, feed("",
                        partner("1000234", "Miller Farms GmbH", "2", "/Date(1767225600000)/",
                                inlineAddresses("Hamburg/DE", "Bremen/DE")),
                        partner("1000777", "Skagen Seafood ApS", "2", "/Date(1767225600000)/",
                                deferredAddresses(gwOrigin(rec), "1000777"))));
            }
            if (n == 1) {
                checkEq(rec.rawUrl(),
                        SERVICE_PATH + "/A_BusinessPartner('1000777')/to_BusinessPartnerAddress?srvMarker=defer-1000777",
                        "deferred uri fetched exactly as given");
                checkEq(rec.headers().get("authorization"), AUTH, "auth on deferred fetch");
                return Scripted.json(200, feed("",
                        "{\"__metadata\":{\"type\":\"API_BUSINESS_PARTNER.A_BusinessPartnerAddressType\"},"
                                + "\"AddressID\":\"30001\",\"CityName\":\"Skagen\",\"Country\":\"DK\"}"));
            }
            return Scripted.json(599, "{}");
        })) {
            SapODataClient client = new SapODataClient(gw.serviceRoot(), USER, PASS);
            FetchResult result = client.fetchAll(ODataQuery.entitySet("A_BusinessPartner"));
            ODataEntity miller = result.entities().get(0);
            ODataEntity skagen = result.entities().get(1);

            check(!miller.hasDeferred("to_BusinessPartnerAddress"), "expanded nav is not deferred");
            List<ODataEntity> addrs = miller.inline("to_BusinessPartnerAddress");
            checkEq(addrs.size(), 2, "inline expanded collection unwrapped from its results wrapper");
            checkEq(addrs.get(0).str("CityName"), "Hamburg", "nested entity property");
            checkEq(addrs.get(1).str("Country"), "DE", "second nested entity property");

            checkEq(miller.instant("CreationDate"), Instant.parse("2026-01-01T00:00:00Z"),
                    "/Date(ms)/ decodes to the UTC instant");
            check(miller.str("BusinessPartnerFullName").equals("Miller Farms GmbH"), "plain property");

            check(skagen.hasDeferred("to_BusinessPartnerAddress"), "unexpanded nav arrives deferred");
            String uri = skagen.deferredUri("to_BusinessPartnerAddress");
            check(uri.endsWith("srvMarker=defer-1000777"), "deferred uri exposed verbatim");
            List<ODataEntity> fetched = client.fetchDeferredCollection(uri);
            checkEq(fetched.size(), 1, "deferred collection fetched on demand");
            checkEq(fetched.get(0).str("CityName"), "Skagen", "deferred entity property");
            checkEq(gw.requests.size(), 2, "exactly one extra request for the deferred nav");
        }
    }

    static void testSapErrorPreserved() throws Exception {
        try (MockGateway gw = new MockGateway((n, rec) -> Scripted.json(400, SAP_ERROR))) {
            SapODataClient client = new SapODataClient(gw.serviceRoot(), USER, PASS);
            try {
                client.fetchAll(ODataQuery.entitySet("A_BusinessPartner").filterEq("IndustrySector", "A1"));
                check(false, "400 with an SAP error document must throw SapODataException");
            } catch (SapODataException e) {
                checkEq(e.httpStatus(), 400, "http status preserved");
                checkEq(e.code(), "/IWBEP/CM_MGW_RT/021", "SAP error code preserved");
                check(e.getMessage().contains("Filtering on property 'IndustrySector' is not supported"),
                        "message.value surfaced in the exception message");
                checkEq(e.errorDetail("innererror", "application", "component_id"), "LO-MD-BP",
                        "innererror application block preserved verbatim");
                checkEq(e.errorDetail("innererror", "transactionid"), "C8A11D6E44FD00E0",
                        "innererror transaction id preserved");
                Object details = e.errorDetail("innererror", "errordetails");
                check(details instanceof List<?> l && l.size() == 1, "errordetails list preserved");
                @SuppressWarnings("unchecked")
                Map<String, Object> first = (Map<String, Object>) ((List<?>) details).get(0);
                checkEq(first.get("propertyref"), "IndustrySector", "errordetails entry fields preserved");
                check(!e.getMessage().contains(PASS), "password never in exception text");
                check(!e.getMessage().contains(AUTH), "auth header never in exception text");
            }
        }
    }

    static void testErrorOnLaterPagePropagates() throws Exception {
        try (MockGateway gw = new MockGateway((n, rec) -> {
            if (n == 0) {
                return Scripted.json(200, feed(
                        ",\"__next\":\"" + gwOrigin(rec) + SERVICE_PATH + "/A_BusinessPartner?$skiptoken=9\"",
                        partner("1000234", "Miller Farms GmbH", "2", "/Date(1767225600000)/", inlineAddresses("Hamburg/DE"))));
            }
            return Scripted.json(502, "{\"error\":{\"code\":\"GATEWAY_TIMEOUT\",\"message\":{\"lang\":\"en\",\"value\":\"Backend not reachable\"}}}");
        })) {
            SapODataClient client = new SapODataClient(gw.serviceRoot(), USER, PASS);
            try {
                client.fetchAll(ODataQuery.entitySet("A_BusinessPartner"));
                check(false, "failure on a later page must throw");
            } catch (SapODataException e) {
                checkEq(e.httpStatus(), 502, "later-page status preserved");
                checkEq(e.code(), "GATEWAY_TIMEOUT", "later-page code preserved");
            }
        }
    }

    static void testWholesaleSnapshot() throws Exception {
        try (MockGateway gw = new MockGateway((n, rec) -> {
            if (n == 0) {
                checkEq(rec.rawUrl(),
                        SERVICE_PATH + "/A_BusinessPartner?$format=json"
                                + "&$select=BusinessPartner,BusinessPartnerFullName"
                                + "&$filter=BusinessPartnerCategory%20eq%20'2'"
                                + "&$expand=to_BusinessPartnerAddress",
                        "snapshot issues the documented select+filter+expand query");
                return Scripted.json(200, feed(
                        ",\"__next\":\"" + gwOrigin(rec) + SERVICE_PATH
                                + "/A_BusinessPartner?$format=json&$skiptoken=2&srvMarker=snap-2\"",
                        partner("1000388", "Nordsee Logistik AG", "2", "/Date(1767312000000)/",
                                inlineAddresses("Bremen/DE", "Hamburg/DE")),
                        partner("1000777", "Skagen Seafood ApS", "2", "/Date(1767225600000)/",
                                deferredAddresses(gwOrigin(rec), "1000777"))));
            }
            if (n == 1) {
                check(rec.rawUrl().contains("srvMarker=snap-2"), "snapshot follows __next verbatim");
                return Scripted.json(200, feed("",
                        partner("1000234", "Miller Farms GmbH", "2", "/Date(1767225600000)/",
                                inlineAddresses("Hamburg/DE"))));
            }
            if (rec.rawUrl().contains("to_BusinessPartnerAddress?srvMarker=defer-1000777")) {
                return Scripted.json(200, feed("",
                        "{\"AddressID\":\"30001\",\"CityName\":\"Skagen\",\"Country\":\"DK\"}"));
            }
            return Scripted.json(599, "{}");
        })) {
            SapODataClient client = new SapODataClient(gw.serviceRoot(), USER, PASS);
            List<String> lines = BusinessPartnerReport.wholesaleSnapshot(client);
            checkEq(lines, List.of(
                    "1000234|Miller Farms GmbH|Hamburg/DE",
                    "1000388|Nordsee Logistik AG|Bremen/DE;Hamburg/DE",
                    "1000777|Skagen Seafood ApS|Skagen/DK"),
                    "snapshot lines: sorted by partner number, addresses City/Country joined with ;");
            checkEq(gw.requests.size(), 3, "two pages plus exactly one deferred fetch");
        }
    }

    public static void main(String[] args) throws Exception {
        java.util.Locale.setDefault(java.util.Locale.ROOT);
        java.util.TimeZone.setDefault(java.util.TimeZone.getTimeZone("UTC"));

        testQueryBuilding();
        System.out.println("ok   query building");
        testPagingAndHeaders();
        System.out.println("ok   __next paging + headers");
        testEntityNavigationAndDates();
        System.out.println("ok   inline/deferred navigation + dates");
        testSapErrorPreserved();
        System.out.println("ok   SAP error document preserved");
        testErrorOnLaterPagePropagates();
        System.out.println("ok   later-page error propagates");
        testWholesaleSnapshot();
        System.out.println("ok   wholesale snapshot end to end");
        System.out.println("OK — 6 scenarios, " + checks + " checks");
    }
}
