import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * A loopback mock of the VCF Automation Catalog service, pinned to docs/contract.json.
 *
 * <p>The route table is not written here; it is read out of the contract at construction time. A
 * request whose method and path are not named by a contract operation is refused, which is what
 * keeps the exercise honest about the excluded administrative and per-item routes.
 *
 * <p>Every request is appended to a JSONL log and flushed immediately, so the harness can read the
 * exact wire shape back off disk after the client has run.
 *
 * <p>Protected file. Do not edit.
 */
final class MockVcfAutomation implements AutoCloseable {

    /** Fault injection selected per scenario by the harness. */
    enum Mode {
        NORMAL,
        ALWAYS_UNAUTHORIZED,
        WRONG_PAGE_NUMBER,
        WRONG_PAGE_SIZE,
        CHANGED_TOTALS,
        CHANGED_TOTAL_PAGES,
        INCONSISTENT_TOTAL_PAGES,
        OVERFULL_PAGE,
        EMPTY_MIDDLE_PAGE,
        MISSING_CONTENT,
        MISSING_NUMBER,
        OUT_OF_RANGE_NUMBER,
        MISSING_SIZE,
        NON_INTEGRAL_SIZE,
        OUT_OF_RANGE_SIZE,
        MISSING_TOTAL_ELEMENTS,
        OUT_OF_RANGE_TOTAL_ELEMENTS,
        MISSING_TOTAL_PAGES,
        NON_INTEGRAL_TOTAL_PAGES,
        OUT_OF_RANGE_TOTAL_PAGES,
        NON_OBJECT_ITEM,
        BLANK_ITEM_ID,
        BLANK_ITEM_NAME,
        DUPLICATE_ITEM_ID,
        FAIL_ON_LAST_PAGE,
        RESPONSE_INVALID_JSON,
        RESPONSE_NOT_AN_OBJECT
    }

    private record Route(String method, String path) {
    }

    private final HttpServer server;
    private final Path logPath;
    private final BufferedWriter log;
    private final AtomicInteger seq = new AtomicInteger();
    private final List<Route> routes = new ArrayList<>();
    private final String bearerToken;
    private final List<Map<String, Object>> serverOrderedItems;
    private final Mode mode;

    /**
     * @param contractPath        path to docs/contract.json; the served routes are derived from it
     * @param logPath             JSONL request log the harness reads back
     * @param bearerToken         token that must arrive as {@code Authorization: Bearer <token>}
     * @param serverOrderedItems  catalog items already in the order the service would return them
     *                            for {@code sort=name,asc}, ties included
     */
    MockVcfAutomation(Path contractPath,
                      Path logPath,
                      String bearerToken,
                      List<Map<String, Object>> serverOrderedItems,
                      Mode mode) throws IOException {
        this.logPath = logPath;
        this.bearerToken = bearerToken;
        this.serverOrderedItems = serverOrderedItems;
        this.mode = mode;

        Object parsed = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        if (!(parsed instanceof Map<?, ?> contract)) {
            throw new IOException("contract.json is not a JSON object");
        }
        Object ops = contract.get("operations");
        if (!(ops instanceof List<?> operations) || operations.isEmpty()) {
            throw new IOException("contract.json names no operations");
        }
        for (Object op : operations) {
            if (op instanceof Map<?, ?> operation) {
                Object method = operation.get("method");
                Object path = operation.get("path");
                if (method instanceof String m && path instanceof String p) {
                    routes.add(new Route(m.toUpperCase(Locale.ROOT), p));
                }
            }
        }
        if (routes.isEmpty()) {
            throw new IOException("contract.json names no usable method/path pairs");
        }

        Files.createDirectories(logPath.toAbsolutePath().getParent());
        Files.deleteIfExists(logPath);
        this.log = Files.newBufferedWriter(logPath, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND);

        this.server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.createContext("/", this::handle);
        this.server.setExecutor(null);
        this.server.start();
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    Path logPath() {
        return logPath;
    }

    @Override
    public void close() throws IOException {
        server.stop(0);
        log.flush();
        log.close();
    }

    private void handle(HttpExchange exchange) throws IOException {
        int status;
        String body;
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String path = exchange.getRequestURI().getPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        if (rawQuery == null) {
            rawQuery = "";
        }
        byte[] requestBody = readAll(exchange.getRequestBody());

        boolean pathKnown = routes.stream().anyMatch(r -> r.path().equals(path));
        boolean routeKnown = routes.stream()
                .anyMatch(r -> r.path().equals(path) && r.method().equals(method));

        if (!pathKnown) {
            status = 404;
            body = errorJson("route_not_in_contract",
                    "No contract operation serves path " + path);
        } else if (!routeKnown) {
            status = 405;
            body = errorJson("method_not_in_contract",
                    "No contract operation serves " + method + " " + path);
        } else if (!authorized(exchange)) {
            status = 401;
            body = errorJson("unauthorized", "A valid bearer token is required.");
        } else {
            List<String[]> params = decodeParams(rawQuery);
            try {
                Result result = serveCatalogItems(params);
                status = result.status();
                body = result.body();
            } catch (BadRequest e) {
                status = 400;
                body = errorJson("bad_request", e.getMessage());
            }
        }

        writeLog(method, path, rawQuery, decodeParams(rawQuery), exchange, requestBody.length, status);

        byte[] payload = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, payload.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(payload);
        }
    }

