package com.example.vcf.harness;

import com.example.vcf.support.Json;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Asserts the exact wire shape of what the client sent, and the exact report it produced.
 *
 * <p>All expectations are derived from the fixture and from {@code docs/contract.json}; nothing is
 * hard-coded against a particular implementation. The verifier only reads the request log written
 * by {@link MockVcenter}; it performs no network access of its own.
 */
final class Verifier {

    private static final Set<String> ALLOWED_QUERY_KEYS = Set.of("is_system", "page_size", "marker");

    private final List<String> failures = new ArrayList<>();
    private final List<Map<String, Object>> roles;
    private final Map<String, List<Integer>> pagePlans;
    private final String sessionId;

    Verifier(Path fixtures) throws IOException {
        Map<String, Object> fixture = Json.parseObject(Files.readString(fixtures, StandardCharsets.UTF_8));
        this.sessionId = Json.optString(fixture, "session_id");

        List<Map<String, Object>> parsed = new ArrayList<>();
        for (Object element : Json.requireArray(fixture, "roles")) {
            @SuppressWarnings("unchecked")
            Map<String, Object> role = (Map<String, Object>) element;
            parsed.add(role);
        }
        parsed.sort(Comparator.comparing(role -> (String) role.get("role")));
        this.roles = List.copyOf(parsed);

        Map<String, List<Integer>> plans = new LinkedHashMap<>();
        for (Map.Entry<String, Object> entry : Json.requireObject(fixture, "page_plans").entrySet()) {
            List<Integer> sizes = new ArrayList<>();
            for (Object size : (List<?>) entry.getValue()) {
                sizes.add(((Number) size).intValue());
            }
            plans.put(entry.getKey(), List.copyOf(sizes));
        }
        this.pagePlans = Map.copyOf(plans);
    }

    /** One traversal performed by the client under test. */
    record Scenario(String name, Integer pageSize, Boolean isSystem, String actualReport) {
    }

    /** @return the collected failures; empty means the run passed. */
    List<String> verify(Path requestLog, List<Scenario> scenarios) throws IOException {
        List<Map<String, Object>> entries = readLog(requestLog);
        for (Scenario scenario : scenarios) {
            List<Map<String, Object>> requests = new ArrayList<>();
            for (Map<String, Object> entry : entries) {
                if (scenario.name().equals(entry.get("scenario"))) {
                    requests.add(entry);
                }
            }
            verifyRequests(scenario, requests);
            verifyReport(scenario);
        }
        return failures;
    }

    // ------------------------------------------------------------- requests

