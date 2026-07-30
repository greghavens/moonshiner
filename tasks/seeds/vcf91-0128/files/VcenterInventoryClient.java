import java.io.IOException;
import java.net.ProxySelector;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Minimal vCenter inventory client backed only by the Java 17 standard library.
 *
 * <p>The public surface in this file is part of the exercise contract.</p>
 */
public final class VcenterInventoryClient {
    private static final String TOKEN_EXCHANGE_GRANT =
            "urn:ietf:params:oauth:grant-type:token-exchange";
    private static final Set<String> VM_POWER_STATES =
            Set.of("POWERED_OFF", "POWERED_ON", "SUSPENDED");
    private static final Set<String> HOST_CONNECTION_STATES =
            Set.of("CONNECTED", "DISCONNECTED", "NOT_RESPONDING");
    private static final Set<String> HOST_POWER_STATES =
            Set.of("POWERED_OFF", "POWERED_ON");

    private final URI origin;
    private final String subjectToken;
    private final String subjectTokenType;
    private final Duration requestTimeout;
    private final HttpClient httpClient;
    private String accessToken;

    public record VM(
            String id,
            String name,
            String powerState,
            Long cpuCount,
            Long memorySizeMiB) {
    }

    public record Host(
            String id,
            String name,
            String connectionState,
            String powerState,
            String hostUuid) {
    }

    public record Inventory(List<VM> vms, List<Host> hosts) {
        public Inventory {
            vms = List.copyOf(Objects.requireNonNull(vms, "vms"));
            hosts = List.copyOf(Objects.requireNonNull(hosts, "hosts"));
        }
    }

    public VcenterInventoryClient(
            URI origin,
            String accessToken,
            String subjectToken,
            String subjectTokenType,
            Duration requestTimeout) {
        this(origin, accessToken, subjectToken, subjectTokenType, requestTimeout, null);
    }

    VcenterInventoryClient(
            URI origin,
            String accessToken,
            String subjectToken,
            String subjectTokenType,
            Duration requestTimeout,
            HttpClient testHttpClient) {
        this.origin = requireOrigin(origin);
        this.accessToken = requireCredential(accessToken, "accessToken");
        this.subjectToken = requireCredential(subjectToken, "subjectToken");
        this.subjectTokenType = requireCredential(subjectTokenType, "subjectTokenType");
        if (requestTimeout == null || requestTimeout.isZero() || requestTimeout.isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        this.requestTimeout = requestTimeout;
        this.httpClient = testHttpClient != null
                ? testHttpClient
                : HttpClient.newBuilder()
                        .connectTimeout(requestTimeout)
                        .followRedirects(HttpClient.Redirect.NEVER)
                        .proxy(ProxySelector.of(null))
                        .build();
    }

    /**
     * Retrieves the VM and host summaries that form one inventory snapshot.
     */
    public Inventory collect() throws IOException, InterruptedException {
        try {
            return collectOnce();
        } catch (ExpiredAccessTokenException expired) {
            issueAccessToken();
            return collectOnce();
        }
    }

    private Inventory collectOnce() throws IOException, InterruptedException {
        List<VM> vms = listVMs();
        List<Host> hosts = listHosts();
        return new Inventory(vms, hosts);
    }

    private List<VM> listVMs() throws IOException, InterruptedException {
        Object document = getCollection("/api/vcenter/vm", "Vcenter.VM_list");
        List<Object> items = requireArray(document, "Vcenter.VM_list");
        List<VM> result = new ArrayList<>(items.size());
        for (Object item : items) {
            Map<String, Object> object = requireObject(item, "VM summary");
            String id = requiredString(object, "vm", "VM summary");
            String name = requiredString(object, "name", "VM summary");
            String powerState = requiredString(object, "power_state", "VM summary");
            if (!VM_POWER_STATES.contains(powerState)) {
                throw protocolFailure("VM summary has an invalid power_state");
            }
            result.add(new VM(
                    id,
                    name,
                    powerState,
                    optionalLong(object, "cpu_count", "VM summary"),
                    optionalLong(object, "memory_size_mib", "VM summary")));
        }
        return result;
    }

    private List<Host> listHosts() throws IOException, InterruptedException {
        Object document = getCollection("/api/vcenter/host", "Vcenter.Host_list");
        List<Object> items = requireArray(document, "Vcenter.Host_list");
        List<Host> result = new ArrayList<>(items.size());
        for (Object item : items) {
            Map<String, Object> object = requireObject(item, "host summary");
            String id = requiredString(object, "host", "host summary");
            String name = requiredString(object, "name", "host summary");
            String connectionState =
                    requiredString(object, "connection_state", "host summary");
            if (!HOST_CONNECTION_STATES.contains(connectionState)) {
                throw protocolFailure("host summary has an invalid connection_state");
            }
            String powerState = optionalString(object, "power_state", "host summary");
            if (powerState != null && !HOST_POWER_STATES.contains(powerState)) {
                throw protocolFailure("host summary has an invalid power_state");
            }
            result.add(new Host(
                    id,
                    name,
                    connectionState,
                    powerState,
                    optionalString(object, "host_uuid", "host summary")));
        }
        return result;
    }

    private Object getCollection(String path, String operationId)
            throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(endpoint(path))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .header("vmware-api-session-id", accessToken)
                .GET()
                .build();
        HttpResponse<byte[]> response = send(request);
        if (response.statusCode() == 401) {
            throw new ExpiredAccessTokenException(operationId);
        }
        if (response.statusCode() != 200) {
            throw new IOException(operationId + " returned HTTP " + response.statusCode());
        }
        return parseJson(response.body(), operationId);
    }

