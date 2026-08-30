/*
 * Protected acceptance harness for the VCF Automation 9.1 Project Service
 * client. The loopback server derives its two allowed routes from
 * docs/contract.json, records every request, and alternates the order of the
 * content array on successive collection responses. It never contacts a live
 * VMware endpoint.
 *
 * Run: java TestMain.java
 */
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class TestMain {
    static final String AUTH = "Bearer fixture-vcfa-token-0342";
    static int checks;

    static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
        checks++;
    }

    static void checkEq(Object actual, Object expected, String what) {
        if (actual == null ? expected != null : !actual.equals(expected)) {
            throw new AssertionError(what + ": got " + actual + ", expected " + expected);
        }
        checks++;
    }

    static void fail(String message) {
        throw new AssertionError(message);
    }

    // --------------------------------------------------------- pinned contract
    record Operation(String key, String name, String method, String path) {}

    record Contract(Operation list, Operation create, String apiVersion) {
        static Contract load() throws IOException {
            Map<String, Object> root = Json.obj(Json.parse(
                    Files.readString(Path.of("docs", "contract.json"), StandardCharsets.UTF_8)));
            checkEq(Json.str(root.get("product_version")), "9.1", "contract product version");
            String basis = Json.str(root.get("source_basis"));
            check(basis != null && basis.contains("reference documentation")
                            && basis.contains("not a published API specification"),
                    "contract must plainly identify its reference-documentation source");

            Map<String, Object> operations = Json.obj(root.get("operations"));
            checkEq(operations.size(), 2, "contract operation count");
            check(operations.containsKey("list_projects"), "contract names list_projects");
            check(operations.containsKey("create_project"), "contract names create_project");
            Operation list = operation("list_projects", Json.obj(operations.get("list_projects")));
            Operation create = operation("create_project", Json.obj(operations.get("create_project")));
            checkEq(list.name(), "Get All Projects", "list operation name");
            checkEq(list.method(), "GET", "list operation method");
            checkEq(list.path(), "/project-service/api/projects", "list operation path");
            checkEq(create.name(), "Create", "create operation name");
            checkEq(create.method(), "POST", "create operation method");
            checkEq(create.path(), list.path(), "create operation path");
            checkEq(((Number) Json.obj(Json.obj(operations.get("list_projects"))
                    .get("success")).get("status")).intValue(), 200, "list success status");
            checkEq(((Number) Json.obj(Json.obj(operations.get("create_project"))
                    .get("success")).get("status")).intValue(), 201, "create success status");
            checkEq(Json.str(root.get("api_version")), "2019-01-15", "pinned API version");
            return new Contract(list, create, Json.str(root.get("api_version")));
        }

        private static Operation operation(String key, Map<String, Object> value) {
            return new Operation(key, Json.str(value.get("operation_name")),
                    Json.str(value.get("method")), Json.str(value.get("path")));
        }
    }

    static void verifyOfficialSources() throws IOException {
        Map<String, Object> root = Json.obj(Json.parse(
                Files.readString(Path.of("docs", "official_sources.json"), StandardCharsets.UTF_8)));
        checkEq(Json.str(root.get("product_version")), "9.1", "source product version");
        String basis = Json.str(root.get("source_basis"));
        check(basis != null && basis.contains("not a published API specification"),
                "source provenance distinguishes reference docs from a specification");
        List<Object> sources = Json.arr(root.get("sources"));
        checkEq(sources.size(), 4, "official source page count");
        for (Object item : sources) {
            Map<String, Object> source = Json.obj(item);
            String url = Json.str(source.get("url"));
            check(url != null && url.startsWith(
                            "https://developer.broadcom.com/xapis/all-apps-org-projects/9.1/"),
                    "every provenance URL is a pinned official Broadcom 9.1 xAPIs page");
            check(Json.str(source.get("operation")) != null
                            && !Json.str(source.get("operation")).isBlank(),
                    "every provenance page records the operation it documents");
            checkEq(Json.str(source.get("fetched_on")), "2026-08-16",
                    "every provenance page records its fetch date");
        }
    }

    // ---------------------------------------------------------- loopback mock
    record Req(String method, String path, String rawQuery, String authorization,
               String accept, String contentType, String body) {}

    record ProjectData(String id, String name, String description) {}

    static final class FakeAutomation implements AutoCloseable {
        final Contract contract;
        final HttpServer server;
        final String baseUrl;
        final List<Req> requests = new ArrayList<>();
        final List<ProjectData> projects = new ArrayList<>();
        int nextId = 300;
        int collectionResponses;
        int failNextListStatus;
        int failNextCreateStatus;
        String raceName;
        String unreconciledConflictName;

        FakeAutomation(Contract contract) throws IOException {
            this.contract = contract;
            projects.add(new ProjectData("prj-100", "alpha", "existing alpha"));
            projects.add(new ProjectData("prj-900", "zulu", "existing zulu"));
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        @Override
        public void close() {
            server.stop(0);
        }

        synchronized void handle(HttpExchange exchange) throws IOException {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            Req request = new Req(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getPath(),
                    exchange.getRequestURI().getRawQuery(),
                    exchange.getRequestHeaders().getFirst("Authorization"),
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    body);
            requests.add(request);

            if (request.path().equals(contract.list().path())
                    && request.method().equals(contract.list().method())) {
                list(request, exchange);
                return;
            }
            if (request.path().equals(contract.create().path())
                    && request.method().equals(contract.create().method())) {
                create(request, exchange);
                return;
            }
            send(exchange, 404, "application/json", "{\"message\":\"operation not in contract\"}");
        }

        void list(Req request, HttpExchange exchange) throws IOException {
            if (!AUTH.equals(request.authorization())) {
                send(exchange, 401, "application/json", "{}");
                return;
            }
            Map<String, List<String>> query = query(request.rawQuery());
            if (!List.of(contract.apiVersion()).equals(query.get("apiVersion"))
                    || !List.of("0").equals(query.get("page"))
                    || !List.of("500").equals(query.get("size"))) {
                send(exchange, 400, "application/json", "{}");
                return;
            }
            if (failNextListStatus != 0) {
                int status = failNextListStatus;
                failNextListStatus = 0;
                send(exchange, status, "application/json", "{}");
                return;
            }

            List<ProjectData> order = new ArrayList<>(projects);
            if ((collectionResponses & 1) == 1) Collections.reverse(order);
            collectionResponses++;
            StringBuilder content = new StringBuilder();
            for (ProjectData project : order) {
                if (!content.isEmpty()) content.append(',');
                content.append(projectJson(project));
            }
            String page = "{\"totalElements\":" + order.size()
                    + ",\"totalPages\":1,\"size\":500,\"content\":[" + content
                    + "],\"number\":0,\"numberOfElements\":" + order.size()
                    + ",\"first\":true,\"last\":true,\"empty\":" + order.isEmpty() + "}";
            send(exchange, 200, "application/json", page);
        }

        void create(Req request, HttpExchange exchange) throws IOException {
            if (!AUTH.equals(request.authorization())) {
                send(exchange, 401, "application/json", "{}");
                return;
            }
            Map<String, List<String>> query = query(request.rawQuery());
            if (!List.of(contract.apiVersion()).equals(query.get("apiVersion"))) {
                send(exchange, 400, "application/json", "{}");
                return;
            }
            if (request.contentType() == null
                    || !request.contentType().toLowerCase().startsWith("application/json")) {
                send(exchange, 415, "application/json", "{}");
                return;
            }
            Map<String, Object> payload;
            try {
                payload = Json.obj(Json.parse(request.body()));
            } catch (RuntimeException badJson) {
                send(exchange, 400, "application/json", "{}");
                return;
            }
            String name = Json.str(payload.get("name"));
            String description = Json.str(payload.get("description"));
            if (name == null || payload.keySet().stream().anyMatch(
                    key -> !key.equals("name") && !key.equals("description"))) {
                send(exchange, 400, "application/json", "{}");
                return;
            }
            if (failNextCreateStatus != 0) {
                int status = failNextCreateStatus;
                failNextCreateStatus = 0;
                send(exchange, status, "application/json", "{}");
                return;
            }
            if (name.equals(unreconciledConflictName)) {
                unreconciledConflictName = null;
                sendEmpty(exchange, 409);
                return;
            }
            for (ProjectData existing : projects) {
                if (existing.name().equals(name)) {
                    sendEmpty(exchange, 409);
                    return;
                }
            }

            ProjectData created = new ProjectData("prj-" + nextId++, name,
                    description == null ? "" : description);
            projects.add(created);
            if (name.equals(raceName)) {
                raceName = null;
                sendEmpty(exchange, 409);
                return;
            }
            send(exchange, 201, "application/json", projectJson(created));
        }

        long countProjectsNamed(String name) {
            return projects.stream().filter(p -> p.name().equals(name)).count();
        }

        long countPostsNamed(String name) {
            return requests.stream()
                    .filter(r -> r.method().equals(contract.create().method()))
                    .filter(r -> {
                        try {
                            return name.equals(Json.str(Json.obj(Json.parse(r.body())).get("name")));
                        } catch (RuntimeException ignored) {
                            return false;
                        }
                    })
                    .count();
        }

        static Map<String, List<String>> query(String raw) {
            Map<String, List<String>> result = new LinkedHashMap<>();
            if (raw == null || raw.isEmpty()) return result;
            for (String pair : raw.split("&")) {
                int equals = pair.indexOf('=');
                String key = equals < 0 ? pair : pair.substring(0, equals);
                String value = equals < 0 ? "" : pair.substring(equals + 1);
                key = URLDecoder.decode(key, StandardCharsets.UTF_8);
                value = URLDecoder.decode(value, StandardCharsets.UTF_8);
                result.computeIfAbsent(key, unused -> new ArrayList<>()).add(value);
            }
            return result;
        }

        static String projectJson(ProjectData project) {
            StringBuilder result = new StringBuilder("{\"id\":\"")
                    .append(jsonEscape(project.id()))
                    .append("\",\"name\":\"").append(jsonEscape(project.name())).append('"');
            if (project.description() != null) {
                result.append(",\"description\":\"")
                        .append(jsonEscape(project.description())).append('"');
            }
            return result.append('}').toString();
        }

        static String jsonEscape(String value) {
            StringBuilder result = new StringBuilder();
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                switch (character) {
                    case '"' -> result.append("\\\"");
                    case '\\' -> result.append("\\\\");
                    case '\b' -> result.append("\\b");
                    case '\f' -> result.append("\\f");
                    case '\n' -> result.append("\\n");
                    case '\r' -> result.append("\\r");
                    case '\t' -> result.append("\\t");
                    default -> {
                        if (character < 0x20) {
                            result.append(String.format("\\u%04x", (int) character));
                        } else {
                            result.append(character);
                        }
                    }
                }
            }
            return result.toString();
        }

        static void send(HttpExchange exchange, int status, String contentType, String body)
                throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", contentType);
            exchange.sendResponseHeaders(status, bytes.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        }

        static void sendEmpty(HttpExchange exchange, int status) throws IOException {
            exchange.sendResponseHeaders(status, -1);
            exchange.close();
        }
    }

    // ---------------------------------------------------------------- tests
    static List<String> names(List<VcfAutomationClient.Project> projects) {
        return projects.stream().map(VcfAutomationClient.Project::name).toList();
    }

    static List<String> ids(List<VcfAutomationClient.Project> projects) {
        return projects.stream().map(VcfAutomationClient.Project::id).toList();
    }

    static List<String> methodsFrom(List<Req> requests, int start) {
        return requests.subList(start, requests.size()).stream().map(Req::method).toList();
    }

    static void verifyWorkspaceConstraints() {
        check(!Files.exists(Path.of("pom.xml")), "no Maven project is added");
    }

    static void testClient(Contract contract) throws Exception {
        try (FakeAutomation mock = new FakeAutomation(contract)) {
            VcfAutomationClient client = new VcfAutomationClient(mock.baseUrl, AUTH);

            // First wire response is alpha,zulu; the second is zulu,alpha.
            // A client that merely preserves server order fails this pair.
            List<VcfAutomationClient.Project> first = client.listProjects();
            List<VcfAutomationClient.Project> second = client.listProjects();
            checkEq(names(first), List.of("alpha", "zulu"), "first sorted collection");
            checkEq(names(second), List.of("alpha", "zulu"),
                    "second sorted collection after mock flips element order");
            checkEq(mock.collectionResponses, 2, "mock emitted two alternately ordered collections");

            int beforeExisting = mock.requests.size();
            VcfAutomationClient.Project alpha = client.ensureProject("alpha", "must not overwrite");
            checkEq(alpha.id(), "prj-100", "existing exact-name project returned");
            checkEq(alpha.description(), "existing alpha", "existing project is not mutated");
            checkEq(mock.requests.size(), beforeExisting + 1,
                    "ensure existing performs only its collection read");

            int beforeBeta = mock.requests.size();
            VcfAutomationClient.Project beta = client.ensureProject("beta", "created once");
            checkEq(beta.name(), "beta", "created project name");
            checkEq(beta.description(), "created once", "created project description");
            checkEq(mock.countProjectsNamed("beta"), 1L, "one beta effect after create");
            checkEq(mock.countPostsNamed("beta"), 1L, "one beta POST after create");
            checkEq(methodsFrom(mock.requests, beforeBeta),
                    List.of(contract.list().method(), contract.create().method()),
                    "absent project path is one initial GET followed by one POST");

            VcfAutomationClient.Project betaRetry = client.ensureProject("beta", "ignored retry text");
            checkEq(betaRetry.id(), beta.id(), "retry returns the same beta project");
            checkEq(betaRetry.description(), "created once", "retry does not mutate existing beta");
            checkEq(mock.countProjectsNamed("beta"), 1L, "retry does not duplicate beta effect");
            checkEq(mock.countPostsNamed("beta"), 1L, "retry performs no second beta POST");

            mock.raceName = "gamma";
            int beforeRace = mock.requests.size();
            VcfAutomationClient.Project gamma = client.ensureProject("gamma", "raced create");
            checkEq(gamma.name(), "gamma", "409 race is reconciled by exact name");
            checkEq(mock.requests.size(), beforeRace + 3,
                    "race path is GET, one POST, then one reconciliation GET");
            checkEq(methodsFrom(mock.requests, beforeRace),
                    List.of(contract.list().method(), contract.create().method(),
                            contract.list().method()),
                    "race request order is GET, POST, GET");
            checkEq(mock.countProjectsNamed("gamma"), 1L, "race creates one gamma effect");
            checkEq(mock.countPostsNamed("gamma"), 1L, "race never blindly retries POST");

            List<VcfAutomationClient.Project> all = client.listProjects();
            checkEq(names(all), List.of("alpha", "beta", "gamma", "zulu"),
                    "complete collection remains sorted after mutations");

            for (Req request : mock.requests) {
                checkEq(request.authorization(), AUTH, "Authorization header on every client request");
                check(!request.rawQuery().contains(AUTH), "authorization value never appears in query");
                checkEq(request.path(), contract.list().path(),
                        "client only uses the path named in the contract");
                check(request.method().equals(contract.list().method())
                                || request.method().equals(contract.create().method()),
                        "client only uses methods named in the contract");
            }

            mock.failNextListStatus = 403;
            try {
                client.listProjects();
                fail("403 should raise ApiException");
            } catch (VcfAutomationClient.ApiException expected) {
                checkEq(expected.statusCode(), 403, "ApiException statusCode");
                check(!expected.getMessage().contains(AUTH),
                        "ApiException message does not reveal authorization");
            }

            HttpClient raw = HttpClient.newHttpClient();
            HttpResponse<String> patch = raw.send(
                    HttpRequest.newBuilder(URI.create(mock.baseUrl + contract.create().path()
                                    + "?apiVersion=" + contract.apiVersion()))
                            .header("Authorization", AUTH)
                            .method("PATCH", HttpRequest.BodyPublishers.noBody())
                            .build(),
                    HttpResponse.BodyHandlers.ofString());
            checkEq(patch.statusCode(), 404, "mock rejects an operation absent from contract");

            HttpResponse<String> about = raw.send(
                    HttpRequest.newBuilder(URI.create(mock.baseUrl + "/project-service/api/about"))
                            .header("Authorization", AUTH)
                            .GET().build(),
                    HttpResponse.BodyHandlers.ofString());
            checkEq(about.statusCode(), 404, "mock rejects a path absent from contract");
        }
    }

    static void testEdgeCases(Contract contract) throws Exception {
        try (FakeAutomation mock = new FakeAutomation(contract)) {
            mock.projects.add(new ProjectData("prj-700", "delta", "later id"));
            mock.projects.add(new ProjectData("prj-200", "delta", "earlier id"));
            mock.projects.add(new ProjectData("prj-050", "bravo", null));
            VcfAutomationClient client = new VcfAutomationClient(mock.baseUrl, AUTH);

            List<VcfAutomationClient.Project> tiedNames = client.listProjects();
            checkEq(names(tiedNames), List.of("alpha", "bravo", "delta", "delta", "zulu"),
                    "primary collection sort key is name");
            checkEq(ids(tiedNames),
                    List.of("prj-100", "prj-050", "prj-200", "prj-700", "prj-900"),
                    "equal names are sorted by the secondary id key");
            checkEq(tiedNames.get(1).description(), null,
                    "an omitted optional project description maps to null");

            String escapedDescription = "quote \" slash \\ tab\t newline\n snowman \u2603";
            VcfAutomationClient.Project prefix = client.ensureProject("alp", escapedDescription);
            checkEq(prefix.name(), "alp", "name matching is exact, not prefix based");
            checkEq(prefix.description(), escapedDescription,
                    "create JSON preserves quotes, slashes, controls, and Unicode");
            checkEq(mock.countProjectsNamed("alpha"), 1L,
                    "creating a prefix does not mistake or mutate the existing longer name");
            checkEq(mock.countProjectsNamed("alp"), 1L, "the exact prefix name is created once");

            VcfAutomationClient.Project capitalized = client.ensureProject("Alpha", "case exact");
            checkEq(capitalized.name(), "Alpha", "exact-name matching is case-sensitive");
            checkEq(capitalized.description(), "case exact",
                    "a differently cased name creates a distinct project");
            checkEq(mock.countProjectsNamed("alpha"), 1L,
                    "the differently cased create does not mutate the existing project");
            checkEq(mock.countProjectsNamed("Alpha"), 1L,
                    "the differently cased exact name is created once");

            String escapedName = "name \" slash \\ tab\t newline\n snowman \u2603";
            VcfAutomationClient.Project escaped = client.ensureProject(escapedName, "special name");
            checkEq(escaped.name(), escapedName,
                    "create JSON preserves quotes, slashes, controls, and Unicode in names");
            checkEq(mock.countProjectsNamed(escapedName), 1L,
                    "the specially encoded project name is created once");

            mock.unreconciledConflictName = "unreconciled";
            int beforeConflict = mock.requests.size();
            try {
                client.ensureProject("unreconciled", "no matching project appears");
                fail("an unreconciled 409 should raise ApiException");
            } catch (VcfAutomationClient.ApiException expected) {
                checkEq(expected.statusCode(), 409, "unreconciled conflict statusCode");
                check(!expected.getMessage().contains(AUTH),
                        "unreconciled conflict message does not reveal authorization");
            }
            checkEq(mock.requests.size(), beforeConflict + 3,
                    "unreconciled conflict performs GET, one POST, and one fresh GET");
            checkEq(methodsFrom(mock.requests, beforeConflict),
                    List.of(contract.list().method(), contract.create().method(),
                            contract.list().method()),
                    "unreconciled conflict request order is GET, POST, GET");
            checkEq(mock.countPostsNamed("unreconciled"), 1L,
                    "unreconciled conflict does not blindly retry POST");
            checkEq(mock.countProjectsNamed("unreconciled"), 0L,
                    "unreconciled conflict does not fabricate a project result");

            mock.failNextCreateStatus = 400;
            int beforeBadRequest = mock.requests.size();
            try {
                client.ensureProject("server-rejected", "valid body, rejected request");
                fail("a create 400 should raise ApiException");
            } catch (VcfAutomationClient.ApiException expected) {
                checkEq(expected.statusCode(), 400, "create ApiException statusCode");
                check(!expected.getMessage().contains(AUTH),
                        "create ApiException message does not reveal authorization");
            }
            checkEq(mock.requests.size(), beforeBadRequest + 2,
                    "create error performs one initial GET and one POST");
            checkEq(methodsFrom(mock.requests, beforeBadRequest),
                    List.of(contract.list().method(), contract.create().method()),
                    "non-conflict create error does not trigger reconciliation");
            checkEq(mock.countProjectsNamed("server-rejected"), 0L,
                    "create error does not produce a project effect");
        }
    }

    public static void main(String[] args) throws Exception {
        verifyWorkspaceConstraints();
        verifyOfficialSources();
        Contract contract = Contract.load();
        testClient(contract);
        testEdgeCases(contract);
        System.out.println("OK " + checks + " checks");
    }

    // ------------------------------------------------------------- tiny JSON
    static final class Json {
        final String source;
        int offset;

        Json(String source) {
            this.source = source;
        }

        static Object parse(String source) {
            Json parser = new Json(source);
            Object result = parser.value();
            parser.whitespace();
            if (parser.offset != source.length()) {
                throw new IllegalArgumentException("trailing JSON at " + parser.offset);
            }
            return result;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> obj(Object value) {
            if (!(value instanceof Map)) throw new IllegalArgumentException("expected object");
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        static List<Object> arr(Object value) {
            if (!(value instanceof List)) throw new IllegalArgumentException("expected array");
            return (List<Object>) value;
        }

        static String str(Object value) {
            return value instanceof String ? (String) value : null;
        }

        void whitespace() {
            while (offset < source.length() && Character.isWhitespace(source.charAt(offset))) offset++;
        }

        void expect(char expected) {
            whitespace();
            if (offset >= source.length() || source.charAt(offset) != expected) {
                throw new IllegalArgumentException("expected '" + expected + "' at " + offset);
            }
            offset++;
        }

        Object value() {
            whitespace();
            if (offset >= source.length()) throw new IllegalArgumentException("unexpected end of JSON");
            return switch (source.charAt(offset)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        Object literal(String text, Object value) {
            if (!source.startsWith(text, offset)) {
                throw new IllegalArgumentException("bad literal at " + offset);
            }
            offset += text.length();
            return value;
        }

        Map<String, Object> object() {
            Map<String, Object> result = new LinkedHashMap<>();
            expect('{');
            whitespace();
            if (offset < source.length() && source.charAt(offset) == '}') {
                offset++;
                return result;
            }
            while (true) {
                String key = string();
                expect(':');
                result.put(key, value());
                whitespace();
                if (offset < source.length() && source.charAt(offset) == ',') {
                    offset++;
                    continue;
                }
                expect('}');
                return result;
            }
        }

        List<Object> array() {
            List<Object> result = new ArrayList<>();
            expect('[');
            whitespace();
            if (offset < source.length() && source.charAt(offset) == ']') {
                offset++;
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (offset < source.length() && source.charAt(offset) == ',') {
                    offset++;
                    continue;
                }
                expect(']');
                return result;
            }
        }

        String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (offset < source.length()) {
                char character = source.charAt(offset++);
                if (character == '"') return result.toString();
                if (character != '\\') {
                    result.append(character);
                    continue;
                }
                if (offset >= source.length()) throw new IllegalArgumentException("bad escape");
                char escaped = source.charAt(offset++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (offset + 4 > source.length()) throw new IllegalArgumentException("bad unicode");
                        result.append((char) Integer.parseInt(source.substring(offset, offset + 4), 16));
                        offset += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape: " + escaped);
                }
            }
            throw new IllegalArgumentException("unterminated string");
        }

        Number number() {
            int start = offset;
            while (offset < source.length()
                    && "-+0123456789.eE".indexOf(source.charAt(offset)) >= 0) offset++;
            if (start == offset) throw new IllegalArgumentException("expected value at " + offset);
            String text = source.substring(start, offset);
            return text.indexOf('.') >= 0 || text.indexOf('e') >= 0 || text.indexOf('E') >= 0
                    ? Double.valueOf(text) : Long.valueOf(text);
        }
    }
}