    private void verifyRequests(Scenario scenario, List<Map<String, Object>> requests) {
        String where = "scenario '" + scenario.name() + "'";

        int expectedRequests = expectedRequestCount(scenario);
        if (requests.size() != expectedRequests) {
            failures.add(where + ": expected exactly " + expectedRequests + " request(s) to "
                    + MockVcenter.OPERATION_PATH + ", the client made " + requests.size()
                    + ". A short page is not the end of the collection, and no request may follow a"
                    + " response that carried no marker.");
            if (requests.isEmpty()) {
                return;
            }
        }

        String previousMarker = null;
        for (int i = 0; i < requests.size(); i++) {
            Map<String, Object> request = requests.get(i);
            String at = where + ", request #" + i;

            if (!"GET".equals(request.get("method"))) {
                failures.add(at + ": expected method GET, got " + request.get("method"));
            }
            if (!MockVcenter.OPERATION_PATH.equals(request.get("path"))) {
                failures.add(at + ": expected path " + MockVcenter.OPERATION_PATH
                        + ", got " + request.get("path"));
            }

            @SuppressWarnings("unchecked")
            Map<String, Object> headers = (Map<String, Object>) request.get("headers");
            Object session = headers.get("vmware-api-session-id");
            if (!sessionId.equals(session)) {
                failures.add(at + ": expected the vmware-api-session-id header to carry the session id, got "
                        + render(session));
            }
            Object accept = headers.get("accept");
            if (!"application/json".equals(accept)) {
                failures.add(at + ": expected Accept: application/json, got "
                        + render(accept));
            }
            if (headers.get("authorization") != null) {
                failures.add(at + ": the contract uses the session header, an Authorization header must not be sent");
            }

            Object status = request.get("responseStatus");
            if (!Long.valueOf(200).equals(status)) {
                failures.add(at + ": the server answered " + status
                        + "; the request did not satisfy the contract");
            }

            Map<String, String> query = queryOf(request);

            for (String key : query.keySet()) {
                if (!ALLOWED_QUERY_KEYS.contains(key)) {
                    failures.add(at + ": unexpected query parameter '" + key + "'");
                }
            }
            for (Map.Entry<String, String> entry : query.entrySet()) {
                if (entry.getValue() == null || entry.getValue().isEmpty()) {
                    failures.add(at + ": query parameter '" + entry.getKey()
                            + "' was sent with an empty value; unset optional properties must be omitted");
                }
            }

            // page_size: present exactly when the caller set it.
            if (scenario.pageSize() == null) {
                if (query.containsKey("page_size")) {
                    failures.add(at + ": page_size was left unset by the caller, so the parameter must be"
                            + " omitted from the query string; got page_size=" + query.get("page_size"));
                }
            } else {
                String expected = String.valueOf(scenario.pageSize());
                if (!expected.equals(query.get("page_size"))) {
                    failures.add(at + ": expected page_size=" + expected + ", got "
                            + render(query.get("page_size")));
                }
            }

            // is_system: present exactly when the caller set it.
            if (scenario.isSystem() == null) {
                if (query.containsKey("is_system")) {
                    failures.add(at + ": the filter was left unset by the caller, so is_system must be"
                            + " omitted from the query string; got is_system=" + query.get("is_system"));
                }
            } else {
                String expected = String.valueOf(scenario.isSystem());
                if (!expected.equals(query.get("is_system"))) {
                    failures.add(at + ": expected is_system=" + expected + ", got "
                            + render(query.get("is_system")));
                }
            }

            // marker: absent on the first request, verbatim echo afterwards.
            if (i == 0) {
                if (query.containsKey("marker")) {
                    failures.add(at + ": the first request of a traversal must omit marker entirely, got marker="
                            + query.get("marker"));
                }
                if (scenario.pageSize() == null && scenario.isSystem() == null) {
                    Object rawQuery = request.get("rawQuery");
                    boolean empty = rawQuery == null || ((String) rawQuery).isEmpty();
                    if (!empty) {
                        failures.add(at + ": every optional property was unset, so the request must carry no"
                                + " query string at all, got '" + rawQuery + "'");
                    }
                }
            } else {
                String sent = query.get("marker");
                if (sent == null) {
                    failures.add(at + ": expected the marker returned by the previous response ("
                            + previousMarker + "), no marker was sent");
                } else if (!sent.equals(previousMarker)) {
                    failures.add(at + ": the marker must be echoed verbatim; expected " + previousMarker
                            + ", got " + sent);
                }
            }

            Object marker = request.get("responseMarker");
            previousMarker = marker == null ? null : (String) marker;
            boolean last = i == requests.size() - 1;
            if (last && previousMarker != null) {
                failures.add(at + ": this was the last request but the response still carried a marker,"
                        + " so the collection was not drained completely");
            }
            if (!last && previousMarker == null) {
                failures.add(at + ": the response carried no marker, the client must not issue a further request");
            }
        }

        long delivered = 0;
        for (Map<String, Object> request : requests) {
            Object count = request.get("responseItemCount");
            if (count instanceof Number) {
                delivered += ((Number) count).longValue();
            }
        }
        int expectedItems = matching(scenario.isSystem()).size();
        if (delivered != expectedItems) {
            failures.add(where + ": the traversal delivered " + delivered + " item(s), expected "
                    + expectedItems);
        }
    }

    private int expectedRequestCount(Scenario scenario) {
        int total = matching(scenario.isSystem()).size();
        String planKey = scenario.pageSize() == null
                ? "default"
                : (scenario.isSystem() == null ? "all" : (scenario.isSystem() ? "system" : "custom"));
        List<Integer> plan = pagePlans.get(planKey);
        int cursor = 0;
        int requests = 0;
        for (Integer size : plan) {
            requests++;
            cursor += size;
            if (cursor >= total) {
                break;
            }
        }
        return requests;
    }

    // --------------------------------------------------------------- report