    private record Result(int status, String body) {
    }

    private static final class BadRequest extends RuntimeException {
        BadRequest(String message) {
            super(message);
        }
    }

    private Result serveCatalogItems(List<String[]> params) {
        String pageRaw = single(params, "page");
        String sizeRaw = single(params, "size");
        if (pageRaw == null) {
            throw new BadRequest("The contract requires an explicit page parameter.");
        }
        if (sizeRaw == null) {
            throw new BadRequest("The contract requires an explicit size parameter.");
        }
        int page = parseInt(pageRaw, "page");
        int size = parseInt(sizeRaw, "size");
        if (page < 0) {
            throw new BadRequest("page must be zero or greater, got " + page);
        }
        if (size < 1) {
            throw new BadRequest("size must be one or greater, got " + size);
        }

        String search = single(params, "search");
        List<String> projects = all(params, "projects");

        List<Map<String, Object>> matched = new ArrayList<>();
        for (Map<String, Object> item : serverOrderedItems) {
            if (!matchesSearch(item, search)) {
                continue;
            }
            if (!matchesProjects(item, projects)) {
                continue;
            }
            matched.add(item);
        }

        int totalElements = matched.size();
        int totalPages = (totalElements + size - 1) / size;
        int from = Math.min(page * size, totalElements);
        int to = Math.min(from + size, totalElements);
        List<Object> slice = new ArrayList<>(matched.subList(from, to));

        if (mode == Mode.RESPONSE_NOT_AN_OBJECT) {
            return new Result(200, Json.write(slice));
        }
        if (mode == Mode.RESPONSE_INVALID_JSON) {
            // A leading plus sign is not permitted by the JSON number grammar. Keeping the rest
            // of the envelope plausible ensures the supplied parser really rejects malformed JSON.
            return new Result(200, "{\"content\":[],\"number\":+0,\"size\":20,"
                    + "\"totalElements\":0,\"totalPages\":0}");
        }
        if (mode == Mode.FAIL_ON_LAST_PAGE && totalPages > 1 && page == totalPages - 1) {
            return new Result(500, errorJson("internal_error", "Injected failure on the final page."));
        }

        if (mode == Mode.EMPTY_MIDDLE_PAGE && page == 1) {
            slice.clear();
        } else if (mode == Mode.OVERFULL_PAGE && page == 0 && to < matched.size()) {
            slice.add(matched.get(to));
        } else if (mode == Mode.NON_OBJECT_ITEM && page == 0 && !slice.isEmpty()) {
            slice.set(0, "not-an-object");
        } else if ((mode == Mode.BLANK_ITEM_ID || mode == Mode.BLANK_ITEM_NAME)
                && page == 0 && !slice.isEmpty()) {
            @SuppressWarnings("unchecked")
            Map<String, Object> bad = new LinkedHashMap<>((Map<String, Object>) slice.get(0));
            bad.put(mode == Mode.BLANK_ITEM_ID ? "id" : "name", "   ");
            slice.set(0, bad);
        } else if (mode == Mode.DUPLICATE_ITEM_ID && page == 0 && slice.size() > 1) {
            @SuppressWarnings("unchecked")
            Map<String, Object> first = (Map<String, Object>) slice.get(0);
            @SuppressWarnings("unchecked")
            Map<String, Object> duplicate = new LinkedHashMap<>((Map<String, Object>) slice.get(1));
            duplicate.put("id", first.get("id"));
            slice.set(1, duplicate);
        }

        int reportedNumber = page;
        if (mode == Mode.WRONG_PAGE_NUMBER && page > 0) {
            reportedNumber = page - 1;
        }

        int reportedSize = mode == Mode.WRONG_PAGE_SIZE ? size + 1 : size;
        int reportedTotalElements = totalElements;
        if (mode == Mode.CHANGED_TOTALS && page > 0) {
            reportedTotalElements++;
        }
        int reportedTotalPages = totalPages;
        if ((mode == Mode.CHANGED_TOTAL_PAGES && page > 0)
                || mode == Mode.INCONSISTENT_TOTAL_PAGES) {
            reportedTotalPages++;
        }

        Map<String, Object> sortObject = new LinkedHashMap<>();
        sortObject.put("empty", false);
        sortObject.put("sorted", true);
        sortObject.put("unsorted", false);

        Map<String, Object> pageable = new LinkedHashMap<>();
        pageable.put("offset", from);
        pageable.put("pageNumber", reportedNumber);
        pageable.put("pageSize", size);
        pageable.put("paged", true);
        pageable.put("sort", sortObject);
        pageable.put("unpaged", false);

        Map<String, Object> envelope = new LinkedHashMap<>();
        if (mode != Mode.MISSING_CONTENT) {
            envelope.put("content", slice);
        }
        envelope.put("empty", slice.isEmpty());
        envelope.put("first", page == 0);
        envelope.put("last", page >= totalPages - 1);
        if (mode != Mode.MISSING_NUMBER) {
            envelope.put("number", mode == Mode.OUT_OF_RANGE_NUMBER
                    ? 4_294_967_296L : reportedNumber);
        }
        envelope.put("numberOfElements", slice.size());
        envelope.put("pageable", pageable);
        Object sizeValue = switch (mode) {
            case NON_INTEGRAL_SIZE -> size + 0.5;
            case OUT_OF_RANGE_SIZE -> size + 4_294_967_296L;
            default -> reportedSize;
        };
        if (mode != Mode.MISSING_SIZE) {
            envelope.put("size", sizeValue);
        }
        envelope.put("sort", sortObject);
        if (mode != Mode.MISSING_TOTAL_ELEMENTS) {
            envelope.put("totalElements", mode == Mode.OUT_OF_RANGE_TOTAL_ELEMENTS
                    ? 1.0e30 : reportedTotalElements);
        }
        Object totalPagesValue = switch (mode) {
            case NON_INTEGRAL_TOTAL_PAGES -> reportedTotalPages + 0.5;
            case OUT_OF_RANGE_TOTAL_PAGES -> reportedTotalPages + 4_294_967_296L;
            default -> reportedTotalPages;
        };
        if (mode != Mode.MISSING_TOTAL_PAGES) {
            envelope.put("totalPages", totalPagesValue);
        }
        return new Result(200, Json.write(envelope));
    }

