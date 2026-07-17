import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.TimeZone;
import java.util.function.IntConsumer;

/**
 * Acceptance harness: loopback fake Okta org speaking the Users API wire
 * contract pinned in docs/contract.json (create staged user, lifecycle
 * activate, status polling, HAL links, error taxonomy). No real Okta, no
 * real credentials, no sleeps. Run with: java TestMain.java
 * Protected — do not modify this file or anything under docs/.
 */
public class TestMain {

    static final String TOKEN = "00dummySSWSt0ken-fixture-only-91xQ";
    static final String USER_ID = "00ub7fkqrDkJHmYnB0g4";
    static final String LOGIN = "ada.chen@example.com";

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

        @SuppressWarnings("unchecked")
        static Map<String, Object> obj(Object o) { return (Map<String, Object>) o; }

        @SuppressWarnings("unchecked")
        static Map<String, Object> at(Object o, String... path) {
            Map<String, Object> m = (Map<String, Object>) o;
            for (String p : path) m = (Map<String, Object>) m.get(p);
            return m;
        }
    }

    // ------------------------------------------------------------- fake Okta

    record Recorded(String method, String rawUri, Map<String, String> headers, String body) {}

    record Scripted(int status, String json) {}

    static final class FakeOkta implements AutoCloseable {
        final HttpServer server;
        final String base;
        final List<Recorded> requests = java.util.Collections.synchronizedList(new ArrayList<>());
        final List<Scripted> script;

        FakeOkta(List<Scripted> script) throws IOException {
            this.script = new ArrayList<>(script);
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            base = "http://127.0.0.1:" + server.getAddress().getPort();
            server.createContext("/", this::handle);
            server.start();
        }

        void handle(HttpExchange ex) throws IOException {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            Map<String, String> headers = new java.util.HashMap<>();
            ex.getRequestHeaders().forEach((k, v) -> headers.put(k.toLowerCase(Locale.ROOT), v.get(0)));
            int n;
            synchronized (requests) {
                requests.add(new Recorded(ex.getRequestMethod(), ex.getRequestURI().toString(), headers, body));
                n = requests.size() - 1;
            }
            Scripted s = script.get(Math.min(n, script.size() - 1));
            byte[] out = s.json.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            ex.sendResponseHeaders(s.status, out.length);
            try (OutputStream os = ex.getResponseBody()) { os.write(out); }
        }

        Recorded req(int n) { return requests.get(n); }

        @Override
        public void close() { server.stop(0); }
    }

    // ------------------------------------------------------- fixture bodies

    static String userJson(String base, String status, String transitioning, String activated,
                           boolean activatable) {
        String links = activatable
                ? """
                  "self": {"href": "%B/api/v1/users/%U"},
                  "activate": {"href": "%B/api/v1/users/%U/lifecycle/activate", "method": "POST"},
                  "deactivate": {"href": "%B/api/v1/users/%U/lifecycle/deactivate", "method": "POST"},
                  "delete": {"href": "%B/api/v1/users/%U", "method": "DELETE"},
                  "schema": {"href": "%B/api/v1/meta/schemas/user/osc2f4qthitYGFq5S0g4"},
                  "type": {"href": "%B/api/v1/meta/types/user/oty2f4qthitYGFq5S0g4"}
                  """
                : """
                  "self": {"href": "%B/api/v1/users/%U"},
                  "suspend": {"href": "%B/api/v1/users/%U/lifecycle/suspend", "method": "POST"},
                  "deactivate": {"href": "%B/api/v1/users/%U/lifecycle/deactivate", "method": "POST"},
                  "resetPassword": {"href": "%B/api/v1/users/%U/lifecycle/reset_password", "method": "POST"},
                  "schema": {"href": "%B/api/v1/meta/schemas/user/osc2f4qthitYGFq5S0g4"},
                  "type": {"href": "%B/api/v1/meta/types/user/oty2f4qthitYGFq5S0g4"}
                  """;
        String t = transitioning == null ? "" : "\"transitioningToStatus\": \"" + transitioning + "\",";
        String act = activated == null ? "null" : "\"" + activated + "\"";
        return ("""
                {
                  "id": "%U",
                  "status": "%S",
                  %T
                  "created": "2026-07-10T16:02:31.000Z",
                  "activated": %A,
                  "statusChanged": "2026-07-10T16:02:31.000Z",
                  "lastLogin": null,
                  "profile": {
                    "firstName": "Ada",
                    "lastName": "Chen",
                    "email": "%L",
                    "login": "%L",
                    "department": "Platform Engineering"
                  },
                  "credentials": {"provider": {"type": "OKTA", "name": "OKTA"}},
                  "_links": {
                %K
                  }
                }
                """)
                .replace("%K", links)
                .replace("%T", t)
                .replace("%A", act)
                .replace("%S", status)
                .replace("%U", USER_ID)
                .replace("%L", LOGIN)
                .replace("%B", base);
    }

    static final String VALIDATION_400 = """
            {
              "errorCode": "E0000001",
              "errorSummary": "Api validation failed: login",
              "errorLink": "E0000001",
              "errorId": "oaeiCF8D5rLW6myqiPItW001",
              "errorCauses": [
                {"errorSummary": "login: An object with this field already exists in the current organization"},
                {"errorSummary": "email: Does not match required pattern"}
              ]
            }
            """;

    static final String PERMISSION_403 = """
            {
              "errorCode": "E0000006",
              "errorSummary": "You do not have permission to perform the requested action",
              "errorLink": "E0000006",
              "errorId": "oaeNUSD8fdkFd8fs8SDBK002",
              "errorCauses": []
            }
            """;

    static final String TRANSITION_403 = """
            {
              "errorCode": "E0000016",
              "errorSummary": "Activation failed because the user is already active",
              "errorLink": "E0000016",
              "errorId": "oaeMlLvGUjYD5v16vkYWY003",
              "errorCauses": []
            }
            """;

    static final String NOT_FOUND_404 = """
            {
              "errorCode": "E0000007",
              "errorSummary": "Not found: Resource not found: 00uMissing (User)",
              "errorLink": "E0000007",
              "errorId": "oaeMlLvGUjYD5v16vkYWY004",
              "errorCauses": []
            }
            """;

    static Map<String, String> newProfile() {
        Map<String, String> p = new java.util.LinkedHashMap<>();
        p.put("firstName", "Ada");
        p.put("lastName", "Chen");
        p.put("email", LOGIN);
        p.put("login", LOGIN);
        p.put("department", "Platform Engineering");
        return p;
    }

    // ---------------------------------------------------------------- tests

    static void testCreateStagedRequestShape() throws Exception {
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(200, userJson("BASE", "STAGED", null, null, true))))) {
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            OktaUser u = client.createStaged(newProfile());

            Recorded r = okta.req(0);
            checkEq(r.method(), "POST", "create must POST");
            String uri = r.rawUri();
            int qs = uri.indexOf('?');
            checkEq(qs < 0 ? uri : uri.substring(0, qs), "/api/v1/users", "create path");
            check(qs > 0, "create must send query parameters");
            List<String> params = List.of(uri.substring(qs + 1).split("&"));
            check(params.contains("activate=false"), "staged create must send activate=false (docs default is activate=true): " + uri);
            checkEq(r.headers().get("authorization"), "SSWS " + TOKEN, "Authorization must use the SSWS scheme");
            check(r.headers().getOrDefault("accept", "").contains("application/json"), "Accept: application/json required");
            check(r.headers().getOrDefault("content-type", "").contains("application/json"), "Content-Type: application/json required");

            Map<String, Object> body = Json.obj(Json.parse(r.body()));
            Map<String, Object> profile = Json.at(body, "profile");
            check(profile != null, "request body must nest fields under profile");
            checkEq(profile.get("firstName"), "Ada", "profile.firstName");
            checkEq(profile.get("lastName"), "Chen", "profile.lastName");
            checkEq(profile.get("email"), LOGIN, "profile.email");
            checkEq(profile.get("login"), LOGIN, "profile.login");
            checkEq(profile.get("department"), "Platform Engineering", "custom profile attributes must pass through");
            check(!body.containsKey("credentials"), "no credentials object when none supplied");
            check(!body.containsKey("activate"), "activate is a query parameter, not a body field");

            checkEq(u.id(), USER_ID, "user id parsed");
            checkEq(u.status(), "STAGED", "user created without activation must be STAGED");
            checkEq(u.created(), "2026-07-10T16:02:31.000Z", "created timestamp preserved");
            checkEq(u.profile().get("login"), LOGIN, "profile echoed");
            check(u.transitioningToStatus() == null, "no transition in flight after staged create");
        }
    }

    static void testHalLinksPreserved() throws Exception {
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(200, "")))) {
            String staged = userJson(okta.base, "STAGED", null, null, true);
            okta.script.set(0, new Scripted(200, staged));
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            OktaUser u = client.createStaged(newProfile());

            Map<String, String> links = u.links();
            checkEq(links.get("self"), okta.base + "/api/v1/users/" + USER_ID, "self link href verbatim");
            checkEq(links.get("activate"), okta.base + "/api/v1/users/" + USER_ID + "/lifecycle/activate",
                    "activate link href verbatim");
            checkEq(links.get("deactivate"), okta.base + "/api/v1/users/" + USER_ID + "/lifecycle/deactivate",
                    "deactivate link href verbatim");
            checkEq(links.get("delete"), okta.base + "/api/v1/users/" + USER_ID, "delete link href verbatim");
            checkEq(links.get("schema"), okta.base + "/api/v1/meta/schemas/user/osc2f4qthitYGFq5S0g4",
                    "schema link preserved even when the client has no use for it");
            checkEq(links.size(), 6, "every _links relation preserved, nothing invented");
        }
    }

    static void testActivateAndPollToActive() throws Exception {
        try (FakeOkta okta = new FakeOkta(new ArrayList<>(List.of(new Scripted(200, ""))))) {
            String b = okta.base;
            okta.script.clear();
            okta.script.add(new Scripted(200, """
                    {
                      "activationToken": "XE6wE17zmphl3KqAPFxO",
                      "activationUrl": "%B/welcome/XE6wE17zmphl3KqAPFxO"
                    }
                    """.replace("%B", b)));
            okta.script.add(new Scripted(200, userJson(b, "STAGED", "ACTIVE", null, true)));
            okta.script.add(new Scripted(200, userJson(b, "STAGED", "ACTIVE", null, true)));
            okta.script.add(new Scripted(200, userJson(b, "ACTIVE", null, "2026-07-10T16:09:00.000Z", false)));

            UserLifecycleClient client = new UserLifecycleClient(b, TOKEN);
            ActivationResult act = client.activate(USER_ID, false);

            Recorded r = okta.req(0);
            checkEq(r.method(), "POST", "activate must POST");
            String uri = r.rawUri();
            int qs = uri.indexOf('?');
            checkEq(qs < 0 ? uri : uri.substring(0, qs), "/api/v1/users/" + USER_ID + "/lifecycle/activate",
                    "activate is a lifecycle sub-resource");
            check(qs > 0 && List.of(uri.substring(qs + 1).split("&")).contains("sendEmail=false"),
                    "sendEmail=false must be explicit (docs default is true): " + uri);
            checkEq(act.activationToken(), "XE6wE17zmphl3KqAPFxO", "activation token parsed");
            checkEq(act.activationUrl(), b + "/welcome/XE6wE17zmphl3KqAPFxO", "activation url parsed");

            List<Integer> paced = new ArrayList<>();
            IntConsumer pacer = paced::add;
            OktaUser done = client.waitForStatus(USER_ID, "ACTIVE", 5, pacer);

            checkEq(okta.requests.size(), 4, "one activate POST plus exactly three status GETs");
            checkEq(okta.req(1).method(), "GET", "poll uses GET");
            String pollUri = okta.req(1).rawUri();
            int pq = pollUri.indexOf('?');
            checkEq(pq < 0 ? pollUri : pollUri.substring(0, pq), "/api/v1/users/" + USER_ID, "poll path");
            checkEq(paced, List.of(1, 2), "pacer called between consecutive polls only, with 1-based attempt");
            checkEq(done.status(), "ACTIVE", "terminal status returned");
            check(done.transitioningToStatus() == null, "transitioningToStatus clears when transition completes");
            checkEq(done.activated(), "2026-07-10T16:09:00.000Z", "activated timestamp surfaces once ACTIVE");
            check(done.links().containsKey("suspend") && !done.links().containsKey("activate"),
                    "links must track the returned document as the status changes");
        }
    }

    static void testPollTimeout() throws Exception {
        String staged = userJson("BASE", "STAGED", "ACTIVE", null, true);
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(200, staged)))) {
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            List<Integer> paced = new ArrayList<>();
            try {
                client.waitForStatus(USER_ID, "ACTIVE", 3, paced::add);
                check(false, "waitForStatus must throw once maxPolls GETs are spent");
            } catch (OktaTransitionTimeout e) {
                check(e.getMessage() != null && e.getMessage().contains(USER_ID),
                        "timeout message names the user id");
                check(e.getMessage().contains("ACTIVE"), "timeout message names the target status");
            }
            checkEq(okta.requests.size(), 3, "exactly maxPolls GETs, no extra request after giving up");
            checkEq(paced, List.of(1, 2), "no pacer call after the final poll");
        }
    }

    static void testValidationError() throws Exception {
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(400, VALIDATION_400)))) {
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            try {
                client.createStaged(newProfile());
                check(false, "400 E0000001 must raise the validation error type");
            } catch (OktaValidationException e) {
                checkEq(e.errorCode(), "E0000001", "validation errorCode");
                checkEq(e.status(), 400, "validation http status");
                checkEq(e.errorSummary(), "Api validation failed: login", "validation errorSummary verbatim");
                checkEq(e.errorId(), "oaeiCF8D5rLW6myqiPItW001", "errorId preserved for support escalation");
                checkEq(e.errorCauses(), List.of(
                        "login: An object with this field already exists in the current organization",
                        "email: Does not match required pattern"),
                        "every errorCauses[].errorSummary, in order");
            }
        }
    }

    static void testPermissionVsTransition() throws Exception {
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(403, PERMISSION_403)))) {
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            try {
                client.activate(USER_ID, false);
                check(false, "403 E0000006 must raise the permission error type");
            } catch (OktaPermissionException e) {
                checkEq(e.errorCode(), "E0000006", "permission errorCode");
                checkEq(e.status(), 403, "permission http status");
                check(e.getMessage() != null && !e.getMessage().contains(TOKEN),
                        "the SSWS token must never leak into exception messages");
            }
        }
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(403, TRANSITION_403)))) {
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            try {
                client.activate(USER_ID, false);
                check(false, "403 E0000016 must raise the lifecycle-transition error type");
            } catch (OktaTransitionException e) {
                checkEq(e.errorCode(), "E0000016", "transition errorCode");
                checkEq(e.errorSummary(), "Activation failed because the user is already active",
                        "transition errorSummary verbatim");
                Object asObject = e;
                check(!(asObject instanceof OktaPermissionException),
                        "E0000016 shares HTTP 403 with permission errors but is a different failure class");
            } catch (OktaPermissionException e) {
                check(false, "E0000016 must not be classified as a permission error just because it is a 403");
            }
        }
    }

    static void testOtherApiErrorsUseBaseType() throws Exception {
        try (FakeOkta okta = new FakeOkta(List.of(new Scripted(404, NOT_FOUND_404)))) {
            UserLifecycleClient client = new UserLifecycleClient(okta.base, TOKEN);
            try {
                client.getUser("00uMissing");
                check(false, "404 must raise");
            } catch (OktaApiException e) {
                check(!(e instanceof OktaValidationException) && !(e instanceof OktaPermissionException)
                        && !(e instanceof OktaTransitionException),
                        "unclassified Okta errors surface as the base OktaApiException");
                checkEq(e.errorCode(), "E0000007", "not-found errorCode preserved");
                checkEq(e.status(), 404, "not-found http status");
            }
        }
    }

    // ----------------------------------------------------------------- main

    public static void main(String[] args) throws Exception {
        Locale.setDefault(Locale.ROOT);
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"));

        testCreateStagedRequestShape();
        testHalLinksPreserved();
        testActivateAndPollToActive();
        testPollTimeout();
        testValidationError();
        testPermissionVsTransition();
        testOtherApiErrorsUseBaseType();

        System.out.println("OK — " + checks + " checks passed");
    }
}
