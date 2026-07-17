// Acceptance tests for the change-marker integration.
//
// Runs a loopback fake NerdGraph endpoint (POST /graphql, API-Key auth,
// entitySearch resolution and the changeTrackingCreateEvent mutation) and
// drives EntityLookup plus the new ChangeMarkerService against it. No real
// New Relic, no real credentials, no Thread.sleep: time comes from an
// injected clock and waiting goes through the injected sleeper. The wire
// contract the fake enforces is pinned in docs/contract.json. This file and
// everything under docs/ are protected; Json.java, NerdGraphClient.java and
// EntityLookup.java are starter code you may extend.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class TestMain {

    static final Map<String, Object> CONTRACT;
    static final Map<String, Object> SOURCES;

    static {
        try {
            CONTRACT = Json.parseObject(Files.readString(Path.of("docs", "contract.json")));
            SOURCES = Json.parseObject(Files.readString(Path.of("docs", "official_sources.json")));
        } catch (IOException e) {
            throw new IllegalStateException("protected docs fixtures missing", e);
        }
    }

    static final String API_KEY = (String) path(CONTRACT, "transport", "fixture_api_key");
    static final Map<String, Object> FIX = section("fixtures");
    static final String GUID = (String) FIX.get("entity_guid");
    static final String CHANGE_ID = (String) FIX.get("change_id");
    static final String CT_ID = (String) FIX.get("change_tracking_id");
    static final long CLOCK_MS = (Long) FIX.get("clock_ms");
    static final long DAY_MS = 24L * 3600L * 1000L;

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
            throw new AssertionError(message + "\n  expected: " + Json.write(expected)
                + "\n  actual:   " + Json.write(actual));
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
    static Map<String, Object> section(String key) {
        return (Map<String, Object>) CONTRACT.get(key);
    }

    record Req(String method, String rawPath, Map<String, String> headers, String body) {
        @SuppressWarnings("unchecked")
        Map<String, Object> json() {
            return Json.parseObject(body);
        }

        String document() {
            return (String) json().get("query");
        }

        @SuppressWarnings("unchecked")
        Map<String, Object> variables() {
            return (Map<String, Object>) json().get("variables");
        }
    }

    static final class FakeNerdGraph {
        final List<Req> requests = new ArrayList<>();
        final List<Object[]> script = new ArrayList<>(); // {status, bodyJson}
        HttpServer server;
        String baseUrl;

        void queue(int status, Object doc) {
            script.add(new Object[] {status, doc});
        }

        void queueSuccess() {
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("changeTrackingId", CT_ID);
            event.put("category", "deployment");
            event.put("type", "basic");
            event.put("categoryAndType", "deployment/basic");
            event.put("timestamp", CLOCK_MS);
            Map<String, Object> entity = new LinkedHashMap<>();
            entity.put("guid", GUID);
            entity.put("name", FIX.get("entity_name"));
            event.put("entity", entity);
            queue(200, Map.of("data", Map.of("changeTrackingCreateEvent",
                Map.of("changeTrackingEvent", event))));
        }

        @SafeVarargs
        final void queueEntities(Object nextCursor, Map<String, Object>... entities) {
            Map<String, Object> results = new LinkedHashMap<>();
            results.put("entities", List.of(entities));
            results.put("nextCursor", nextCursor);
            queue(200, Map.of("data", Map.of("actor",
                Map.of("entitySearch", Map.of("results", results)))));
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
            String body = new String(exchange.getRequestBody().readAllBytes(),
                StandardCharsets.UTF_8);
            Map<String, String> headers = new LinkedHashMap<>();
            exchange.getRequestHeaders().forEach((k, v) ->
                headers.put(k.toLowerCase(), String.join(",", v)));
            requests.add(new Req(exchange.getRequestMethod(),
                exchange.getRequestURI().toString(), headers, body));
            int status = 200;
            Object doc = Map.of("data", Map.of());
            if (!script.isEmpty()) {
                Object[] step = script.remove(0);
                status = (Integer) step[0];
                doc = step[1];
            }
            byte[] payload = (doc instanceof String s ? s : Json.write(doc))
                .getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, payload.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(payload);
            }
        }
    }

    static final class Harness implements AutoCloseable {
        final FakeNerdGraph fake = new FakeNerdGraph();
        final List<Long> sleeps = new ArrayList<>();
        final Map<String, String> ledger = new LinkedHashMap<>();
        long nowMs = CLOCK_MS;
        NerdGraphClient client;

        Harness() throws IOException {
            fake.start();
            client = new NerdGraphClient(fake.baseUrl + "/graphql", API_KEY,
                HttpClient.newHttpClient());
        }

        ChangeMarkerService service(int maxAttempts, long baseDelayMs) {
            return new ChangeMarkerService(client, ledger, () -> nowMs,
                new ChangeMarkerService.Retry(maxAttempts, baseDelayMs, sleeps::add));
        }

        @Override
        public void close() {
            fake.stop();
        }
    }

    static ChangeMarker fullMarker() {
        return new ChangeMarker(GUID, (String) FIX.get("version"), CHANGE_ID)
            .commit((String) FIX.get("commit"))
            .changelog((String) FIX.get("changelog"))
            .deepLink((String) FIX.get("deep_link"))
            .user((String) FIX.get("user"))
            .groupId((String) FIX.get("group_id"))
            .customAttribute("pipeline", "deploy-prod");
    }

    static Map<String, Object> expectedFullEvent() {
        Map<String, Object> deployment = new LinkedHashMap<>();
        deployment.put("version", FIX.get("version"));
        deployment.put("commit", FIX.get("commit"));
        deployment.put("changelog", FIX.get("changelog"));
        deployment.put("deepLink", FIX.get("deep_link"));
        Map<String, Object> kind = new LinkedHashMap<>();
        kind.put("category", "deployment");
        kind.put("type", "basic");
        Map<String, Object> catData = new LinkedHashMap<>();
        catData.put("kind", kind);
        catData.put("categoryFields", Map.of("deployment", deployment));
        Map<String, Object> attrs = new LinkedHashMap<>();
        attrs.put("pipeline", "deploy-prod");
        attrs.put("change_id", CHANGE_ID);
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("categoryAndTypeData", catData);
        event.put("entitySearch", Map.of("query", "id = '" + GUID + "'"));
        event.put("user", FIX.get("user"));
        event.put("groupId", FIX.get("group_id"));
        event.put("customAttributes", attrs);
        event.put("timestamp", CLOCK_MS);
        return event;
    }

    // ---- existing behavior: entity GUID resolution ----------------------

    static void testEntityLookupStillResolvesSingleGuid() throws Exception {
        try (Harness h = new Harness()) {
            Map<String, Object> entity = new LinkedHashMap<>();
            entity.put("guid", GUID);
            entity.put("name", FIX.get("entity_name"));
            h.fake.queueEntities(null, entity);
            String guid = new EntityLookup(h.client)
                .resolveSingleGuid((String) FIX.get("entity_name"),
                    (String) FIX.get("entity_domain"));
            checkEq(GUID, guid, "lookup must return the matching GUID");
            Req req = h.fake.requests.get(0);
            checkEq("POST", req.method(), "NerdGraph calls are POSTs");
            checkEq(API_KEY, req.headers().get("api-key"),
                "the user key travels in the API-Key header");
            check(!req.headers().containsKey("authorization"),
                "NerdGraph auth is API-Key, not a Bearer Authorization header");
            checkEq("name = 'checkout-api' AND domain = 'APM'",
                req.variables().get("query"),
                "the search string travels as a GraphQL variable");
            check(req.document().contains("entitySearch"),
                "lookup must query actor.entitySearch");
            check(!req.document().contains("checkout-api"),
                "user input must not be spliced into the GraphQL document");
        }
    }

    static void testEntityLookupErrorCases() throws Exception {
        try (Harness h = new Harness()) {
            EntityLookup lookup = new EntityLookup(h.client);
            h.fake.queueEntities(null);
            try {
                lookup.resolveSingleGuid("ghost-service", "APM");
                check(false, "zero matches must raise EntityNotFoundException");
            } catch (EntityLookup.EntityNotFoundException expected) {
                checks++;
            }

            Map<String, Object> a = new LinkedHashMap<>(Map.of("guid", GUID));
            Map<String, Object> b = new LinkedHashMap<>(
                Map.of("guid", FIX.get("wrong_entity_guid")));
            h.fake.queueEntities(null, a, b);
            try {
                lookup.resolveSingleGuid("checkout-api", "APM");
                check(false, "two matches must raise AmbiguousEntityException");
            } catch (EntityLookup.AmbiguousEntityException expected) {
                checks++;
            }

            int before = h.fake.requests.size();
            try {
                lookup.resolveSingleGuid("bad'name", "APM");
                check(false, "quoted names must be rejected locally");
            } catch (IllegalArgumentException expected) {
                checks++;
            }
            checkEq((long) before, (long) h.fake.requests.size(),
                "rejected names must never reach the wire");
        }
    }

    // ---- new behavior: the change marker ---------------------------------

    static void testMarkerMutationShape() throws Exception {
        try (Harness h = new Harness()) {
            h.fake.queueSuccess();
            var result = h.service(3, 250).record(fullMarker());

            checkEq(1L, (long) h.fake.requests.size(), "one mutation for a clean create");
            Req req = h.fake.requests.get(0);
            checkEq(API_KEY, req.headers().get("api-key"),
                "the mutation carries the API-Key header");
            String doc = req.document();
            check(doc.contains("changeTrackingCreateEvent"),
                "the current mutation is changeTrackingCreateEvent");
            check(doc.contains("$changeTrackingEvent: ChangeTrackingCreateEventInput!"),
                "the event travels as the documented ChangeTrackingCreateEventInput! variable");
            check(doc.contains("$dataHandlingRules: ChangeTrackingDataHandlingRules"),
                "data handling rules travel as the documented variable");
            check(doc.contains("changeTrackingId"),
                "the selection must return the server-generated changeTrackingId");
            check(!doc.contains("changeTrackingCreateDeployment"),
                "the legacy deployment mutation must not be used");
            check(!doc.contains(GUID), "the GUID travels in variables, not the document");

            checkEq(expectedFullEvent(), req.variables().get("changeTrackingEvent"),
                "the changeTrackingEvent variable must match the documented input exactly");
            checkEq(Map.of("validationFlags", List.of("FAIL_ON_FIELD_LENGTH")),
                req.variables().get("dataHandlingRules"),
                "standard kinds send FAIL_ON_FIELD_LENGTH and never ALLOW_CUSTOM_CATEGORY_OR_TYPE");

            checkEq(CT_ID, result.changeTrackingId(),
                "the server-generated changeTrackingId is surfaced");
            check(!result.replayed(), "a fresh create is not a replay");
            checkEq(CT_ID, h.ledger.get(CHANGE_ID),
                "a confirmed marker lands in the ledger under its stable change id");
        }
    }

    static void testOptionalFieldsAreOmitted() throws Exception {
        try (Harness h = new Harness()) {
            h.fake.queueSuccess();
            h.service(3, 250).record(new ChangeMarker(GUID,
                (String) FIX.get("version"), CHANGE_ID));
            @SuppressWarnings("unchecked")
            Map<String, Object> event = (Map<String, Object>)
                h.fake.requests.get(0).variables().get("changeTrackingEvent");
            @SuppressWarnings("unchecked")
            Map<String, Object> deployment = (Map<String, Object>)
                path(event, "categoryAndTypeData", "categoryFields", "deployment");
            checkEq(Map.of("version", FIX.get("version")), deployment,
                "absent deployment fields are omitted, never null");
            check(!event.containsKey("user") && !event.containsKey("groupId")
                    && !event.containsKey("shortDescription")
                    && !event.containsKey("description"),
                "absent optional top-level fields are omitted");
            @SuppressWarnings("unchecked")
            Map<String, Object> attrs = (Map<String, Object>) event.get("customAttributes");
            checkEq(Map.of("change_id", CHANGE_ID), attrs,
                "the stable change id always rides along as customAttributes.change_id");
            checkEq(CLOCK_MS, event.get("timestamp"),
                "the timestamp comes from the injected clock");
        }
    }

    static void testLedgerReplaySkipsHttp() throws Exception {
        try (Harness h = new Harness()) {
            h.fake.queueSuccess();
            ChangeMarkerService svc = h.service(3, 250);
            svc.record(fullMarker());
            var replay = svc.record(fullMarker());
            checkEq(1L, (long) h.fake.requests.size(),
                "a changeId already in the ledger must not hit the wire again");
            check(replay.replayed(), "the second record() is a replay");
            checkEq(CT_ID, replay.changeTrackingId(),
                "the replay returns the original changeTrackingId");
        }
    }

    static void testTransientRetriesAreByteIdentical() throws Exception {
        try (Harness h = new Harness()) {
            h.fake.queue(429, FIX.get("throttle_body"));
            h.fake.queue(503, "{\"error\":\"bad gateway\"}");
            h.fake.queueSuccess();
            var result = h.service(3, 250).record(fullMarker());

            checkEq(3L, (long) h.fake.requests.size(), "two transient failures, then success");
            checkEq(List.of(250L, 500L), h.sleeps,
                "backoff doubles from the base delay via the injected sleeper");
            String first = h.fake.requests.get(0).body();
            checkEq(first, h.fake.requests.get(1).body(),
                "retries must be byte-identical - same change_id, same timestamp");
            checkEq(first, h.fake.requests.get(2).body(),
                "every retry of one record() call reuses the original body");
            checkEq(CT_ID, result.changeTrackingId(), "the retried create succeeds");
        }
    }

    static void testRetryExhaustionSurfacesStatus() throws Exception {
        try (Harness h = new Harness()) {
            h.fake.queue(500, "{\"error\":\"boom\"}");
            h.fake.queue(500, "{\"error\":\"boom\"}");
            try {
                h.service(2, 100).record(fullMarker());
                check(false, "exhausted retries must fail");
            } catch (NerdGraphClient.NerdGraphHttpException e) {
                checkEq(500L, (long) e.status(), "the last HTTP status is surfaced");
                check(!e.getMessage().contains(API_KEY)
                        && !String.valueOf(e.body()).contains(API_KEY),
                    "the API key must never surface in errors");
            }
            checkEq(2L, (long) h.fake.requests.size(), "exactly maxAttempts attempts");
            checkEq(List.of(100L), h.sleeps, "no trailing sleep after the last attempt");
            check(h.ledger.isEmpty(), "a failed create must not poison the ledger");
        }
    }

    static void testMutationLevelErrorsAreNotRetried() throws Exception {
        try (Harness h = new Harness()) {
            Map<String, Object> envelope = new LinkedHashMap<>();
            envelope.put("data", null);
            envelope.put("errors", List.of(FIX.get("validation_error")));
            h.fake.queue(200, envelope);
            try {
                h.service(3, 250).record(fullMarker());
                check(false, "GraphQL errors without data must fail");
            } catch (ChangeTrackingException e) {
                checkEq(List.of(path(CONTRACT, "fixtures", "validation_error", "message")),
                    e.messages(), "the server's error messages are preserved");
                checkEq(List.of("INVALID_INPUT"), e.errorClasses(),
                    "the errorClass extension is surfaced");
            }
            checkEq(1L, (long) h.fake.requests.size(),
                "validation failures are permanent and never retried");
            check(h.sleeps.isEmpty(), "no backoff for permanent failures");
            check(h.ledger.isEmpty(), "no ledger entry without a confirmed success");
        }
    }

    static void testWrongEntityInResponseIsRejected() throws Exception {
        try (Harness h = new Harness()) {
            Map<String, Object> event = new LinkedHashMap<>();
            event.put("changeTrackingId", CT_ID);
            event.put("timestamp", CLOCK_MS);
            event.put("entity", Map.of("guid", FIX.get("wrong_entity_guid"),
                "name", "someone-elses-service"));
            h.fake.queue(200, Map.of("data", Map.of("changeTrackingCreateEvent",
                Map.of("changeTrackingEvent", event))));
            try {
                h.service(3, 250).record(fullMarker());
                check(false, "a marker recorded on the wrong entity must fail");
            } catch (ChangeTrackingException expected) {
                checks++;
            }
            check(h.ledger.isEmpty(),
                "a marker on the wrong entity must not enter the ledger");
        }
    }

    static void testTimestampOverrideWindow() throws Exception {
        try (Harness h = new Harness()) {
            int before = h.fake.requests.size();
            try {
                h.service(3, 250).record(fullMarker().timestamp(CLOCK_MS - DAY_MS - 1));
                check(false, "timestamps beyond +/- 24h must be rejected locally");
            } catch (IllegalArgumentException expected) {
                checks++;
            }
            checkEq((long) before, (long) h.fake.requests.size(),
                "an invalid timestamp must never reach the wire");

            h.fake.queueSuccess();
            long valid = CLOCK_MS - DAY_MS + 60_000;
            h.service(3, 250).record(fullMarker().timestamp(valid));
            @SuppressWarnings("unchecked")
            Map<String, Object> event = (Map<String, Object>)
                h.fake.requests.get(0).variables().get("changeTrackingEvent");
            checkEq(valid, event.get("timestamp"),
                "a valid override inside the window is sent as-is");
        }
    }

    static void testProvenanceFixtures() {
        checkEq(Boolean.TRUE, path(SOURCES, "research", "required"),
            "research provenance must be present");
        check(((List<?>) path(SOURCES, "research", "official_sources")).size() >= 2,
            "at least two official sources");
        checkEq("changeTrackingCreateEvent", path(CONTRACT, "mutation", "name"),
            "the pinned mutation is the current one");
        checkEq("ChangeTrackingCreateEventInput!",
            path(CONTRACT, "mutation", "variable_declarations", "changeTrackingEvent"),
            "the pinned input type");
        checkEq(24L, path(CONTRACT, "mutation", "timestamp_window_hours"),
            "the pinned timestamp window");
        checkEq("change_id", path(CONTRACT, "idempotency", "stable_id_attribute"),
            "the pinned stable identifier attribute");
        checkEq("API-Key", path(CONTRACT, "transport", "auth_header"),
            "the pinned auth header");
    }

    public static void main(String[] args) throws Exception {
        List<Object[]> tests = List.of(
            new Object[] {"entity_lookup_still_resolves_single_guid",
                (Callable) TestMain::testEntityLookupStillResolvesSingleGuid},
            new Object[] {"entity_lookup_error_cases",
                (Callable) TestMain::testEntityLookupErrorCases},
            new Object[] {"marker_mutation_shape",
                (Callable) TestMain::testMarkerMutationShape},
            new Object[] {"optional_fields_are_omitted",
                (Callable) TestMain::testOptionalFieldsAreOmitted},
            new Object[] {"ledger_replay_skips_http",
                (Callable) TestMain::testLedgerReplaySkipsHttp},
            new Object[] {"transient_retries_are_byte_identical",
                (Callable) TestMain::testTransientRetriesAreByteIdentical},
            new Object[] {"retry_exhaustion_surfaces_status",
                (Callable) TestMain::testRetryExhaustionSurfacesStatus},
            new Object[] {"mutation_level_errors_are_not_retried",
                (Callable) TestMain::testMutationLevelErrorsAreNotRetried},
            new Object[] {"wrong_entity_in_response_is_rejected",
                (Callable) TestMain::testWrongEntityInResponseIsRejected},
            new Object[] {"timestamp_override_window",
                (Callable) TestMain::testTimestampOverrideWindow},
            new Object[] {"provenance_fixtures",
                (Callable) TestMain::testProvenanceFixtures});
        int failed = 0;
        for (Object[] t : tests) {
            String name = (String) t[0];
            try {
                ((Callable) t[1]).run();
                System.out.println("ok  - " + name);
            } catch (Throwable e) {
                failed++;
                System.out.println("FAIL- " + name + ": " + e);
            }
        }
        System.out.println(checks + " checks across " + tests.size() + " tests");
        if (failed > 0) {
            throw new AssertionError(failed + " test(s) failed");
        }
    }

    interface Callable {
        void run() throws Exception;
    }
}