    private void issueAccessToken() throws IOException, InterruptedException {
        String form = "grant_type=" + formEncode(TOKEN_EXCHANGE_GRANT)
                + "&subject_token=" + formEncode(subjectToken)
                + "&subject_token_type=" + formEncode(subjectTokenType);
        HttpRequest request = HttpRequest.newBuilder(
                        endpoint("/api/vcenter/authentication/token"))
                .timeout(requestTimeout)
                .header("Accept", "application/json")
                .header("Content-Type", "application/x-www-form-urlencoded")
                .header("Authorization", "Bearer " + subjectToken)
                .POST(HttpRequest.BodyPublishers.ofString(form, StandardCharsets.UTF_8))
                .build();
        HttpResponse<byte[]> response = send(request);
        if (response.statusCode() != 200) {
            throw new IOException(
                    "Vcenter.Authentication.Token_issue returned HTTP "
                            + response.statusCode());
        }
        Map<String, Object> token = requireObject(
                parseJson(response.body(), "Vcenter.Authentication.Token_issue"),
                "token response");
        String replacement = requiredString(token, "access_token", "token response");
        String tokenType = requiredString(token, "token_type", "token response");
        if (!"Bearer".equals(tokenType) || !isHeaderSafe(replacement)) {
            throw protocolFailure("token response contains invalid token data");
        }
        accessToken = replacement;
    }

    private HttpResponse<byte[]> send(HttpRequest request)
            throws IOException, InterruptedException {
        try {
            return httpClient.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw interrupted;
        } catch (IOException transportFailure) {
            throw new IOException("vCenter request failed", transportFailure);
        }
    }

    private URI endpoint(String path) {
        return URI.create(origin.toString() + path);
    }

    private static URI requireOrigin(URI value) {
        if (value == null
                || value.getScheme() == null
                || (!"http".equalsIgnoreCase(value.getScheme())
                        && !"https".equalsIgnoreCase(value.getScheme()))
                || value.getHost() == null
                || value.getRawUserInfo() != null
                || value.getRawQuery() != null
                || value.getRawFragment() != null
                || !(value.getRawPath() == null
                        || value.getRawPath().isEmpty()
                        || "/".equals(value.getRawPath()))) {
            throw new IllegalArgumentException("origin must be an HTTP(S) origin");
        }
        String text = value.toString();
        return URI.create(text.endsWith("/") ? text.substring(0, text.length() - 1) : text);
    }

    private static String requireCredential(String value, String name) {
        if (value == null || value.isBlank() || !isHeaderSafe(value)) {
            throw new IllegalArgumentException(name + " is invalid");
        }
        return value;
    }

