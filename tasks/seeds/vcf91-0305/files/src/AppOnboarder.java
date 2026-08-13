import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Dependency-free, single-file client that onboards a multi-tier application
 * into VCF Operations for Networks 9.1.
 *
 * The REST surface is pinned by docs/contract.json, which is derived from
 * specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml in the
 * vmware/vcf-api-specs repository. Operations used:
 *
 *   create                POST   /api/ni/auth/token
 *   addApplication        POST   /api/ni/groups/applications
 *   addTier               POST   /api/ni/groups/applications/{id}/tiers
 *   listApplicationTiers  GET    /api/ni/groups/applications/{id}/tiers
 *   delete                DELETE /api/ni/auth/token
 */
public final class AppOnboarder {

    static final String BASE_PATH = "/api/ni";

    /** Exit codes. */
    static final int EXIT_OK = 0;
    static final int EXIT_PARTIAL = 3;
    static final int EXIT_ABORTED = 4;

    private final String baseUrl;
    private final HttpClient http;
    private String token;

    AppOnboarder(String baseUrl) {
        this.baseUrl = baseUrl.endsWith("/")
                ? baseUrl.substring(0, baseUrl.length() - 1)
                : baseUrl;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    public static void main(String[] args) throws Exception {
        System.exit(run(args));
    }

    /**
     * args: baseUrl configPath reportPath username password
     */
    public static int run(String[] args) throws Exception {
        if (args.length < 5) {
            System.err.println(
                    "usage: AppOnboarder <baseUrl> <configPath> <reportPath> <username> <password>");
            return EXIT_ABORTED;
        }
        AppOnboarder client = new AppOnboarder(args[0]);
        return client.onboard(Path.of(args[1]), Path.of(args[2]), args[3], args[4]);
    }

    // ------------------------------------------------------------- onboarding

    @SuppressWarnings("unchecked")
    int onboard(Path configPath, Path reportPath, String username, String password)
            throws IOException, InterruptedException {

        Map<String, Object> config = (Map<String, Object>) Json.parse(
                Files.readString(configPath, StandardCharsets.UTF_8));
        Map<String, Object> appSpec = (Map<String, Object>) config.get("application");
        List<Object> tierSpecs = (List<Object>) config.get("tiers");
        String appName = (String) appSpec.get("name");

        Report report = new Report(appName);

        try {
            login(username, password);
        } catch (ApiFailure f) {
            report.applicationCreated = false;
            report.outcome = "aborted";
            report.abortReason = "authentication failed: " + f.message;
            for (Object t : tierSpecs) {
                report.tiers.add(TierResult.notAttempted(tierName(t)));
            }
            report.write(reportPath);
            return EXIT_ABORTED;
        }

        try {
            String appId;
            try {
                appId = addApplication(appName);
            } catch (ApiFailure f) {
                report.applicationCreated = false;
                report.outcome = "aborted";
                report.abortReason = "addApplication failed: " + f.message;
                for (Object t : tierSpecs) {
                    report.tiers.add(TierResult.notAttempted(tierName(t)));
                }
                report.write(reportPath);
                return EXIT_ABORTED;
            }

            report.applicationCreated = true;
            report.applicationEntityId = appId;

            for (Object t : tierSpecs) {
                Map<String, Object> tier = (Map<String, Object>) t;
                String name = (String) tier.get("name");
                try {
                    String tierId = addTier(appId, tier);
                    report.tiers.add(TierResult.created(name, tierId));
                } catch (ApiFailure f) {
                    report.tiers.add(TierResult.failed(name, f));
                }
                report.serverTiers.add(name);
            }

            report.reconciled = true;
            report.outcome = "succeeded";
            report.write(reportPath);
            return EXIT_OK;
        } finally {
            logoutQuietly();
        }
    }

    @SuppressWarnings("unchecked")
    private static String tierName(Object tierSpec) {
        return (String) ((Map<String, Object>) tierSpec).get("name");
    }

    // ------------------------------------------------------------ operations

    /** operationId: create — POST /api/ni/auth/token */
    void login(String username, String password) throws IOException, InterruptedException {
        Json.Obj body = Json.obj()
                .put("username", username)
                .put("password", password)
                .put("domain", Json.obj()
                        .put("domain_type", "")
                        .put("value", ""));

        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + BASE_PATH + "/auth/token"))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body.build(), StandardCharsets.UTF_8))
                .build();

        HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (res.statusCode() != 200) {
            throw ApiFailure.from("create", res);
        }
        Map<String, Object> parsed = Json.asObject(Json.parse(res.body()));
        Object t = parsed == null ? null : parsed.get("token");
        if (!(t instanceof String) || ((String) t).isEmpty()) {
            throw new ApiFailure("create", res.statusCode(), null,
                    "token missing from Token response");
        }
        this.token = (String) t;
    }

    /** operationId: addApplication — POST /api/ni/groups/applications */
    String addApplication(String name) throws IOException, InterruptedException {
        String body = Json.obj().put("name", name).build();

        HttpRequest req = authed(
                HttpRequest.newBuilder(URI.create(baseUrl + BASE_PATH + "/groups/applications")))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build();

        HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (res.statusCode() != 201) {
            throw ApiFailure.from("addApplication", res);
        }
        Map<String, Object> app = Json.asObject(Json.parse(res.body()));
        Object id = app == null ? null : app.get("entity_id");
        if (!(id instanceof String) || ((String) id).isEmpty()) {
            throw new ApiFailure("addApplication", res.statusCode(), null,
                    "entity_id missing from Application response");
        }
        return (String) id;
    }

    /** operationId: addTier — POST /api/ni/groups/applications/{id}/tiers */
    @SuppressWarnings("unchecked")
    String addTier(String appId, Map<String, Object> tierSpec)
            throws IOException, InterruptedException {

        Json.Obj body = Json.obj()
                .put("name", (String) tierSpec.get("name"))
                .put("entity_id", "");

        Json.Arr criteria = Json.arr();
        Map<String, Object> search = (Map<String, Object>) tierSpec.get("search_membership");
        if (search != null) {
            criteria.add(Json.obj()
                    .put("membership_type", "SearchMembershipCriteria")
                    .put("search_membership_criteria", Json.obj()
                            .put("entity_type", (String) search.get("entity_type"))
                            .put("filter", (String) search.get("filter"))));
        }
        Map<String, Object> ip = (Map<String, Object>) tierSpec.get("ip_membership");
        if (ip != null) {
            Json.Arr addresses = Json.arr();
            for (Object a : (List<Object>) ip.get("ip_addresses")) {
                addresses.add((String) a);
            }
            criteria.add(Json.obj()
                    .put("membership_type", "IPAddressMembershipCriteria")
                    .put("ip_address_membership_criteria", Json.obj()
                            .put("ip_addresses", addresses)));
        }
        body.put("group_membership_criteria", criteria);
        body.put("member_list", Json.obj());
        body.put("source_group_entity_id", Json.arr());

        URI uri = URI.create(baseUrl + BASE_PATH + "/groups/applications/"
                + encodePathSegment(appId) + "/tiers");
        HttpRequest req = authed(HttpRequest.newBuilder(uri))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body.build(), StandardCharsets.UTF_8))
                .build();

        HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (res.statusCode() != 201) {
            throw ApiFailure.from("addTier", res);
        }
        Map<String, Object> tier = Json.asObject(Json.parse(res.body()));
        Object id = tier == null ? null : tier.get("entity_id");
        return (id instanceof String) ? (String) id : null;
    }

    /** operationId: listApplicationTiers — GET /api/ni/groups/applications/{id}/tiers */
    @SuppressWarnings("unchecked")
    List<String> listApplicationTiers(String appId) throws IOException, InterruptedException {
        URI uri = URI.create(baseUrl + BASE_PATH + "/groups/applications/"
                + encodePathSegment(appId) + "/tiers");
        HttpRequest req = authed(HttpRequest.newBuilder(uri)).GET().build();

        HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (res.statusCode() != 200) {
            throw ApiFailure.from("listApplicationTiers", res);
        }
        Map<String, Object> parsed = Json.asObject(Json.parse(res.body()));
        List<String> names = new ArrayList<>();
        Object results = parsed == null ? null : parsed.get("results");
        if (results instanceof List) {
            for (Object o : (List<Object>) results) {
                Map<String, Object> tier = Json.asObject(o);
                if (tier != null && tier.get("name") instanceof String) {
                    names.add((String) tier.get("name"));
                }
            }
        }
        return names;
    }

    /** operationId: delete — DELETE /api/ni/auth/token */
    void logout() throws IOException, InterruptedException {
        if (token == null) {
            return;
        }
        HttpRequest req = authed(
                HttpRequest.newBuilder(URI.create(baseUrl + BASE_PATH + "/auth/token")))
                .DELETE()
                .build();
        HttpResponse<String> res = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (res.statusCode() != 204) {
            throw ApiFailure.from("delete", res);
        }
        token = null;
    }

    private void logoutQuietly() {
        try {
            logout();
        } catch (ApiFailure | IOException e) {
            System.err.println("warning: token revocation failed: " + e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private HttpRequest.Builder authed(HttpRequest.Builder b) {
        return b.header("Authorization", "NetworkInsight " + token)
                .header("Accept", "application/json")
                .timeout(Duration.ofSeconds(20));
    }

    private static String encodePathSegment(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8).replace("+", "%20");
    }

    // --------------------------------------------------------------- failures

    /** A non-success response from a contract operation. */
    static final class ApiFailure extends RuntimeException {
        final String operationId;
        final int httpStatus;
        final Integer errorCode;
        final String message;

        ApiFailure(String operationId, int httpStatus, Integer errorCode, String message) {
            super(operationId + " -> HTTP " + httpStatus + ": " + message);
            this.operationId = operationId;
            this.httpStatus = httpStatus;
            this.errorCode = errorCode;
            this.message = message;
        }

        /** Decodes the spec's ApiError body when the server sent one. */
        static ApiFailure from(String operationId, HttpResponse<String> res) {
            Integer code = null;
            String message = null;
            Map<String, Object> err = Json.asObject(Json.parse(res.body()));
            if (err != null) {
                if (err.get("code") instanceof Long) {
                    code = (int) (long) (Long) err.get("code");
                }
                if (err.get("message") instanceof String) {
                    message = (String) err.get("message");
                }
            }
            if (message == null || message.isEmpty()) {
                message = "HTTP " + res.statusCode();
            }
            return new ApiFailure(operationId, res.statusCode(), code, message);
        }
    }

    // ----------------------------------------------------------------- report

    static final class TierResult {
        String name;
        String status;
        String entityId;
        Integer httpStatus;
        Integer errorCode;
        String errorMessage;

        static TierResult created(String name, String entityId) {
            TierResult r = new TierResult();
            r.name = name;
            r.status = "created";
            r.entityId = entityId;
            return r;
        }

        static TierResult failed(String name, ApiFailure f) {
            TierResult r = new TierResult();
            r.name = name;
            r.status = "failed";
            r.httpStatus = f.httpStatus;
            r.errorCode = f.errorCode;
            r.errorMessage = f.message;
            return r;
        }

        static TierResult notAttempted(String name) {
            TierResult r = new TierResult();
            r.name = name;
            r.status = "not_attempted";
            return r;
        }

        Json.Obj toJson() {
            Json.Obj o = Json.obj().put("name", name).put("status", status);
            if (entityId != null) o.put("entity_id", entityId);
            if (httpStatus != null) o.put("http_status", httpStatus);
            if (errorCode != null) o.put("error_code", errorCode);
            if (errorMessage != null) o.put("error_message", errorMessage);
            return o;
        }
    }

    static final class Report {
        final String applicationName;
        boolean applicationCreated;
        String applicationEntityId;
        final List<TierResult> tiers = new ArrayList<>();
        List<String> serverTiers = new ArrayList<>();
        boolean reconciled;
        String reconcileError;
        String outcome = "unknown";
        String failedAt;
        String abortReason;

        Report(String applicationName) {
            this.applicationName = applicationName;
        }

        void write(Path path) throws IOException {
            Json.Obj app = Json.obj()
                    .put("name", applicationName)
                    .put("created", applicationCreated);
            if (applicationEntityId != null) {
                app.put("entity_id", applicationEntityId);
            }

            Json.Arr tierArr = Json.arr();
            for (TierResult t : tiers) {
                tierArr.add(t.toJson());
            }
            Json.Arr serverArr = Json.arr();
            for (String n : serverTiers) {
                serverArr.add(n);
            }

            Json.Obj root = Json.obj()
                    .put("application", app)
                    .put("tiers", tierArr)
                    .put("server_tiers", serverArr)
                    .put("reconciled", reconciled)
                    .put("outcome", outcome);
            if (failedAt != null) root.put("failed_at", failedAt);
            if (abortReason != null) root.put("abort_reason", abortReason);
            if (reconcileError != null) root.put("reconcile_error", reconcileError);

            Path parent = path.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            Files.writeString(path, root.build() + "\n", StandardCharsets.UTF_8);
        }
    }

    // ------------------------------------------------------------------- json

    /**
     * Minimal JSON support. The builders serialize exactly the members that were
     * added: there is no implicit key for an absent value.
     */
    static final class Json {

        static Obj obj() {
            return new Obj();
        }

        static Arr arr() {
            return new Arr();
        }

        /** A JSON object under construction. */
        static final class Obj {
            private final Map<String, String> members = new LinkedHashMap<>();

            Obj put(String key, String value) {
                members.put(key, str(value));
                return this;
            }

            Obj put(String key, boolean value) {
                members.put(key, Boolean.toString(value));
                return this;
            }

            Obj put(String key, int value) {
                members.put(key, Integer.toString(value));
                return this;
            }

            Obj put(String key, Obj value) {
                members.put(key, value.build());
                return this;
            }

            Obj put(String key, Arr value) {
                members.put(key, value.build());
                return this;
            }

            String build() {
                StringBuilder sb = new StringBuilder("{");
                boolean first = true;
                for (Map.Entry<String, String> e : members.entrySet()) {
                    if (!first) sb.append(',');
                    first = false;
                    sb.append(str(e.getKey())).append(':').append(e.getValue());
                }
                return sb.append('}').toString();
            }
        }

        /** A JSON array under construction. */
        static final class Arr {
            private final List<String> items = new ArrayList<>();

            Arr add(String value) {
                items.add(str(value));
                return this;
            }

            Arr add(Obj value) {
                items.add(value.build());
                return this;
            }

            boolean isEmpty() {
                return items.isEmpty();
            }

            String build() {
                return "[" + String.join(",", items) + "]";
            }
        }

        static String str(String s) {
            StringBuilder sb = new StringBuilder("\"");
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\r': sb.append("\\r"); break;
                    case '\t': sb.append("\\t"); break;
                    case '\b': sb.append("\\b"); break;
                    case '\f': sb.append("\\f"); break;
                    default:
                        if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            return sb.append('"').toString();
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> asObject(Object o) {
            return (o instanceof Map) ? (Map<String, Object>) o : null;
        }

        static Object parse(String s) {
            try {
                P p = new P(s);
                p.ws();
                Object v = p.value();
                p.ws();
                return p.i == p.s.length() ? v : null;
            } catch (RuntimeException e) {
                return null;
            }
        }

        private static final class P {
            final String s;
            int i;

            P(String s) { this.s = s; }

            void ws() {
                while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++;
            }

            char peek() {
                if (i >= s.length()) throw new IllegalStateException("eof");
                return s.charAt(i);
            }

            Object value() {
                ws();
                char c = peek();
                if (c == '{') return object();
                if (c == '[') return array();
                if (c == '"') return string();
                if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
                if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
                if (s.startsWith("null", i)) { i += 4; return null; }
                return number();
            }

            Map<String, Object> object() {
                Map<String, Object> m = new LinkedHashMap<>();
                i++;
                ws();
                if (peek() == '}') { i++; return m; }
                while (true) {
                    ws();
                    String k = string();
                    ws();
                    if (peek() != ':') throw new IllegalStateException("expected :");
                    i++;
                    m.put(k, value());
                    ws();
                    char c = peek();
                    if (c == ',') { i++; continue; }
                    if (c == '}') { i++; return m; }
                    throw new IllegalStateException("expected , or }");
                }
            }

            List<Object> array() {
                List<Object> l = new ArrayList<>();
                i++;
                ws();
                if (peek() == ']') { i++; return l; }
                while (true) {
                    l.add(value());
                    ws();
                    char c = peek();
                    if (c == ',') { i++; continue; }
                    if (c == ']') { i++; return l; }
                    throw new IllegalStateException("expected , or ]");
                }
            }

            String string() {
                if (peek() != '"') throw new IllegalStateException("expected string");
                i++;
                StringBuilder sb = new StringBuilder();
                while (true) {
                    char c = s.charAt(i++);
                    if (c == '"') return sb.toString();
                    if (c == '\\') {
                        char e = s.charAt(i++);
                        switch (e) {
                            case 'n': sb.append('\n'); break;
                            case 't': sb.append('\t'); break;
                            case 'r': sb.append('\r'); break;
                            case 'b': sb.append('\b'); break;
                            case 'f': sb.append('\f'); break;
                            case 'u':
                                sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                                i += 4;
                                break;
                            default: sb.append(e);
                        }
                    } else {
                        sb.append(c);
                    }
                }
            }

            Object number() {
                int start = i;
                while (i < s.length() && "+-.eE0123456789".indexOf(s.charAt(i)) >= 0) i++;
                String n = s.substring(start, i);
                if (n.isEmpty()) throw new IllegalStateException("bad number");
                if (n.contains(".") || n.contains("e") || n.contains("E")) {
                    return Double.parseDouble(n);
                }
                return Long.parseLong(n);
            }
        }
    }
}
