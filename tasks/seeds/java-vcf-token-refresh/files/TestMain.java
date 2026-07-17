import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Acceptance harness for the VCF token layer: a loopback fake SDDC Manager
 * speaking the Tokens + Domains contract pinned in docs/contract.json.
 * No real appliance, no real credentials. Protected — do not modify.
 */
public final class TestMain {

    static final String USER = "svc-inventory";
    static final String PASS = "dummy-pass-4e81c903"; // dummy; must never leak
    static final String REFRESH_ID = "rt-77f0c3-2b94-dummy";

    static int checks = 0;

    static void check(boolean cond, String label) {
        checks++;
        if (!cond) {
            throw new AssertionError("FAIL: " + label);
        }
    }

    // ------------------------------------------------------------ fake SDDC

    static final class Req {
        final String method;
        final String path;
        final String auth;
        final String contentType;
        final String body;

        Req(String method, String path, String auth, String contentType, String body) {
            this.method = method;
            this.path = path;
            this.auth = auth;
            this.contentType = contentType;
            this.body = body;
        }
    }

    static final class FakeSddc {
        final List<Req> requests = Collections.synchronizedList(new ArrayList<>());
        final Set<String> validTokens = Collections.synchronizedSet(new HashSet<>());
        final List<String> issuedTokens = Collections.synchronizedList(new ArrayList<>());
        final AtomicInteger tokenCounter = new AtomicInteger();
        volatile boolean refreshValid = true;
        volatile boolean forbidden = false;
        HttpServer server;

        String issueToken() {
            String token = "at-" + tokenCounter.incrementAndGet() + "-9f27c40e5b1d";
            issuedTokens.add(token);
            validTokens.add(token);
            return token;
        }

        void expireAccessTokens() {
            validTokens.clear();
        }

        long count(String method, String path) {
            synchronized (requests) {
                return requests.stream()
                        .filter(r -> r.method.equals(method) && r.path.equals(path))
                        .count();
            }
        }

        Req last(String method, String path) {
            synchronized (requests) {
                for (int i = requests.size() - 1; i >= 0; i--) {
                    Req r = requests.get(i);
                    if (r.method.equals(method) && r.path.equals(path)) {
                        return r;
                    }
                }
            }
            return null;
        }
    }

    static final String DOMAINS_BODY = Json.write(Map.of(
            "elements", List.of(
                    orderedMap("id", "5f2ab904-1c3e-4d87-b6a0-92e4c7f1d358",
                            "name", "sfo-m01", "type", "MANAGEMENT", "status", "ACTIVE"),
                    orderedMap("id", "8c1b6e27-40d5-4f9a-a3b8-07d92c5e614f",
                            "name", "sfo-w01", "type", "VI", "status", "ACTIVE")),
            "pageMetadata", orderedMap("pageNumber", 0, "pageSize", 2,
                    "totalElements", 2, "totalPages", 1)));

    static Map<String, Object> orderedMap(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    static String errorBody(String code, String message, String token) {
        return Json.write(orderedMap("errorCode", code, "message", message, "referenceToken", token));
    }

    static void respond(HttpExchange ex, int status, String body) throws IOException {
        if (body == null) {
            ex.sendResponseHeaders(status, -1);
            ex.close();
            return;
        }
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    static FakeSddc startServer() throws IOException {
        FakeSddc fake = new FakeSddc();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String auth = ex.getRequestHeaders().getFirst("Authorization");
            String ctype = ex.getRequestHeaders().getFirst("Content-Type");
            String method = ex.getRequestMethod();
            String path = ex.getRequestURI().getPath();
            fake.requests.add(new Req(method, path, auth, ctype, body));

            try {
                if (method.equals("POST") && path.equals("/v1/tokens")) {
                    Map<String, Object> spec = Json.object(Json.parse(body));
                    if (!USER.equals(spec.get("username")) || !PASS.equals(spec.get("password"))) {
                        respond(ex, 400, errorBody("INVALID_CREDENTIALS",
                                "Invalid credentials provided", "L0G1N1"));
                        return;
                    }
                    String token = fake.issueToken();
                    respond(ex, 201, Json.write(orderedMap(
                            "accessToken", token,
                            "refreshToken", orderedMap("id", REFRESH_ID))));
                } else if (method.equals("PATCH") && path.equals("/v1/tokens/access-token/refresh")) {
                    if (!("\"" + REFRESH_ID + "\"").equals(body)) {
                        respond(ex, 400, errorBody("BAD_REQUEST",
                                "Request body must be the refresh token id as a JSON string", "R3FR01"));
                        return;
                    }
                    if (!fake.refreshValid) {
                        respond(ex, 404, errorBody("REFRESH_TOKEN_NOT_FOUND",
                                "The refresh token is unknown or revoked", "R3FR44"));
                        return;
                    }
                    fake.expireAccessTokens();
                    String token = fake.issueToken();
                    respond(ex, 200, "\"" + token + "\"");
                } else if (method.equals("DELETE") && path.equals("/v1/tokens/refresh-token")) {
                    if (!("\"" + REFRESH_ID + "\"").equals(body)) {
                        respond(ex, 400, errorBody("BAD_REQUEST",
                                "Request body must be the refresh token id as a JSON string", "R3VK01"));
                        return;
                    }
                    fake.refreshValid = false;
                    respond(ex, 204, null);
                } else if (method.equals("GET") && path.equals("/v1/domains")) {
                    String expectedPrefix = "Bearer ";
                    if (auth == null || !auth.startsWith(expectedPrefix)
                            || !fake.validTokens.contains(auth.substring(expectedPrefix.length()))) {
                        respond(ex, 401, errorBody("UNAUTHORIZED", "Authentication required", "AUTH01"));
                        return;
                    }
                    if (fake.forbidden) {
                        respond(ex, 403, errorBody("FORBIDDEN",
                                "The token role does not allow this operation", "F0RB1D"));
                        return;
                    }
                    respond(ex, 200, DOMAINS_BODY);
                } else {
                    respond(ex, 404, errorBody("NOT_FOUND", "No such operation", "N0R0UT"));
                }
            } catch (RuntimeException e) {
                respond(ex, 500, errorBody("VCF_SYSTEM_ERROR", "fake server error: " + e, "SRV500"));
            }
        });
        server.start();
        fake.server = server;
        return fake;
    }