    private static boolean isHeaderSafe(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c < 0x20 || c == 0x7f) {
                return false;
            }
        }
        return true;
    }

    private static String formEncode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static Object parseJson(byte[] bytes, String operationId) throws IOException {
        final String text;
        try {
            text = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException badUtf8) {
            throw new IOException(operationId + " returned malformed UTF-8");
        }
        try {
            return new JsonParser(text).parseDocument();
        } catch (JsonFailure malformed) {
            throw new IOException(operationId + " returned malformed JSON");
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> requireObject(Object value, String context)
            throws IOException {
        if (!(value instanceof Map<?, ?>)) {
            throw protocolFailure(context + " must be an object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> requireArray(Object value, String context)
            throws IOException {
        if (!(value instanceof List<?>)) {
            throw protocolFailure(context + " must return an array");
        }
        return (List<Object>) value;
    }

    private static String requiredString(
            Map<String, Object> object, String field, String context) throws IOException {
        Object value = object.get(field);
        if (!(value instanceof String text) || text.isBlank()) {
            throw protocolFailure(context + " has invalid " + field);
        }
        return text;
    }

    private static String optionalString(
            Map<String, Object> object, String field, String context) throws IOException {
        if (!object.containsKey(field) || object.get(field) == null) {
            return null;
        }
        Object value = object.get(field);
        if (!(value instanceof String text)) {
            throw protocolFailure(context + " has invalid " + field);
        }
        return text;
    }

    private static Long optionalLong(
            Map<String, Object> object, String field, String context) throws IOException {
        if (!object.containsKey(field) || object.get(field) == null) {
            return null;
        }
        Object value = object.get(field);
        if (!(value instanceof Long number)) {
            throw protocolFailure(context + " has invalid " + field);
        }
        return number;
    }

    private static IOException protocolFailure(String message) {
        return new IOException(message);
    }

    private static final class ExpiredAccessTokenException extends IOException {
        private ExpiredAccessTokenException(String operationId) {
            super(operationId + " returned HTTP 401");
        }
    }

    private static final class JsonFailure extends Exception {
        private JsonFailure() {
            super(null, null, false, false);
        }
    }

    /**
     * Small strict JSON decoder sufficient for the schemas projected in the
     * checked-in contract.
     */
    private static final class JsonParser {
        private final String source;
        private int index;

        private JsonParser(String source) {
            this.source = source;
        }

        private Object parseDocument() throws JsonFailure {
            skipWhitespace();
            Object value = parseValue();
            skipWhitespace();
            if (index != source.length()) {
                throw new JsonFailure();
            }
            return value;
        }

        private Object parseValue() throws JsonFailure {
            if (index >= source.length()) {
                throw new JsonFailure();
            }
            return switch (source.charAt(index)) {
                case '{' -> parseObject();
                case '[' -> parseArray();
                case '"' -> parseString();
                case 't' -> parseLiteral("true", Boolean.TRUE);
                case 'f' -> parseLiteral("false", Boolean.FALSE);
                case 'n' -> parseLiteral("null", null);
                default -> parseNumber();
            };
        }

        private Map<String, Object> parseObject() throws JsonFailure {
            expect('{');
            skipWhitespace();
            Map<String, Object> result = new LinkedHashMap<>();
            if (consume('}')) {
                return result;
            }
            while (true) {
                if (index >= source.length() || source.charAt(index) != '"') {
                    throw new JsonFailure();
                }
                String key = parseString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                Object value = parseValue();
                if (result.containsKey(key)) {
                    throw new JsonFailure();
                }
                result.put(key, value);
                skipWhitespace();
                if (consume('}')) {
                    return result;
                }
                expect(',');
                skipWhitespace();
            }
        }

        private List<Object> parseArray() throws JsonFailure {
            expect('[');
            skipWhitespace();
            List<Object> result = new ArrayList<>();
            if (consume(']')) {
                return result;
            }
            while (true) {
                result.add(parseValue());
                skipWhitespace();
                if (consume(']')) {
                    return result;
                }
                expect(',');
                skipWhitespace();
            }
        }

        private String parseString() throws JsonFailure {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (index < source.length()) {
                char c = source.charAt(index++);
                if (c == '"') {
                    return out.toString();
                }
                if (c == '\\') {
                    if (index >= source.length()) {
                        throw new JsonFailure();
                    }
                    char escaped = source.charAt(index++);
                    switch (escaped) {
                        case '"', '\\', '/' -> out.append(escaped);
                        case 'b' -> out.append('\b');
                        case 'f' -> out.append('\f');
                        case 'n' -> out.append('\n');
                        case 'r' -> out.append('\r');
                        case 't' -> out.append('\t');
                        case 'u' -> out.append(parseUnicodeEscape());
                        default -> throw new JsonFailure();
                    }
                } else {
                    if (c < 0x20) {
                        throw new JsonFailure();
                    }
                    out.append(c);
                }
            }
            throw new JsonFailure();
        }

        private char parseUnicodeEscape() throws JsonFailure {
            if (index + 4 > source.length()) {
                throw new JsonFailure();
            }
            int value = 0;
            for (int i = 0; i < 4; i++) {
                int digit = Character.digit(source.charAt(index++), 16);
                if (digit < 0) {
                    throw new JsonFailure();
                }
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private Long parseNumber() throws JsonFailure {
            int start = index;
            if (consume('-') && index >= source.length()) {
                throw new JsonFailure();
            }
            if (consume('0')) {
                if (index < source.length() && Character.isDigit(source.charAt(index))) {
                    throw new JsonFailure();
                }
            } else {
                int digits = index;
                while (index < source.length()
                        && Character.isDigit(source.charAt(index))) {
                    index++;
                }
                if (digits == index) {
                    throw new JsonFailure();
                }
            }
            if (index < source.length()
                    && (source.charAt(index) == '.' || source.charAt(index) == 'e'
                            || source.charAt(index) == 'E')) {
                throw new JsonFailure();
            }
            try {
                return Long.valueOf(source.substring(start, index));
            } catch (NumberFormatException invalid) {
                throw new JsonFailure();
            }
        }

        private Object parseLiteral(String literal, Object value) throws JsonFailure {
            if (!source.startsWith(literal, index)) {
                throw new JsonFailure();
            }
            index += literal.length();
            return value;
        }

        private void expect(char wanted) throws JsonFailure {
            if (!consume(wanted)) {
                throw new JsonFailure();
            }
        }

        private boolean consume(char wanted) {
            if (index < source.length() && source.charAt(index) == wanted) {
                index++;
                return true;
            }
            return false;
        }

        private void skipWhitespace() {
            while (index < source.length()) {
                char c = source.charAt(index);
                if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
                    index++;
                } else {
                    return;
                }
            }
        }
    }
}
