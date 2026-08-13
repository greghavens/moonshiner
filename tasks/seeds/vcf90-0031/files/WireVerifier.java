import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Supplier;

/**
 * Asserts the exact wire shape of a rotation run, reading only the stand-in's request log.
 *
 * <p>Nothing here consults the client's return value: the log is the record of what actually left
 * the process, so it is the only evidence used.
 */
public final class WireVerifier {

    public record Check(String name, boolean passed, String detail) {
    }

    private WireVerifier() {
    }

    private static final String CREATE_TOKEN = "createToken";
    private static final String REFRESH = "refreshAccessToken";
    private static final String GET_CREDENTIALS = "getCredentials";
    private static final String ROTATE = "updateOrRotatePasswords";
    private static final String GET_TASK = "getCredentialsTask";

    public static List<Check> verify(List<Map<String, Object>> rawLog) {
        List<Map<String, Object>> log = new ArrayList<>(rawLog);
        log.sort(Comparator.comparingLong(entry -> number(entry, "seq")));
        List<Check> checks = new ArrayList<>();

        List<Map<String, Object>> logins = byOperation(log, CREATE_TOKEN);
        List<Map<String, Object>> refreshes = byOperation(log, REFRESH);
        List<Map<String, Object>> lookups = byOperation(log, GET_CREDENTIALS);
        List<Map<String, Object>> rotates = byOperation(log, ROTATE);
        List<Map<String, Object>> polls = byOperation(log, GET_TASK);
        List<Map<String, Object>> stranded = withStatus(lookups, 401);
        List<Map<String, Object>> served = withStatus(lookups, 200);
        List<Issued> issued = issuedTokens(log);

        check(checks, "every request lands on a contracted operation", () -> {
            List<String> strays = new ArrayList<>();
            for (Map<String, Object> entry : log) {
                if (entry.get("operationId") == null) {
                    strays.add(text(entry, "method") + " " + text(entry, "path")
                            + " -> " + number(entry, "status"));
                }
            }
            return strays.isEmpty() ? null
                    : "requests reached paths docs/contract.json does not name: " + strays;
        });

        check(checks, "the run signs in exactly once", () -> {
            if (logins.size() != 1) {
                return "expected 1 " + CREATE_TOKEN + " request, saw " + logins.size()
                        + ". A stale access token is replaced by refreshing it, not by signing in again.";
            }
            return number(logins.get(0), "seq") == number(log.get(0), "seq") ? null
                    : "the first request of the run was " + text(log.get(0), "operationId")
                    + ", not " + CREATE_TOKEN;
        });

        check(checks, "createToken carries only the members it set", () -> {
            Map<String, Object> entry = single(logins, CREATE_TOKEN);
            String problem = jsonRequest(entry, "POST", "/v1/tokens", false);
            if (problem != null) {
                return problem;
            }
            Object body = Json.parse(text(entry, "body"));
            Set<String> keys = Json.asObject(body).keySet();
            if (!keys.equals(Set.of("username", "password"))) {
                return "the body carried " + keys + "; TokenCreationSpec also declares apiKey and "
                        + "idToken, and members with no value are omitted rather than sent empty";
            }
            if (!MockSddcManager.USERNAME.equals(Json.getString(body, "username"))
                    || !MockSddcManager.PASSWORD.equals(Json.getString(body, "password"))) {
                return "the body did not carry the supplied username and password";
            }
            return null;
        });

        check(checks, "the credential lookups are issued together", () -> {
            if (lookups.size() != 2 * MockSddcManager.HOSTS.size()) {
                return "expected " + (2 * MockSddcManager.HOSTS.size()) + " " + GET_CREDENTIALS
                        + " requests (one per host, stranded on the expired token, then one per host "
                        + "replayed on the refreshed token) but saw " + lookups.size();
            }
            if (stranded.size() != MockSddcManager.HOSTS.size()) {
                return "expected " + MockSddcManager.HOSTS.size() + " lookups to be answered 401 but "
                        + stranded.size() + " were; the appliance expires the access token only while "
                        + "it is holding " + MockSddcManager.GATE_PARTIES + " lookups at once";
            }
            long lastReceived = Long.MIN_VALUE;
            long firstResponded = Long.MAX_VALUE;
            for (Map<String, Object> entry : stranded) {
                lastReceived = Math.max(lastReceived, number(entry, "receivedOrder"));
                firstResponded = Math.min(firstResponded, number(entry, "respondedOrder"));
            }
            return lastReceived < firstResponded ? null
                    : "the stranded lookups were not all in flight at the same time";
        });

        check(checks, "every lookup is replayed after the refresh", () -> {
            if (served.size() != MockSddcManager.HOSTS.size()) {
                return "expected " + MockSddcManager.HOSTS.size() + " lookups to succeed but "
                        + served.size() + " did; a request stranded on the old secret is replayed, "
                        + "not dropped";
            }
            Set<String> strandedHosts = resourceNames(stranded);
            Set<String> servedHosts = resourceNames(served);
            if (!strandedHosts.equals(new LinkedHashSet<>(MockSddcManager.HOSTS))) {
                return "the stranded lookups covered " + strandedHosts + ", expected "
                        + MockSddcManager.HOSTS;
            }
            return servedHosts.equals(new LinkedHashSet<>(MockSddcManager.HOSTS)) ? null
                    : "the replayed lookups covered " + servedHosts + ", expected " + MockSddcManager.HOSTS;
        });

        check(checks, "getCredentials sends only the filters it set", () -> {
            List<String> problems = new ArrayList<>();
            for (Map<String, Object> entry : lookups) {
                if (!"GET".equals(text(entry, "method")) || !"/v1/credentials".equals(text(entry, "path"))) {
                    problems.add("seq " + number(entry, "seq") + ": "
                            + text(entry, "method") + " " + text(entry, "path"));
                    continue;
                }
                Map<String, String> query = query(entry);
                if (!query.keySet().equals(Set.of("resourceName", "accountType"))) {
                    problems.add("seq " + number(entry, "seq") + ": query carried " + query.keySet()
                            + ", expected exactly [resourceName, accountType]");
                    continue;
                }
                if (!"USER".equals(query.get("accountType"))) {
                    problems.add("seq " + number(entry, "seq") + ": accountType was "
                            + query.get("accountType") + ", expected USER");
                }
                if (!MockSddcManager.HOSTS.contains(query.get("resourceName"))) {
                    problems.add("seq " + number(entry, "seq") + ": resourceName was "
                            + query.get("resourceName"));
                }
                if (!text(entry, "body").isEmpty()) {
                    problems.add("seq " + number(entry, "seq") + ": a GET carried a request body");
                }
            }
            return problems.isEmpty() ? null : String.join("; ", problems);
        });

        check(checks, "the stranded lookups share one refresh", () -> refreshes.size() == 1 ? null
                : "expected exactly 1 " + REFRESH + " request but saw " + refreshes.size()
                + "; every request stranded on the expired token waits on the same exchange rather "
                + "than starting its own");

        check(checks, "refreshAccessToken sends a bare JSON string", () -> {
            Map<String, Object> entry = single(refreshes, REFRESH);
            String problem = jsonRequest(entry, "PATCH", "/v1/tokens/access-token/refresh", false);
            if (problem != null) {
                return problem;
            }
            String body = text(entry, "body").trim();
            if (!body.startsWith("\"")) {
                return "the body was " + body + "; the contract's request schema is a bare JSON "
                        + "string holding the refresh token id, not an object wrapping it";
            }
            Object parsed = Json.parse(body);
            return MockSddcManager.REFRESH_TOKEN_ID.equals(parsed) ? null
                    : "the body held " + parsed + ", expected the refresh token id issued by "
                    + CREATE_TOKEN;
        });

        check(checks, "the refresh follows an access-token rejection", () -> {
            Map<String, Object> entry = single(refreshes, REFRESH);
            long refreshedAt = number(entry, "receivedOrder");
            long firstRejectedAt = Long.MAX_VALUE;
            for (Map<String, Object> strandedEntry : stranded) {
                firstRejectedAt = Math.min(firstRejectedAt,
                        number(strandedEntry, "respondedOrder"));
            }
            return firstRejectedAt < refreshedAt ? null
                    : "the refresh was sent before any request was rejected on the expired token";
        });

        check(checks, "no request is sent on a superseded access token", () -> {
            List<String> problems = new ArrayList<>();
            for (Map<String, Object> entry : log) {
                String authorization = text(entry, "authorization");
                if (authorization.isEmpty()) {
                    continue;
                }
                String token = authorization.startsWith("Bearer ")
                        ? authorization.substring("Bearer ".length()) : authorization;
                int index = indexOfToken(issued, token);
                if (index < 0 || index == issued.size() - 1) {
                    continue;
                }
                long supersededAt = issued.get(index + 1).issuedAtOrder;
                if (number(entry, "receivedOrder") > supersededAt) {
                    problems.add("seq " + number(entry, "seq") + " (" + text(entry, "operationId")
                            + ") reached the appliance on an access token that had already been "
                            + "replaced");
                }
            }
            return problems.isEmpty() ? null : String.join("; ", problems);
        });

        check(checks, "credential operations are bearer authenticated", () -> {
            List<String> problems = new ArrayList<>();
            for (Map<String, Object> entry : log) {
                String operation = text(entry, "operationId");
                String authorization = text(entry, "authorization");
                boolean tokenOperation = CREATE_TOKEN.equals(operation) || REFRESH.equals(operation);
                if (tokenOperation) {
                    if (!authorization.isEmpty()) {
                        problems.add("seq " + number(entry, "seq") + " (" + operation
                                + ") carried an Authorization header");
                    }
                    continue;
                }
                if (!authorization.startsWith("Bearer ")) {
                    problems.add("seq " + number(entry, "seq") + " (" + operation
                            + ") carried Authorization " + (authorization.isEmpty() ? "(absent)" : authorization));
                } else if (indexOfToken(issued, authorization.substring("Bearer ".length())) < 0) {
                    problems.add("seq " + number(entry, "seq") + " (" + operation
                            + ") presented a token the appliance never issued");
                }
            }
            return problems.isEmpty() ? null : String.join("; ", problems);
        });

        check(checks, "the rotation is submitted exactly once", () -> rotates.size() == 1 ? null
                : "expected 1 " + ROTATE + " request but saw " + rotates.size());

        check(checks, "the rotation spec carries only the members it set", () -> {
            Map<String, Object> entry = single(rotates, ROTATE);
            String problem = jsonRequest(entry, "PATCH", "/v1/credentials", true);
            if (problem != null) {
                return problem;
            }
            Map<String, Object> body = Json.asObject(Json.parse(text(entry, "body")));
            if (!body.keySet().equals(Set.of("operationType", "elements"))) {
                return "the body carried " + body.keySet() + ", expected exactly "
                        + "[operationType, elements]; autoRotatePolicy is optional and unset here";
            }
            if (!"ROTATE".equals(body.get("operationType"))) {
                return "operationType was " + body.get("operationType") + ", expected ROTATE";
            }
            List<Object> elements = Json.asArray(body.get("elements"));
            if (elements.size() != MockSddcManager.HOSTS.size()) {
                return "elements held " + elements.size() + " entries, expected "
                        + MockSddcManager.HOSTS.size();
            }
            for (int i = 0; i < elements.size(); i++) {
                String host = MockSddcManager.HOSTS.get(i);
                Map<String, Object> element = Json.asObject(elements.get(i));
                if (!element.keySet().equals(Set.of("resourceId", "resourceType", "credentials"))) {
                    return "elements[" + i + "] carried " + element.keySet() + ", expected exactly "
                            + "[resourceId, resourceType, credentials]";
                }
                if (!MockSddcManager.RESOURCE_IDS.get(host).equals(element.get("resourceId"))) {
                    return "elements[" + i + "].resourceId was " + element.get("resourceId")
                            + ", expected the id the appliance reported for " + host;
                }
                if (!MockSddcManager.RESOURCE_TYPE.equals(element.get("resourceType"))) {
                    return "elements[" + i + "].resourceType was " + element.get("resourceType");
                }
                List<Object> credentials = Json.asArray(element.get("credentials"));
                if (credentials.size() != 1) {
                    return "elements[" + i + "].credentials held " + credentials.size()
                            + " entries, expected only the root USER account";
                }
                Map<String, Object> credential = Json.asObject(credentials.get(0));
                if (!credential.keySet().equals(Set.of("credentialType", "accountType", "username"))) {
                    return "elements[" + i + "].credentials[0] carried " + credential.keySet()
                            + ", expected exactly [credentialType, accountType, username]";
                }
                if (!MockSddcManager.CREDENTIAL_TYPE.equals(credential.get("credentialType"))
                        || !"USER".equals(credential.get("accountType"))
                        || !MockSddcManager.USER_ACCOUNT_NAME.equals(credential.get("username"))) {
                    return "elements[" + i + "].credentials[0] was " + credential
                            + ", expected the SSH USER account the lookup reported";
                }
            }
            return null;
        });

        check(checks, "a ROTATE never carries a password", () -> {
            Map<String, Object> entry = single(rotates, ROTATE);
            List<String> found = new ArrayList<>();
            findKey(Json.parse(text(entry, "body")), "password", "body", found);
            return found.isEmpty() ? null
                    : "the request carried a password member at " + found + "; on a ROTATE the "
                    + "appliance generates the new secret, so the member is omitted";
        });

        check(checks, "no request body carries a null or empty value", () -> {
            List<String> problems = new ArrayList<>();
            for (Map<String, Object> entry : log) {
                String body = text(entry, "body");
                if (body.isEmpty()) {
                    continue;
                }
                findEmpty(Json.parse(body), "body", problems,
                        text(entry, "operationId") + " (seq " + number(entry, "seq") + ")");
            }
            return problems.isEmpty() ? null : String.join("; ", problems);
        });

        check(checks, "the rotation follows every lookup it depends on", () -> {
            Map<String, Object> entry = single(rotates, ROTATE);
            long rotatedAt = number(entry, "receivedOrder");
            for (Map<String, Object> lookup : served) {
                if (number(lookup, "respondedOrder") > rotatedAt) {
                    return "the rotation was submitted before every lookup had answered";
                }
            }
            return null;
        });

        check(checks, "the task is polled to a terminal status and no further", () -> {
            int expectedPolls = MockSddcManager.POLLS_BEFORE_TERMINAL + 1;
            if (polls.size() != expectedPolls) {
                return "the credentials task was polled " + polls.size() + " times; it first settles "
                        + "after " + expectedPolls + ", and must not be polled again once terminal";
            }
            String expectedPath = "/v1/credentials/tasks/" + MockSddcManager.CREDENTIALS_TASK_ID;
            for (Map<String, Object> entry : polls) {
                if (!"GET".equals(text(entry, "method")) || !expectedPath.equals(text(entry, "path"))) {
                    return "seq " + number(entry, "seq") + " polled " + text(entry, "method") + " "
                            + text(entry, "path") + ", expected GET " + expectedPath
                            + " using the id from the 202 response";
                }
                if (!text(entry, "rawQuery").isEmpty()) {
                    return "seq " + number(entry, "seq") + " polled with a query string";
                }
                if (!text(entry, "body").isEmpty()) {
                    return "seq " + number(entry, "seq") + " sent a request body on a GET poll";
                }
            }
            Map<String, Object> last = log.get(log.size() - 1);
            if (!GET_TASK.equals(text(last, "operationId"))) {
                return "the run ended with " + text(last, "operationId")
                        + ", expected the terminal poll to be the last request";
            }
            String status = Json.getString(Json.parse(text(last, "responseBody")), "status");
            return "SUCCESSFUL".equals(status) ? null
                    : "the last poll saw status " + status + ", so polling stopped early or continued "
                    + "past the terminal status";
        });

        return checks;
    }