    // ------------------------------------------------------------ tests

    public static void main(String[] args) throws Exception {
        for (String doc : new String[] {"docs/contract.json", "docs/official_sources.json"}) {
            Json.parse(java.nio.file.Files.readString(java.nio.file.Path.of(doc)));
            check(true, doc + " parses");
        }

        FakeSddc fake = startServer();
        String base = "http://127.0.0.1:" + fake.server.getAddress().getPort();
        HttpClient http = HttpClient.newHttpClient();
        List<String> log = Collections.synchronizedList(new ArrayList<>());
        List<String> exceptionMessages = Collections.synchronizedList(new ArrayList<>());

        try {
            // -- existing behavior: static-token transport keeps working ----
            String legacyToken = fake.issueToken();
            DomainsClient legacy = new DomainsClient(new SddcHttpClient(base, http, () -> legacyToken));
            List<DomainsClient.Domain> domains = legacy.listDomains();
            check(domains.size() == 2, "legacy transport lists both domains");
            check(domains.get(0).id().equals("5f2ab904-1c3e-4d87-b6a0-92e4c7f1d358"),
                    "legacy transport preserves API resource ids");
            check(domains.get(1).name().equals("sfo-w01") && domains.get(1).type().equals("VI"),
                    "legacy transport preserves domain fields");

            // -- login ------------------------------------------------------
            TokenManager tm = TokenManager.connect(base, USER, PASS, http, log::add);
            check(fake.count("POST", "/v1/tokens") == 1, "connect performs exactly one token POST");
            Req loginReq = fake.last("POST", "/v1/tokens");
            check(loginReq.contentType != null && loginReq.contentType.startsWith("application/json"),
                    "token POST content-type is application/json");
            Map<String, Object> sentSpec = Json.object(Json.parse(loginReq.body));
            check(sentSpec.size() == 2 && USER.equals(sentSpec.get("username"))
                            && PASS.equals(sentSpec.get("password")),
                    "TokenCreationSpec carries exactly username and password");

            DomainsClient dc = new DomainsClient(tm);
            domains = dc.listDomains();
            check(domains.size() == 2, "token manager transport lists domains");
            Req domReq = fake.last("GET", "/v1/domains");
            check(domReq.auth != null && domReq.auth.equals("Bearer " + fake.issuedTokens.get(1)),
                    "data request carries the freshly created bearer token");

            // -- wrong password never leaks ----------------------------------
            String wrongPass = "dummy-wrong-b2c473aa";
            try {
                TokenManager.connect(base, USER, wrongPass, http, log::add);
                check(false, "wrong password must raise");
            } catch (VcfApiException e) {
                exceptionMessages.add(String.valueOf(e.getMessage()));
                check(e.statusCode() == 400, "failed login surfaces the documented 400");
                check(e.getMessage() != null && !e.getMessage().contains(wrongPass),
                        "failed-login exception must not contain the password");
            }

            // -- refresh once on expiry --------------------------------------
            long getsBefore = fake.count("GET", "/v1/domains");
            fake.expireAccessTokens();
            domains = dc.listDomains();
            check(domains.size() == 2, "expired token is refreshed transparently");
            check(fake.count("PATCH", "/v1/tokens/access-token/refresh") == 1,
                    "exactly one refresh PATCH after expiry");
            Req refreshReq = fake.last("PATCH", "/v1/tokens/access-token/refresh");
            check(("\"" + REFRESH_ID + "\"").equals(refreshReq.body),
                    "refresh body is the refresh token id as a bare JSON string");
            check(refreshReq.contentType != null && refreshReq.contentType.startsWith("application/json"),
                    "refresh content-type is application/json");
            check(fake.count("GET", "/v1/domains") == getsBefore + 2,
                    "the 401'd request is retried exactly once");
            Req retried = fake.last("GET", "/v1/domains");
            check(retried.auth.equals("Bearer " + fake.issuedTokens.get(fake.issuedTokens.size() - 1)),
                    "the retry carries the rotated token");

            // -- 403 is not an auth-refresh situation -------------------------
            long patchesBefore = fake.count("PATCH", "/v1/tokens/access-token/refresh");
            fake.forbidden = true;
            try {
                dc.listDomains();
                check(false, "403 must raise");
            } catch (VcfApiException e) {
                exceptionMessages.add(String.valueOf(e.getMessage()));
                check(e.statusCode() == 403, "403 surfaces its status");
                check(!(e instanceof VcfAuthException), "403 is not an auth failure");
            }
            fake.forbidden = false;
            check(fake.count("PATCH", "/v1/tokens/access-token/refresh") == patchesBefore,
                    "403 must not trigger a token refresh");

            // -- concurrent expiry: refresh is serialized ----------------------
            fake.expireAccessTokens();
            patchesBefore = fake.count("PATCH", "/v1/tokens/access-token/refresh");
            int workers = 4;
            CountDownLatch ready = new CountDownLatch(workers);
            CountDownLatch go = new CountDownLatch(1);
            List<Throwable> failures = Collections.synchronizedList(new ArrayList<>());
            AtomicInteger okCount = new AtomicInteger();
            List<Thread> threads = new ArrayList<>();
            for (int i = 0; i < workers; i++) {
                Thread t = new Thread(() -> {
                    ready.countDown();
                    try {
                        go.await(10, TimeUnit.SECONDS);
                        if (dc.listDomains().size() == 2) {
                            okCount.incrementAndGet();
                        }
                    } catch (Throwable e) {
                        failures.add(e);
                    }
                });
                t.start();
                threads.add(t);
            }
            check(ready.await(10, TimeUnit.SECONDS), "workers ready");
            go.countDown();
            for (Thread t : threads) {
                t.join(15_000);
                check(!t.isAlive(), "worker finished in time");
            }
            check(failures.isEmpty(), "no worker failed during concurrent refresh: " + failures);
            check(okCount.get() == workers, "every concurrent caller got a full result");
            check(fake.count("PATCH", "/v1/tokens/access-token/refresh") == patchesBefore + 1,
                    "concurrent 401s produce exactly one refresh on the wire");

            // -- unrecoverable refresh ----------------------------------------
            fake.expireAccessTokens();
            fake.refreshValid = false;
            try {
                dc.listDomains();
                check(false, "revoked refresh token must raise VcfAuthException");
            } catch (VcfAuthException e) {
                exceptionMessages.add(String.valueOf(e.getMessage()));
                check(true, "revoked refresh raises VcfAuthException");
            }
            fake.refreshValid = true;

            // -- shutdown -----------------------------------------------------
            tm.close();
            check(fake.count("DELETE", "/v1/tokens/refresh-token") == 1,
                    "close invalidates the refresh token once");
            Req del = fake.last("DELETE", "/v1/tokens/refresh-token");
            check(("\"" + REFRESH_ID + "\"").equals(del.body),
                    "invalidate body is the refresh token id as a JSON string");
            check(!fake.refreshValid, "server side refresh token revoked");
            try {
                tm.get("/v1/domains");
                check(false, "get() after close must raise IllegalStateException");
            } catch (IllegalStateException e) {
                exceptionMessages.add(String.valueOf(e.getMessage()));
                check(true, "closed manager refuses requests");
            }
            tm.close();
            check(fake.count("DELETE", "/v1/tokens/refresh-token") == 1,
                    "close is idempotent, no second DELETE");

            // -- secrecy ------------------------------------------------------
            boolean sawRefreshLog = false;
            List<String> secrets = new ArrayList<>(fake.issuedTokens);
            secrets.add(PASS);
            secrets.add(REFRESH_ID);
            List<String> lines = new ArrayList<>(log);
            for (String line : lines) {
                if (line != null && line.toLowerCase().contains("refresh")) {
                    sawRefreshLog = true;
                }
                for (String secret : secrets) {
                    check(line == null || !line.contains(secret),
                            "log line must not contain token material: " + describeLeak(line));
                }
            }
            check(!lines.isEmpty(), "the injected logger is actually used");
            check(sawRefreshLog, "refreshes are visible in the injected log");
            for (String msg : exceptionMessages) {
                for (String secret : secrets) {
                    check(msg == null || !msg.contains(secret),
                            "exception message must not contain token material: " + describeLeak(msg));
                }
            }
        } finally {
            fake.server.stop(0);
        }

        System.out.println("all " + checks + " checks passed");
    }

    private static String describeLeak(String line) {
        // never echo the offending secret back into test output
        return line == null ? "null" : "line of length " + line.length();
    }
}