    private void verifyReport(Scenario scenario) {
        String expected = expectedReport(scenario.isSystem());
        String actual = scenario.actualReport();
        if (actual == null) {
            failures.add("scenario '" + scenario.name() + "': the client returned null");
            return;
        }
        if (expected.equals(actual)) {
            return;
        }
        failures.add("scenario '" + scenario.name() + "': the report does not match.\n"
                + firstDifference(expected, actual)
                + "\n--- expected ---\n" + visible(expected)
                + "\n--- actual ---\n" + visible(actual));
    }

    private String expectedReport(Boolean isSystem) {
        List<String[]> rows = new ArrayList<>();
        for (Map<String, Object> role : matching(isSystem)) {
            @SuppressWarnings("unchecked")
            Map<String, Object> info = (Map<String, Object>) role.get("info");
            Set<String> privileges = new TreeSet<>();
            for (Object privilege : (List<?>) info.get("privileges")) {
                privileges.add((String) privilege);
            }
            rows.add(new String[] {
                    (String) info.get("name"),
                    (String) role.get("role"),
                    String.valueOf(info.get("system")),
                    String.join(",", privileges)
            });
        }
        rows.sort(Comparator.<String[], String>comparing(row -> row[0]).thenComparing(row -> row[1]));

        StringBuilder out = new StringBuilder();
        for (String[] row : rows) {
            out.append(row[0]).append('\t').append(row[1]).append('\t')
                    .append(row[2]).append('\t').append(row[3]).append('\n');
        }
        return out.toString();
    }

    private List<Map<String, Object>> matching(Boolean isSystem) {
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> role : roles) {
            @SuppressWarnings("unchecked")
            Map<String, Object> info = (Map<String, Object>) role.get("info");
            if (isSystem == null || isSystem.equals(info.get("system"))) {
                result.add(role);
            }
        }
        return result;
    }

    // -------------------------------------------------------------- helpers

    private List<Map<String, Object>> readLog(Path requestLog) throws IOException {
        List<Map<String, Object>> entries = new ArrayList<>();
        if (!Files.exists(requestLog)) {
            failures.add("the request log " + requestLog + " was never written");
            return entries;
        }
        for (String line : Files.readAllLines(requestLog, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                entries.add(Json.parseObject(line));
            }
        }
        if (entries.isEmpty()) {
            failures.add("the client did not send a single request");
        }
        return entries;
    }

    private Map<String, String> queryOf(Map<String, Object> request) {
        Map<String, String> flat = new LinkedHashMap<>();
        Object raw = request.get("query");
        if (!(raw instanceof Map)) {
            return flat;
        }
        for (Map.Entry<?, ?> entry : ((Map<?, ?>) raw).entrySet()) {
            Object value = entry.getValue();
            String single = value instanceof List && !((List<?>) value).isEmpty()
                    ? String.valueOf(((List<?>) value).get(0))
                    : String.valueOf(value);
            flat.put(String.valueOf(entry.getKey()), single);
        }
        return flat;
    }

    private static String render(Object value) {
        return value == null ? "<absent>" : "'" + value + "'";
    }

    private static String visible(String text) {
        return text.replace("\t", "\\t").replace("\n", "\\n\n");
    }

    private static String firstDifference(String expected, String actual) {
        String[] expectedLines = expected.split("\n", -1);
        String[] actualLines = actual.split("\n", -1);
        int limit = Math.min(expectedLines.length, actualLines.length);
        for (int i = 0; i < limit; i++) {
            if (!expectedLines[i].equals(actualLines[i])) {
                return "first difference on line " + (i + 1) + ":\n  expected: "
                        + expectedLines[i].replace("\t", "\\t") + "\n  actual:   "
                        + actualLines[i].replace("\t", "\\t");
            }
        }
        return "the reports have different lengths: expected " + expectedLines.length
                + " line(s), got " + actualLines.length;
    }

    /** Distinct keys observed, for diagnostics. */
    static Set<String> distinctKeys(List<Map<String, Object>> requests) {
        Set<String> keys = new LinkedHashSet<>();
        for (Map<String, Object> request : requests) {
            Object query = request.get("query");
            if (query instanceof Map) {
                for (Object key : ((Map<?, ?>) query).keySet()) {
                    keys.add(String.valueOf(key));
                }
            }
        }
        return keys;
    }
}