    private static boolean matchesSearch(Map<String, Object> item, String search) {
        if (search == null || search.isBlank()) {
            return true;
        }
        String needle = search.toLowerCase(Locale.ROOT);
        String name = item.get("name") instanceof String s ? s : "";
        String description = item.get("description") instanceof String s ? s : "";
        return name.toLowerCase(Locale.ROOT).contains(needle)
                || description.toLowerCase(Locale.ROOT).contains(needle);
    }

    private static boolean matchesProjects(Map<String, Object> item, List<String> projects) {
        if (projects.isEmpty()) {
            return true;
        }
        if (!(item.get("projectIds") instanceof List<?> owned)) {
            return false;
        }
        for (Object candidate : owned) {
            if (candidate instanceof String s && projects.contains(s)) {
                return true;
            }
        }
        return false;
    }

    private boolean authorized(HttpExchange exchange) {
        if (mode == Mode.ALWAYS_UNAUTHORIZED) {
            return false;
        }
        List<String> values = exchange.getRequestHeaders().get("Authorization");
        if (values == null || values.size() != 1) {
            return false;
        }
        return ("Bearer " + bearerToken).equals(values.get(0));
    }

    private void writeLog(String method,
                          String path,
                          String rawQuery,
                          List<String[]> params,
                          HttpExchange exchange,
                          int bodyBytes,
                          int status) throws IOException {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("seq", seq.incrementAndGet());
        entry.put("method", method);
        entry.put("path", path);
        entry.put("rawQuery", rawQuery);
        entry.put("target", rawQuery.isEmpty() ? path : path + "?" + rawQuery);

        List<Object> loggedParams = new ArrayList<>();
        for (String[] pair : params) {
            Map<String, Object> p = new LinkedHashMap<>();
            p.put("name", pair[0]);
            p.put("value", pair[1]);
            loggedParams.add(p);
        }
        entry.put("params", loggedParams);

        Map<String, Object> headers = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> header : exchange.getRequestHeaders().entrySet()) {
            headers.put(header.getKey().toLowerCase(Locale.ROOT), new ArrayList<Object>(header.getValue()));
        }
        entry.put("headers", headers);
        entry.put("bodyBytes", bodyBytes);
        entry.put("status", status);