    // ------------------------------------------------------------- helpers

    private record Issued(String token, long issuedAtOrder) {
    }

    private static List<Issued> issuedTokens(List<Map<String, Object>> log) {
        List<Issued> issued = new ArrayList<>();
        for (Map<String, Object> entry : log) {
            String operation = text(entry, "operationId");
            long status = number(entry, "status");
            try {
                if (CREATE_TOKEN.equals(operation) && status == 201) {
                    String token = Json.getString(Json.parse(text(entry, "responseBody")), "accessToken");
                    if (token != null) {
                        issued.add(new Issued(token, number(entry, "respondedOrder")));
                    }
                } else if (REFRESH.equals(operation) && status == 200) {
                    Object token = Json.parse(text(entry, "responseBody"));
                    if (token instanceof String text) {
                        issued.add(new Issued(text, number(entry, "respondedOrder")));
                    }
                }
            } catch (RuntimeException ignored) {
                // A malformed stand-in response cannot happen; ignore rather than mask a real check.
            }
        }
        return issued;
    }

    private static int indexOfToken(List<Issued> issued, String token) {
        for (int i = 0; i < issued.size(); i++) {
            if (issued.get(i).token.equals(token)) {
                return i;
            }
        }
        return -1;
    }

    private static String jsonRequest(Map<String, Object> entry, String method, String path, boolean bearer) {
        if (!method.equals(text(entry, "method")) || !path.equals(text(entry, "path"))) {
            return "the request was " + text(entry, "method") + " " + text(entry, "path")
                    + ", expected " + method + " " + path;
        }
        if (!text(entry, "rawQuery").isEmpty()) {
            return "the request carried the query string " + text(entry, "rawQuery")
                    + ", which the contract does not declare";
        }
        String contentType = text(entry, "contentType").toLowerCase();
        if (!contentType.startsWith("application/json")) {
            return "Content-Type was " + (contentType.isEmpty() ? "absent" : contentType)
                    + ", expected application/json";
        }
        boolean hasAuthorization = !text(entry, "authorization").isEmpty();
        if (bearer && !hasAuthorization) {
            return "the request carried no Authorization header";
        }
        if (!bearer && hasAuthorization) {
            return "the request carried an Authorization header";
        }
        return null;
    }

