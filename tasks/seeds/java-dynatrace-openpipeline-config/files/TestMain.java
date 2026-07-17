// Acceptance tests for the OpenPipeline configuration reconciler.
//
// Runs a loopback fake Dynatrace Settings API 2.0 (/api/v2/settings/objects
// with cursor pagination, updateToken optimistic locking, validateOnly dry
// runs and SettingsObjectResponse error decoding) and drives SettingsClient
// plus the new PipelineReconciler against it. No real Dynatrace, no real
// credentials, no Thread.sleep. The wire contract the fake enforces is
// pinned in docs/contract.json. This file and everything under docs/ are
// protected; Json.java, SettingsClient.java and SettingsApiException.java
// are starter code you may extend.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class TestMain {

    static final Map<String, Object> CONTRACT;
    static final Map<String, Object> SOURCES;

    static {
        try {
            CONTRACT = Json.parseObject(
                Files.readString(Path.of("docs", "contract.json")));
            SOURCES = Json.parseObject(
                Files.readString(Path.of("docs", "official_sources.json")));
        } catch (IOException e) {
            throw new IllegalStateException("protected docs fixtures missing", e);
        }
    }

    static final String TOKEN =
        (String) path(CONTRACT, "auth", "fixture_token");
    static final String SCHEMA_ID = "builtin:openpipeline.logs.pipelines";
    static final String FIELDS = "objectId,value,scope,schemaVersion,updateToken";
    static final String OBJECTS = "/api/v2/settings/objects";
    static final String OWNED_PREFIX = "acme.";

    static int checks = 0;

    static void check(boolean ok, String message) {
        checks++;
        if (!ok) {
            throw new AssertionError(message);
        }
    }

    static void checkEq(Object expected, Object actual, String message) {
        checks++;
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + "\n  expected: "
                + Json.write(expected) + "\n  actual:   " + Json.write(actual));
        }
    }

    @SuppressWarnings("unchecked")
    static Object path(Map<String, Object> doc, String... keys) {
        Object cur = doc;
        for (String key : keys) {
            cur = ((Map<String, Object>) cur).get(key);
        }
        return cur;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> deepCopy(Map<String, Object> doc) {
        return (Map<String, Object>) Json.parse(Json.write(doc));
    }

    record Req(String method, String path, String query,
               Map<String, String> headers, String body) {
        Map<String, String> params() {
            Map<String, String> out = new LinkedHashMap<>();
            if (query == null || query.isEmpty()) {
                return out;
            }
            for (String pair : query.split("&")) {
                int eq = pair.indexOf('=');
                String k = eq < 0 ? pair : pair.substring(0, eq);
                String v = eq < 0 ? "" : pair.substring(eq + 1);
                out.put(URLDecoder.decode(k, StandardCharsets.UTF_8),
                        URLDecoder.decode(v, StandardCharsets.UTF_8));
            }
            return out;
        }
    }

    static final class FakeSettings {
        final List<Req> requests = new ArrayList<>();
        final List<Object[]> script = new ArrayList<>(); // {status, doc}
        HttpServer server;
        String baseUrl;

        void queue(int status, Object doc) {
            script.add(new Object[] {status, doc});
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

        void handle(HttpExchange exchange) throws IOException {
            byte[] body = exchange.getRequestBody().readAllBytes();
            Map<String, String> headers = new LinkedHashMap<>();
            exchange.getRequestHeaders().forEach((k, v) ->
                headers.put(k.toLowerCase(java.util.Locale.ROOT), String.join(",", v)));
            requests.add(new Req(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                headers,
                new String(body, StandardCharsets.UTF_8)));
            int status = 200;
            Object doc = Map.of();
            if (!script.isEmpty()) {
                Object[] step = script.remove(0);
                status = (Integer) step[0];
                doc = step[1];
            }
            byte[] payload = Json.write(doc).getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, payload.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(payload);
            }
            exchange.close();
        }
    }

    interface Body {
        void run(FakeSettings fake) throws Exception;
    }

    static void with(Body body) throws Exception {
        FakeSettings fake = new FakeSettings();
        fake.start();
        try {
            body.run(fake);
        } finally {
            fake.stop();
        }
    }

    static SettingsClient client(FakeSettings fake) {
        return new SettingsClient(fake.baseUrl, TOKEN, HttpClient.newHttpClient());
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> fixture(String name) {
        return deepCopy((Map<String, Object>) path(CONTRACT, "fixtures", name));
    }

    static Map<String, Object> listPage(List<Map<String, Object>> items) {
        Map<String, Object> page = new LinkedHashMap<>();
        page.put("items", items);
        page.put("totalCount", (double) items.size());
        page.put("pageSize", 100.0);
        page.put("nextPageKey", null);
        return page;
    }

    @SuppressWarnings("unchecked")
    static List<Map<String, Object>> processorsOf(Map<String, Object> value) {
        return (List<Map<String, Object>>) path(value, "processing", "processors");
    }

    static Map<String, Object> desired(String customId,
                                       List<Map<String, Object>> processors) {
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("customId", customId);
        d.put("processors", processors);
        return d;
    }

    static Map<String, Object> newMask() {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("id", "acme.mask-ssn");
        p.put("type", "dql");
        p.put("enabled", true);
        p.put("matcher", "isNotNull(content)");
        p.put("dql", Map.of("script", "fieldsAdd content = maskSsnV2(content)"));
        return p;
    }

    static Map<String, Object> sampleDebug() {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("id", "acme.sample-debug");
        p.put("type", "drop");
        p.put("enabled", true);
        p.put("matcher", "loglevel == \"TRACE\"");
        return p;
    }

    // --- starter behavior: listing must keep working -----------------------

    static void testListPagingAndAuth() throws Exception {
        with(fake -> {
            Map<String, Object> web = fixture("object_web");
            Map<String, Object> batch = fixture("object_batch");
            Map<String, Object> page1 = new LinkedHashMap<>();
            page1.put("items", List.of(web));
            page1.put("totalCount", 2.0);
            page1.put("pageSize", 1.0);
            page1.put("nextPageKey", "AQAfake+cursor=");
            fake.queue(200, page1);
            fake.queue(200, listPage(List.of(batch)));

            List<Map<String, Object>> items =
                client(fake).listObjects(SCHEMA_ID, FIELDS, 1);

            checkEq(2, items.size(), "both pages accumulate");
            checkEq(web.get("objectId"), items.get(0).get("objectId"),
                "page order preserved");
            checkEq(2, fake.requests.size(), "stops when nextPageKey is null");
            Req first = fake.requests.get(0);
            checkEq(OBJECTS, first.path(), "list path");
            Map<String, String> q1 = first.params();
            checkEq(SCHEMA_ID, q1.get("schemaIds"), "schemaIds filter");
            checkEq(FIELDS, q1.get("fields"),
                "fields must request updateToken explicitly");
            checkEq("1", q1.get("pageSize"), "explicit pageSize");
            checkEq(Set.of("schemaIds", "fields", "pageSize"), q1.keySet(),
                "no undocumented first-page parameters");
            Req second = fake.requests.get(1);
            checkEq(Set.of("nextPageKey"), second.params().keySet(),
                "subsequent pages carry the cursor and nothing else");
            checkEq("AQAfake+cursor=", second.params().get("nextPageKey"),
                "cursor round-trips byte-exactly");
            for (Req req : fake.requests) {
                checkEq("Api-Token " + TOKEN, req.headers().get("authorization"),
                    "Api-Token header on every request");
            }
        });
    }

    static void testListErrorEnvelope() throws Exception {
        with(fake -> {
            fake.queue(403, Map.of("error", Map.of(
                "code", 403.0,
                "message", "Token is missing required scope settings.read",
                "constraintViolations", List.of())));
            try {
                client(fake).listObjects(SCHEMA_ID, FIELDS, 100);
                check(false, "403 must raise SettingsApiException");
            } catch (SettingsApiException e) {
                checkEq(403, e.status(), "status");
                checkEq(403, e.errorCode(), "error code from the envelope");
                check(e.getMessage().contains("settings.read"),
                    "envelope message surfaces");
                check(!e.getMessage().contains(TOKEN),
                    "token must never appear in error text");
            }
        });
    }

    // --- the feature: reconciliation ---------------------------------------

    @SuppressWarnings("unchecked")
    static void testReconcileUpdatesOwnedAndPreservesUnowned() throws Exception {
        with(fake -> {
            Map<String, Object> web = fixture("object_web");
            fake.queue(200, listPage(List.of(web, fixture("object_batch"))));
            fake.queue(200, Map.of("code", 200.0, "objectId", web.get("objectId")));
            fake.queue(200, Map.of("code", 200.0, "objectId", web.get("objectId")));

            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            List<Map<String, Object>> outcomes = reconciler.reconcile(
                SCHEMA_ID,
                List.of(desired("acme.logs.web", List.of(newMask(), sampleDebug()))));

            checkEq(3, fake.requests.size(), "list, validate, apply");
            Req list = fake.requests.get(0);
            checkEq("GET", list.method(), "reconcile starts from a fresh list");
            checkEq(FIELDS, list.params().get("fields"),
                "reconcile must request updateToken via fields");

            Req validate = fake.requests.get(1);
            Req apply = fake.requests.get(2);
            String objectPath = OBJECTS + "/" + web.get("objectId");
            checkEq("PUT", validate.method(), "validate is a PUT");
            checkEq(objectPath, validate.path(), "validate path");
            checkEq("true", validate.params().get("validateOnly"),
                "dry-run validation must set validateOnly=true");
            checkEq("PUT", apply.method(), "apply is a PUT");
            checkEq(objectPath, apply.path(), "apply path");
            check(!apply.params().containsKey("validateOnly"),
                "the real write must not be a dry run");
            checkEq(validate.body(), apply.body(),
                "exactly the validated payload is applied");
            check(validate.headers().get("content-type").startsWith("application/json"),
                "JSON content type on writes");
            checkEq("Api-Token " + TOKEN, apply.headers().get("authorization"),
                "auth header on writes");

            Map<String, Object> body = Json.parseObject(apply.body());
            checkEq(Set.of("value", "updateToken", "schemaVersion"), body.keySet(),
                "PUT body carries value, updateToken and schemaVersion only");
            checkEq(web.get("updateToken"), body.get("updateToken"),
                "updateToken from the fetched object");
            checkEq(web.get("schemaVersion"), body.get("schemaVersion"),
                "schemaVersion pinned from the fetched object");

            Map<String, Object> expectedValue = deepCopy(
                (Map<String, Object>) web.get("value"));
            List<Map<String, Object>> current = processorsOf(expectedValue);
            List<Map<String, Object>> merged = new ArrayList<>();
            merged.add(current.get(0));            // logs.enrich-region stays put
            merged.add(newMask());                 // acme.mask-ssn replaced in place
            merged.add(current.get(2));            // logs.trim-body stays put
            merged.add(sampleDebug());             // new owned processor appended
            ((Map<String, Object>) expectedValue.get("processing"))
                .put("processors", merged);
            checkEq(expectedValue, body.get("value"),
                "owned processors merge in place; unowned processors and every "
                + "other value field are preserved verbatim");

            checkEq(1, outcomes.size(), "one outcome per desired pipeline");
            checkEq("acme.logs.web", outcomes.get(0).get("customId"), "outcome id");
            checkEq("updated", outcomes.get(0).get("action"), "outcome action");
            checkEq(web.get("objectId"), outcomes.get(0).get("objectId"),
                "outcome carries the settings objectId");
        });
    }

    @SuppressWarnings("unchecked")
    static void testReconcileNoOpWritesNothing() throws Exception {
        with(fake -> {
            Map<String, Object> web = fixture("object_web");
            fake.queue(200, listPage(List.of(web)));
            List<Map<String, Object>> currentOwned = new ArrayList<>();
            for (Map<String, Object> p : processorsOf(
                    (Map<String, Object>) fixture("object_web").get("value"))) {
                if (((String) p.get("id")).startsWith(OWNED_PREFIX)) {
                    currentOwned.add(p);
                }
            }

            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            List<Map<String, Object>> outcomes = reconciler.reconcile(
                SCHEMA_ID, List.of(desired("acme.logs.web", currentOwned)));

            checkEq(1, fake.requests.size(),
                "an already-converged pipeline must not be written at all");
            checkEq("unchanged", outcomes.get(0).get("action"), "no-op outcome");
        });
    }

    static void testReconcileMissingPipeline() throws Exception {
        with(fake -> {
            fake.queue(200, listPage(List.of(fixture("object_web"))));
            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            List<Map<String, Object>> outcomes = reconciler.reconcile(
                SCHEMA_ID, List.of(desired("acme.logs.mobile", List.of(sampleDebug()))));
            checkEq(1, fake.requests.size(), "nothing to write for a missing pipeline");
            checkEq("missing", outcomes.get(0).get("action"), "missing outcome");
            checkEq("acme.logs.mobile", outcomes.get(0).get("customId"), "outcome id");
        });
    }

    @SuppressWarnings("unchecked")
    static void testValidationFailureBlocksApply() throws Exception {
        with(fake -> {
            Map<String, Object> web = fixture("object_web");
            fake.queue(200, listPage(List.of(web)));
            fake.queue(400, fixture("validation_400"));

            Map<String, Object> bad = new LinkedHashMap<>();
            bad.put("id", "acme.bad-matcher");
            bad.put("type", "drop");
            bad.put("enabled", true);
            bad.put("matcher", "");

            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            List<Map<String, Object>> outcomes = reconciler.reconcile(
                SCHEMA_ID, List.of(desired("acme.logs.web", List.of(bad))));

            checkEq(2, fake.requests.size(),
                "a failed validation must block the real write");
            checkEq("true", fake.requests.get(1).params().get("validateOnly"),
                "the failing call was the dry run");
            checkEq("invalid", outcomes.get(0).get("action"), "invalid outcome");
            List<Map<String, Object>> violations =
                (List<Map<String, Object>>) outcomes.get(0).get("violations");
            checkEq(1, violations.size(), "constraint violations decoded");
            checkEq("must not be blank", violations.get(0).get("message"),
                "violation message");
            checkEq("value.processing.processors[1].matcher",
                violations.get(0).get("path"), "violation path");
        });
    }

    @SuppressWarnings("unchecked")
    static void testConflictRetryUsesFreshState() throws Exception {
        with(fake -> {
            Map<String, Object> web = fixture("object_web");
            Map<String, Object> fresh = fixture("object_web");
            fresh.put("updateToken", path(CONTRACT, "fixtures", "fresh_updateToken"));
            processorsOf((Map<String, Object>) fresh.get("value"))
                .add(fixture("concurrent_processor"));
            fresh.put("schemaId", SCHEMA_ID);

            fake.queue(200, listPage(List.of(web)));
            fake.queue(200, Map.of("code", 200.0));
            fake.queue(409, fixture("conflict_409"));
            fake.queue(200, fresh);
            fake.queue(200, Map.of("code", 200.0));
            fake.queue(200, Map.of("code", 200.0, "objectId", web.get("objectId")));

            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            List<Map<String, Object>> outcomes = reconciler.reconcile(
                SCHEMA_ID,
                List.of(desired("acme.logs.web", List.of(newMask(), sampleDebug()))));

            checkEq(6, fake.requests.size(),
                "list, validate, apply(409), refetch, validate, apply");
            Req refetch = fake.requests.get(3);
            checkEq("GET", refetch.method(), "409 recovery refetches the object");
            checkEq(OBJECTS + "/" + web.get("objectId"), refetch.path(),
                "single-object GET for the fresh revision");
            Req retry = fake.requests.get(5);
            check(!retry.params().containsKey("validateOnly"),
                "final request is the real write");
            Map<String, Object> body = Json.parseObject(retry.body());
            checkEq(path(CONTRACT, "fixtures", "fresh_updateToken"),
                body.get("updateToken"),
                "retry must use the fresh updateToken, not the stale one");
            List<Map<String, Object>> processors = (List<Map<String, Object>>)
                path((Map<String, Object>) body.get("value"),
                     "processing", "processors");
            List<String> ids = new ArrayList<>();
            for (Map<String, Object> p : processors) {
                ids.add((String) p.get("id"));
            }
            checkEq(List.of("logs.enrich-region", "acme.mask-ssn",
                            "logs.trim-body", "logs.geo-lookup",
                            "acme.sample-debug"),
                ids,
                "the retry merges against the fresh value so the concurrently "
                + "added unowned processor survives");
            checkEq("updated", outcomes.get(0).get("action"), "retry succeeded");
        });
    }

    static void testSecondConflictGivesUp() throws Exception {
        with(fake -> {
            Map<String, Object> web = fixture("object_web");
            Map<String, Object> fresh = fixture("object_web");
            fresh.put("updateToken", path(CONTRACT, "fixtures", "fresh_updateToken"));

            fake.queue(200, listPage(List.of(web)));
            fake.queue(200, Map.of("code", 200.0));
            fake.queue(409, fixture("conflict_409"));
            fake.queue(200, fresh);
            fake.queue(200, Map.of("code", 200.0));
            fake.queue(409, fixture("conflict_409"));

            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            List<Map<String, Object>> outcomes = reconciler.reconcile(
                SCHEMA_ID,
                List.of(desired("acme.logs.web", List.of(newMask()))));

            checkEq(6, fake.requests.size(),
                "exactly one refetch-and-retry round, then give up");
            checkEq("conflict", outcomes.get(0).get("action"), "conflict outcome");
            checkEq("acme.logs.web", outcomes.get(0).get("customId"), "outcome id");
        });
    }

    static void testOwnedPrefixEnforcedBeforeAnyRequest() throws Exception {
        with(fake -> {
            Map<String, Object> foreign = new LinkedHashMap<>();
            foreign.put("id", "logs.cleanup");
            foreign.put("type", "drop");
            foreign.put("enabled", true);
            foreign.put("matcher", "true");
            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            try {
                reconciler.reconcile(SCHEMA_ID,
                    List.of(desired("acme.logs.web", List.of(foreign))));
                check(false, "desired processors outside the owned prefix must be rejected");
            } catch (IllegalArgumentException expected) {
                check(expected.getMessage().contains("logs.cleanup"),
                    "rejection names the offending processor");
            }
            checkEq(0, fake.requests.size(), "rejected before any request");
        });
    }

    @SuppressWarnings("unchecked")
    static void testAppendWhenNoOwnedProcessorsYet() throws Exception {
        with(fake -> {
            Map<String, Object> batch = fixture("object_batch");
            fake.queue(200, listPage(List.of(batch)));
            fake.queue(200, Map.of("code", 200.0));
            fake.queue(200, Map.of("code", 200.0));

            Map<String, Object> tag = new LinkedHashMap<>();
            tag.put("id", "acme.tag-batch");
            tag.put("type", "fieldsAdd");
            tag.put("enabled", true);
            tag.put("matcher", "true");
            tag.put("fieldsAdd", Map.of("fields",
                List.of(Map.of("name", "pipeline", "value", "batch"))));

            PipelineReconciler reconciler =
                new PipelineReconciler(client(fake), OWNED_PREFIX);
            reconciler.reconcile(SCHEMA_ID,
                List.of(desired("acme.logs.batch", List.of(tag))));

            checkEq(3, fake.requests.size(), "list, validate, apply");
            Map<String, Object> body = Json.parseObject(fake.requests.get(2).body());
            Map<String, Object> value = (Map<String, Object>) body.get("value");
            List<Map<String, Object>> processors = (List<Map<String, Object>>)
                path(value, "processing", "processors");
            checkEq(2, processors.size(), "appended after the unowned processor");
            checkEq("logs.enrich-region", processors.get(0).get("id"),
                "unowned processor keeps its position");
            checkEq("acme.tag-batch", processors.get(1).get("id"),
                "first owned processor appends at the end of the stage");
            checkEq("notRoutable", value.get("routing"),
                "untouched value fields are preserved");
            checkEq("Batch job logs", value.get("displayName"),
                "displayName preserved");
        });
    }

    @SuppressWarnings("unchecked")
    static void testProvenanceFixtures() {
        Map<String, Object> research = (Map<String, Object>) SOURCES.get("research");
        checkEq(Boolean.TRUE, research.get("required"), "research required");
        check(((List<Object>) research.get("official_sources")).size() >= 2,
            "at least two official sources");
        check(((String) CONTRACT.get("catalog_correction")).contains("June 29, 2026"),
            "the Configurations API EOL correction is recorded");
        checkEq(SCHEMA_ID, path(CONTRACT, "schema", "schemaId"), "pinned schema");
        checkEq(OBJECTS, path(CONTRACT, "endpoints", "list", "path"), "pinned path");
        checkEq(FIELDS, path(CONTRACT, "endpoints", "list", "fields"), "pinned fields");
    }

    public static void main(String[] args) throws Exception {
        testProvenanceFixtures();
        testListPagingAndAuth();
        testListErrorEnvelope();
        testReconcileUpdatesOwnedAndPreservesUnowned();
        testReconcileNoOpWritesNothing();
        testReconcileMissingPipeline();
        testValidationFailureBlocksApply();
        testConflictRetryUsesFreshState();
        testSecondConflictGivesUp();
        testOwnedPrefixEnforcedBeforeAnyRequest();
        testAppendWhenNoOwnedProcessorsYet();
        System.out.println("all tests passed (" + checks + " checks)");
    }
}