        synchronized (log) {
            log.write(Json.write(entry));
            log.newLine();
            log.flush();
        }
    }

    /**
     * Splits a raw query string into decoded name/value pairs in wire order. A parameter written
     * without an equals sign and a parameter written with an empty value both decode to an empty
     * value, and both are recorded, so the harness can tell an omitted parameter from a blank one.
     */
    private static List<String[]> decodeParams(String rawQuery) {
        List<String[]> params = new ArrayList<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return params;
        }
        for (String chunk : rawQuery.split("&", -1)) {
            if (chunk.isEmpty()) {
                params.add(new String[]{"", ""});
                continue;
            }
            int eq = chunk.indexOf('=');
            String rawName = eq < 0 ? chunk : chunk.substring(0, eq);
            String rawValue = eq < 0 ? "" : chunk.substring(eq + 1);
            params.add(new String[]{decode(rawName), decode(rawValue)});
        }
        return params;
    }

    private static String decode(String raw) {
        try {
            return URLDecoder.decode(raw, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return raw;
        }
    }

    private static String single(List<String[]> params, String name) {
        String found = null;
        for (String[] pair : params) {
            if (pair[0].equals(name)) {
                if (found != null) {
                    throw new BadRequest("Parameter " + name + " was sent more than once.");
                }
                found = pair[1];
            }
        }
        return found;
    }

    private static List<String> all(List<String[]> params, String name) {
        List<String> values = new ArrayList<>();
        for (String[] pair : params) {
            if (pair[0].equals(name)) {
                values.add(pair[1]);
            }
        }
        return values;
    }

    private static int parseInt(String raw, String name) {
        try {
            return Integer.parseInt(raw.trim());
        } catch (NumberFormatException e) {
            throw new BadRequest("Parameter " + name + " is not an integer: '" + raw + "'");
        }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        try (in) {
            return in.readAllBytes();
        }
    }

    private static String errorJson(String code, String message) {
        Map<String, Object> error = new LinkedHashMap<>();
        error.put("error", code);
        error.put("message", message);
        return Json.write(error);
    }
}