    private static void findKey(Object node, String key, String path, List<String> found) {
        if (node instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> member : map.entrySet()) {
                if (key.equals(member.getKey())) {
                    found.add(path + "." + key);
                }
                findKey(member.getValue(), key, path + "." + member.getKey(), found);
            }
        } else if (node instanceof List<?> list) {
            for (int i = 0; i < list.size(); i++) {
                findKey(list.get(i), key, path + "[" + i + "]", found);
            }
        }
    }

    private static void findEmpty(Object node, String path, List<String> problems, String origin) {
        if (node == null) {
            problems.add(origin + " sent " + path + " as null");
        } else if (node instanceof String text) {
            if (text.isEmpty()) {
                problems.add(origin + " sent " + path + " as an empty string");
            }
        } else if (node instanceof Map<?, ?> map) {
            if (map.isEmpty()) {
                problems.add(origin + " sent " + path + " as an empty object");
            }
            for (Map.Entry<?, ?> member : map.entrySet()) {
                findEmpty(member.getValue(), path + "." + member.getKey(), problems, origin);
            }
        } else if (node instanceof List<?> list) {
            if (list.isEmpty()) {
                problems.add(origin + " sent " + path + " as an empty array");
            }
            for (int i = 0; i < list.size(); i++) {
                findEmpty(list.get(i), path + "[" + i + "]", problems, origin);
            }
        }
    }

    private static Set<String> resourceNames(List<Map<String, Object>> entries) {
        Set<String> names = new LinkedHashSet<>();
        for (Map<String, Object> entry : entries) {
            String name = query(entry).get("resourceName");
            if (name != null) {
                names.add(name);
            }
        }
        return names;
    }

    private static Map<String, String> query(Map<String, Object> entry) {
        Map<String, String> query = new LinkedHashMap<>();
        String raw = text(entry, "rawQuery");
        if (raw.isEmpty()) {
            return query;
        }
        for (String pair : raw.split("&", -1)) {
            int split = pair.indexOf('=');
            if (split < 0) {
                query.put(java.net.URLDecoder.decode(pair, java.nio.charset.StandardCharsets.UTF_8), "");
            } else {
                query.put(
                        java.net.URLDecoder.decode(pair.substring(0, split),
                                java.nio.charset.StandardCharsets.UTF_8),
                        java.net.URLDecoder.decode(pair.substring(split + 1),
                                java.nio.charset.StandardCharsets.UTF_8));
            }
        }
        return query;
    }

    private static List<Map<String, Object>> byOperation(List<Map<String, Object>> log, String operationId) {
        List<Map<String, Object>> matches = new ArrayList<>();
        for (Map<String, Object> entry : log) {
            if (operationId.equals(entry.get("operationId"))) {
                matches.add(entry);
            }
        }
        return matches;
    }

    private static List<Map<String, Object>> withStatus(List<Map<String, Object>> entries, long status) {
        List<Map<String, Object>> matches = new ArrayList<>();
        for (Map<String, Object> entry : entries) {
            if (number(entry, "status") == status) {
                matches.add(entry);
            }
        }
        return matches;
    }

    private static Map<String, Object> single(List<Map<String, Object>> entries, String operationId) {
        if (entries.size() != 1) {
            throw new IllegalStateException("expected exactly one " + operationId + " request but saw "
                    + entries.size());
        }
        return entries.get(0);
    }

    private static String text(Map<String, Object> entry, String key) {
        Object value = entry.get(key);
        return value == null ? "" : String.valueOf(value);
    }

    private static long number(Map<String, Object> entry, String key) {
        Object value = entry.get(key);
        return value instanceof Number n ? n.longValue() : 0L;
    }

    private static void check(List<Check> checks, String name, Supplier<String> body) {
        try {
            String failure = body.get();
            checks.add(new Check(name, failure == null, failure == null ? "ok" : failure));
        } catch (RuntimeException problem) {
            checks.add(new Check(name, false, String.valueOf(problem.getMessage())));
        }
    }
}
