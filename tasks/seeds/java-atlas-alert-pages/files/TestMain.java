/*
 * Acceptance harness for the Atlas alert/event exporter (MongoDB Atlas
 * Administration API v2). A loopback com.sun.net.httpserver mock speaks the
 * wire contract pinned in docs/contract.json: dated Accept media type,
 * encoded filter queries, links/results/totalCount pagination, the
 * application/json ApiError envelope, and empty-collection semantics.
 * No real Atlas, no credentials, no sleeps.
 *
 * Run: java TestMain.java
 * Protected — do not modify this file or anything under docs/.
 */
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;

public class TestMain {

    static final String TOKEN = "fixture-atlas-bearer-token-java4";
    static final String GID = "64f00dfeedfacecafe123abc";
    static final String MEDIA = "application/vnd.atlas.2023-01-01+json";
    static final String EVENTS = "/api/atlas/v2/groups/" + GID + "/events";
    static final String ALERTS = "/api/atlas/v2/groups/" + GID + "/alerts";

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String what) {
        if (got == null ? want != null : !got.equals(want)) {
            throw new AssertionError(what + ": got " + got + ", want " + want);
        }
        checks++;
    }

    // ------------------------------------------------------------ mini JSON
    static final class Json {
        final String s;
        int i = 0;

        Json(String s) { this.s = s; }

        static Object parse(String text) {
            Json p = new Json(text);
            Object v = p.value();
            p.ws();
            if (p.i != p.s.length()) throw new IllegalArgumentException("trailing json at " + p.i);
            return v;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> obj(Object o) { return (Map<String, Object>) o; }

        @SuppressWarnings("unchecked")
        static List<Object> arr(Object o) { return (List<Object>) o; }

        void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        char peek() { return s.charAt(i); }

        void expect(char c) {
            if (i >= s.length() || s.charAt(i) != c) throw new IllegalArgumentException("expected " + c + " at " + i);
            i++;
        }

        Object value() {
            ws();
            char c = peek();
            if (c == '{') return object();
            if (c == '[') return array();
            if (c == '"') return string();
            if (c == 't') { i += 4; return Boolean.TRUE; }
            if (c == 'f') { i += 5; return Boolean.FALSE; }
            if (c == 'n') { i += 4; return null; }
            return number();
        }

        Map<String, Object> object() {
            Map<String, Object> m = new LinkedHashMap<>();
            expect('{');
            ws();
            if (peek() == '}') { i++; return m; }
            while (true) {
                ws();
                String k = string();
                ws();
                expect(':');
                m.put(k, value());
                ws();
                if (peek() == ',') { i++; continue; }
                expect('}');
                return m;
            }
        }

        List<Object> array() {
            List<Object> l = new ArrayList<>();
            expect('[');
            ws();
            if (peek() == ']') { i++; return l; }
            while (true) {
                l.add(value());
                ws();
                if (peek() == ',') { i++; continue; }
                expect(']');
                return l;
            }
        }

        String string() {
            expect('"');
            StringBuilder b = new StringBuilder();
            while (true) {
                char c = s.charAt(i++);
                if (c == '"') return b.toString();
                if (c == '\\') {
                    char e = s.charAt(i++);
                    switch (e) {
                        case 'n' -> b.append('\n');
                        case 't' -> b.append('\t');
                        case 'u' -> { b.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); i += 4; }
                        default -> b.append(e);
                    }
                } else {
                    b.append(c);
                }
            }
        }

        Double number() {
            int start = i;
            while (i < s.length() && "+-0123456789.eE".indexOf(s.charAt(i)) >= 0) i++;
            return Double.valueOf(s.substring(start, i));
        }
    }

    // ------------------------------------------------------------ mock host
    record Scripted(int status, String contentType, String json) {}

    record Req(String method, String uri, String accept, String authorization) {}

    static final class FakeAtlas implements AutoCloseable {
        final HttpServer server;
        final String base;
        final Deque<Scripted> script = new ArrayDeque<>();
        final List<Req> requests = new ArrayList<>();

        FakeAtlas() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            base = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        synchronized void handle(HttpExchange ex) throws IOException {
            requests.add(new Req(
                    ex.getRequestMethod(),
                    ex.getRequestURI().toString(),
                    ex.getRequestHeaders().getFirst("Accept"),
                    ex.getRequestHeaders().getFirst("Authorization")));
            Scripted s = script.isEmpty()
                    ? new Scripted(500, "application/json", apiError(500, "UNEXPECTED_REQUEST", "mock script exhausted", "Internal Server Error"))
                    : script.removeFirst();
            byte[] out = s.json().getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", s.contentType());
            ex.sendResponseHeaders(s.status(), out.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(out); }
        }

        @Override
        public void close() { server.stop(0); }
    }

    // ------------------------------------------------------- fixture bodies
    static String apiError(int status, String code, String detail, String reason) {
        return "{\"detail\":\"" + detail + "\",\"error\":" + status + ",\"errorCode\":\"" + code
                + "\",\"parameters\":[],\"reason\":\"" + reason + "\"}";
    }

    static String event(String id, String type, String created, String username) {
        String user = username == null ? "" : ",\"username\":\"" + username + "\"";
        return "{\"created\":\"" + created + "\",\"eventTypeName\":\"" + type + "\",\"groupId\":\"" + GID
                + "\",\"id\":\"" + id + "\",\"links\":[{\"href\":\"ignored\",\"rel\":\"self\"}]" + user + "}";
    }

    static String alert(String id, String cfg, String type, String status, String created, String updated) {
        return "{\"alertConfigId\":\"" + cfg + "\",\"created\":\"" + created + "\",\"eventTypeName\":\"" + type
                + "\",\"groupId\":\"" + GID + "\",\"id\":\"" + id + "\",\"status\":\"" + status
                + "\",\"updated\":\"" + updated + "\"}";
    }

    static String page(String base, List<String> results, Integer total, String nextQuery, String prevQuery) {
        StringBuilder links = new StringBuilder("[{\"href\":\"" + base + "#self\",\"rel\":\"self\"}");
        if (prevQuery != null) links.append(",{\"href\":\"").append(base).append(prevQuery).append("\",\"rel\":\"previous\"}");
        if (nextQuery != null) links.append(",{\"href\":\"").append(base).append(nextQuery).append("\",\"rel\":\"next\"}");
        links.append("]");
        String count = total == null ? "" : ",\"totalCount\":" + total;
        return "{\"links\":" + links + ",\"results\":[" + String.join(",", results) + "]" + count + "}";
    }

    static Scripted okPage(String base, List<String> results, Integer total, String nextQuery, String prevQuery) {
        return new Scripted(200, MEDIA, page(base, results, total, nextQuery, prevQuery));
    }

    // ---------------------------------------------------------------- tests
    static void testEmptyCollectionAndBaseQuery() throws Exception {
        Path cp = Path.of("checkpoint_empty.txt");
        Files.deleteIfExists(cp);
        try (FakeAtlas atlas = new FakeAtlas()) {
            atlas.script.add(okPage(atlas.base, List.of(), 0, null, null));
            AtlasClient client = new AtlasClient(atlas.base, TOKEN);
            EventExporter exporter = new EventExporter(client, cp);
            List<List<?>> deliveries = new ArrayList<>();
            ExportReport report = exporter.export(GID, List.of("HOST_DOWN"), deliveries::add);

            checkEq(atlas.requests.size(), 1, "request count for an empty collection");
            Req r = atlas.requests.get(0);
            checkEq(r.method(), "GET", "events method");
            checkEq(r.uri(), EVENTS + "?eventType=HOST_DOWN&itemsPerPage=100&includeCount=true&pageNum=1",
                    "first events request without a checkpoint (no minDate)");
            checkEq(r.accept(), MEDIA, "events Accept header");
            checkEq(r.authorization(), "Bearer " + TOKEN, "events Authorization header");

            checkEq(report.delivered(), 0, "delivered count on empty collection");
            checkEq(report.totalCount(), 0, "totalCount on empty collection");
            check(deliveries.isEmpty(), "the sink must not be invoked when there are no events");
            check(!Files.exists(cp), "no checkpoint may be written when nothing was delivered");
        } finally {
            Files.deleteIfExists(cp);
        }
    }

    static void testPaginationDedupAndCheckpoint() throws Exception {
        Path cp = Path.of("checkpoint_main.txt");
        Files.writeString(cp, "2026-07-16T09:30:00Z");
        try (FakeAtlas atlas = new FakeAtlas()) {
            String q2 = EVENTS + "?eventType=CLUSTER_CREATED&eventType=JOINED_GROUP&minDate=2026-07-16T09%3A30%3A00Z&itemsPerPage=100&includeCount=true&pageNum=2";
            String q3 = EVENTS + "?eventType=CLUSTER_CREATED&eventType=JOINED_GROUP&minDate=2026-07-16T09%3A30%3A00Z&itemsPerPage=100&includeCount=true&pageNum=3";
            atlas.script.add(okPage(atlas.base, List.of(
                    event("ev001", "CLUSTER_CREATED", "2026-07-16T10:00:00Z", "svc.deploy@corp.example"),
                    event("ev002", "JOINED_GROUP", "2026-07-16T10:05:00Z", null)), 5, q2, null));
            atlas.script.add(okPage(atlas.base, List.of(
                    event("ev002", "JOINED_GROUP", "2026-07-16T10:05:00Z", null),
                    event("ev003", "CLUSTER_CREATED", "2026-07-16T11:11:00Z", "ana@corp.example")), 5, q3, "#prev"));
            atlas.script.add(okPage(atlas.base, List.of(
                    event("ev004", "JOINED_GROUP", "2026-07-16T12:00:00Z", null)), 5, null, "#prev"));

            AtlasClient client = new AtlasClient(atlas.base, TOKEN);
            EventExporter exporter = new EventExporter(client, cp);
            List<List<AtlasEvent>> deliveries = new ArrayList<>();
            ExportReport report = exporter.export(GID, List.of("CLUSTER_CREATED", "JOINED_GROUP"), deliveries::add);

            checkEq(atlas.requests.size(), 3, "one request per page");
            checkEq(atlas.requests.get(0).uri(),
                    EVENTS + "?eventType=CLUSTER_CREATED&eventType=JOINED_GROUP&minDate=2026-07-16T09%3A30%3A00Z&itemsPerPage=100&includeCount=true&pageNum=1",
                    "first request: repeated eventType params in order, minDate from the checkpoint with ':' percent-encoded");
            checkEq(atlas.requests.get(1).uri(), q2, "second request must follow the rel=next href verbatim");
            checkEq(atlas.requests.get(2).uri(), q3, "third request must follow the rel=next href verbatim");
            for (Req r : atlas.requests) {
                checkEq(r.accept(), MEDIA, "dated Accept on every page request");
                checkEq(r.authorization(), "Bearer " + TOKEN, "Bearer auth on every page request");
            }

            checkEq(deliveries.size(), 1, "the sink receives exactly one complete delivery");
            List<AtlasEvent> got = deliveries.get(0);
            checkEq(got.size(), 4, "duplicate ev002 across the page boundary must be deduplicated");
            checkEq(got.get(0).id(), "ev001", "delivery order: first-seen order [0]");
            checkEq(got.get(1).id(), "ev002", "delivery order: first-seen order [1]");
            checkEq(got.get(2).id(), "ev003", "delivery order: first-seen order [2]");
            checkEq(got.get(3).id(), "ev004", "delivery order: first-seen order [3]");
            checkEq(got.get(0).eventTypeName(), "CLUSTER_CREATED", "event type decode");
            checkEq(got.get(0).created(), "2026-07-16T10:00:00Z", "event created decode");
            checkEq(got.get(0).username(), "svc.deploy@corp.example", "event username decode");
            check(got.get(1).username() == null, "an event without username decodes as null");

            checkEq(report.delivered(), 4, "report.delivered");
            checkEq(report.totalCount(), 5, "report.totalCount from the server");
            checkEq(Files.readString(cp).strip(), "2026-07-16T12:00:00Z",
                    "checkpoint advances to the newest delivered created timestamp only after full delivery");
        } finally {
            Files.deleteIfExists(cp);
        }
    }

    static void testMidScanFailureKeepsCheckpoint() throws Exception {
        Path cp = Path.of("checkpoint_midscan.txt");
        Files.writeString(cp, "2026-07-16T09:30:00Z");
        try (FakeAtlas atlas = new FakeAtlas()) {
            String q2 = EVENTS + "?eventType=HOST_DOWN&minDate=2026-07-16T09%3A30%3A00Z&itemsPerPage=100&includeCount=true&pageNum=2";
            atlas.script.add(okPage(atlas.base, List.of(
                    event("ev101", "HOST_DOWN", "2026-07-16T10:00:00Z", null)), 2, q2, null));
            atlas.script.add(new Scripted(500, "application/json",
                    apiError(500, "UNEXPECTED_ERROR", "Unexpected error.", "Internal Server Error")));

            AtlasClient client = new AtlasClient(atlas.base, TOKEN);
            EventExporter exporter = new EventExporter(client, cp);
            List<List<AtlasEvent>> deliveries = new ArrayList<>();
            AtlasApiException thrown = null;
            try {
                exporter.export(GID, List.of("HOST_DOWN"), deliveries::add);
            } catch (AtlasApiException e) {
                thrown = e;
            }
            check(thrown != null, "a failed page fetch must raise AtlasApiException");
            checkEq(thrown.status(), 500, "mid-scan failure status");
            checkEq(thrown.errorCode(), "UNEXPECTED_ERROR", "mid-scan failure errorCode");
            check(deliveries.isEmpty(), "no partial delivery: the sink must see nothing when a later page fails");
            checkEq(Files.readString(cp).strip(), "2026-07-16T09:30:00Z", "checkpoint untouched after a failed scan");
        } finally {
            Files.deleteIfExists(cp);
        }
    }

    static void testSinkFailureKeepsCheckpoint() throws Exception {
        Path cp = Path.of("checkpoint_sink.txt");
        Files.writeString(cp, "2026-07-16T09:30:00Z");
        try (FakeAtlas atlas = new FakeAtlas()) {
            atlas.script.add(okPage(atlas.base, List.of(
                    event("ev201", "HOST_DOWN", "2026-07-16T10:00:00Z", null)), 1, null, null));
            AtlasClient client = new AtlasClient(atlas.base, TOKEN);
            EventExporter exporter = new EventExporter(client, cp);
            RuntimeException boom = new RuntimeException("downstream kafka unavailable");
            RuntimeException thrown = null;
            try {
                exporter.export(GID, List.of("HOST_DOWN"), events -> { throw boom; });
            } catch (RuntimeException e) {
                thrown = e;
            }
            check(thrown == boom, "a sink failure must propagate to the caller");
            checkEq(Files.readString(cp).strip(), "2026-07-16T09:30:00Z",
                    "checkpoint must not advance when delivery failed");
        } finally {
            Files.deleteIfExists(cp);
        }
    }

    static void testProjectNotFound() throws Exception {
        Path cp = Path.of("checkpoint_404.txt");
        Files.deleteIfExists(cp);
        try (FakeAtlas atlas = new FakeAtlas()) {
            atlas.script.add(new Scripted(404, "application/json",
                    apiError(404, "RESOURCE_NOT_FOUND", "Cannot find resource " + EVENTS + ".", "Not Found")));
            AtlasClient client = new AtlasClient(atlas.base, TOKEN);
            EventExporter exporter = new EventExporter(client, cp);
            AtlasApiException thrown = null;
            try {
                exporter.export(GID, List.of("HOST_DOWN"), events -> {});
            } catch (AtlasApiException e) {
                thrown = e;
            }
            check(thrown != null, "a 404 context must raise AtlasApiException (unlike an empty collection)");
            checkEq(thrown.status(), 404, "404 status");
            checkEq(thrown.errorCode(), "RESOURCE_NOT_FOUND", "404 errorCode");
            checkEq(thrown.detail(), "Cannot find resource " + EVENTS + ".", "404 detail");
            checkEq(thrown.reason(), "Not Found", "404 reason");
            check(!String.valueOf(thrown.getMessage()).contains(TOKEN), "exception text must not leak the token");
            check(!Files.exists(cp), "no checkpoint after a failed export");
        } finally {
            Files.deleteIfExists(cp);
        }
    }

    static void testOpenAlerts() throws Exception {
        try (FakeAtlas atlas = new FakeAtlas()) {
            atlas.script.add(okPage(atlas.base, List.of(
                    alert("al001", "cfg001", "OUTSIDE_METRIC_THRESHOLD", "OPEN", "2026-07-15T22:00:00Z", "2026-07-16T01:30:00Z"),
                    alert("al002", "cfg002", "HOST_DOWN", "OPEN", "2026-07-16T03:00:00Z", "2026-07-16T03:05:00Z")), 2, null, null));
            AtlasClient client = new AtlasClient(atlas.base, TOKEN);

            List<AtlasAlert> alerts = client.alerts(GID, "OPEN");
            checkEq(atlas.requests.size(), 1, "alerts request count");
            checkEq(atlas.requests.get(0).uri(), ALERTS + "?status=OPEN&itemsPerPage=100&includeCount=true&pageNum=1",
                    "alerts request URI with the documented status filter");
            checkEq(atlas.requests.get(0).accept(), MEDIA, "alerts Accept header");
            checkEq(alerts.size(), 2, "alert count");
            checkEq(alerts.get(0).id(), "al001", "alert id decode");
            checkEq(alerts.get(0).alertConfigId(), "cfg001", "alertConfigId decode");
            checkEq(alerts.get(0).eventTypeName(), "OUTSIDE_METRIC_THRESHOLD", "alert eventTypeName decode");
            checkEq(alerts.get(0).status(), "OPEN", "alert status decode");
            checkEq(alerts.get(1).created(), "2026-07-16T03:00:00Z", "alert created decode");
            checkEq(alerts.get(1).updated(), "2026-07-16T03:05:00Z", "alert updated decode");

            IllegalArgumentException bad = null;
            try {
                client.alerts(GID, "SNOOZED");
            } catch (IllegalArgumentException e) {
                bad = e;
            }
            check(bad != null, "alerts must reject a status outside the documented enum (OPEN/TRACKING/CLOSED)");
            checkEq(atlas.requests.size(), 1, "an invalid status must not produce an HTTP request");
        }
    }

    static void testDocsFixturesParse() throws Exception {
        Map<String, Object> contract = Json.obj(Json.parse(Files.readString(Path.of("docs/contract.json"))));
        Map<String, Object> sources = Json.obj(Json.parse(Files.readString(Path.of("docs/official_sources.json"))));
        checkEq(contract.get("media_type"), MEDIA, "contract media type");
        checkEq(Json.arr(contract.get("alert_status_enum")), List.of("OPEN", "TRACKING", "CLOSED"), "alert status enum");
        check(Json.arr(Json.obj(sources.get("research")).get("official_sources")).size() >= 2,
                "at least two official sources recorded");
    }

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));
        testEmptyCollectionAndBaseQuery();
        System.out.println("ok   empty collection + base query");
        testPaginationDedupAndCheckpoint();
        System.out.println("ok   pagination, dedup, checkpoint");
        testMidScanFailureKeepsCheckpoint();
        System.out.println("ok   mid-scan failure keeps checkpoint");
        testSinkFailureKeepsCheckpoint();
        System.out.println("ok   sink failure keeps checkpoint");
        testProjectNotFound();
        System.out.println("ok   404 vs empty semantics");
        testOpenAlerts();
        System.out.println("ok   open alerts + status validation");
        testDocsFixturesParse();
        System.out.println("ok   docs fixtures");
        System.out.println("all tests passed (" + checks + " checks)");
    }
}
