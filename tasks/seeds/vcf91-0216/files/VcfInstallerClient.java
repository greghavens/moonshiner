import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/** Dependency-free client scaffold for the focused VCF Installer 9.1 contract. */
public final class VcfInstallerClient {
    private static final String GET_TASKS_OPERATION = "getTasks";
    private static final String REFRESH_OPERATION = "refreshAccessToken";
    private static final String TASKS_PATH = "/v1/tasks";
    private static final String REFRESH_PATH = "/v1/tokens/access-token/refresh";

    private final String baseUrl;
    private String accessToken;
    private final String refreshTokenId;
    private final HttpClient httpClient;

    public record TaskQuery(
            Integer limit,
            String taskStatus,
            String taskType,
            String resourceId,
            String resourceType,
            Long completedAfter,
            String orderDirection,
            String orderBy,
            String taskName,
            Boolean doLiveRefresh,
            int pageSize) {
    }

    public record Task(
            String id,
            String name,
            String type,
            String status,
            String creationTimestamp) {
    }

    public static final class VcfApiException extends RuntimeException {
        private final String operationId;
        private final int statusCode;
        private final String errorCode;

        private VcfApiException(String operationId, int statusCode, String errorCode) {
            super(operationId + " failed with HTTP status " + statusCode);
            this.operationId = operationId;
            this.statusCode = statusCode;
            this.errorCode = errorCode;
        }

        public String operationId() {
            return operationId;
        }

        public int statusCode() {
            return statusCode;
        }

        public String errorCode() {
            return errorCode;
        }
    }

    public static final class ProtocolException extends RuntimeException {
        private final String operationId;

        private ProtocolException(String operationId, String problem) {
            super(operationId + " protocol error: " + problem);
            this.operationId = operationId;
        }

        public String operationId() {
            return operationId;
        }
    }

    public static final class TransportException extends RuntimeException {
        private final String operationId;

        private TransportException(String operationId) {
            super(operationId + " transport failure");
            this.operationId = operationId;
        }

        public String operationId() {
            return operationId;
        }
    }

    private record Page(
            List<Task> tasks,
            int pageNumber,
            int pageSize,
            int totalElements,
            int totalPages) {
    }

    public VcfInstallerClient(String baseUrl, String accessToken, String refreshTokenId) {
        this.baseUrl = validateBaseUrl(baseUrl);
        if (!headerSafeText(accessToken)) {
            throw new IllegalArgumentException("accessToken must be nonblank and header-safe");
        }
        if (refreshTokenId == null || refreshTokenId.isBlank()) {
            throw new IllegalArgumentException("refreshTokenId must be nonblank");
        }
        this.accessToken = accessToken;
        this.refreshTokenId = refreshTokenId;
        this.httpClient = HttpClient.newBuilder()
                .followRedirects(HttpClient.Redirect.NEVER)
                .build();
    }

    /** Retrieve all pages, refreshing once if the access token expires mid-run. */
    public synchronized List<Task> listAllTasks(TaskQuery query) {
        Objects.requireNonNull(query, "query");
        if (query.pageSize() < 1 || query.pageSize() > 100) {
            throw new IllegalArgumentException("pageSize must be between 1 and 100");
        }
        throw new UnsupportedOperationException("TODO: implement paged retrieval with token refresh");
    }

    private HttpResponse<String> requestTasks(String rawTarget, String token) {
        throw new UnsupportedOperationException("TODO: implement bodyless getTasks request");
    }

    private String refreshAccessToken() {
        throw new UnsupportedOperationException("TODO: implement refreshAccessToken request");
    }

    private static String rawTarget(TaskQuery query, Integer pageNumber) {
        throw new UnsupportedOperationException("TODO: encode optional query parameters");
    }

    private HttpResponse<String> send(HttpRequest request, String operationId) {
        try {
            return httpClient.send(
                    request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new TransportException(operationId);
        } catch (IOException exception) {
            throw new TransportException(operationId);
        }
    }

    private static Page decodePage(String body, int requestedPage, int requestedSize) {
        Map<?, ?> root = parseObject(body, GET_TASKS_OPERATION, "PageOfTask");
        Object elementsValue = root.get("elements");
        if (!(elementsValue instanceof List<?> elements)) {
            throw protocol(GET_TASKS_OPERATION, "elements must be a non-null array");
        }
        Object metadataValue = root.get("pageMetadata");
        if (!(metadataValue instanceof Map<?, ?> metadata)) {
            throw protocol(GET_TASKS_OPERATION, "pageMetadata must be an object");
        }

        int pageNumber = integer(metadata, "pageNumber", GET_TASKS_OPERATION);
        int pageSize = integer(metadata, "pageSize", GET_TASKS_OPERATION);
        int totalElements = integer(metadata, "totalElements", GET_TASKS_OPERATION);
        int totalPages = integer(metadata, "totalPages", GET_TASKS_OPERATION);
        if (pageNumber != requestedPage) {
            throw protocol(GET_TASKS_OPERATION, "returned pageNumber does not match request");
        }
        if (pageSize < 0 || totalElements < 0 || totalPages < 0) {
            throw protocol(GET_TASKS_OPERATION, "pagination metadata must not be negative");
        }
        if (pageSize != elements.size() || pageSize > requestedSize) {
            throw protocol(GET_TASKS_OPERATION, "pageSize is inconsistent with elements");
        }

        List<Task> tasks = new ArrayList<>(elements.size());
        for (Object element : elements) {
            if (!(element instanceof Map<?, ?> task)) {
                throw protocol(GET_TASKS_OPERATION, "Task must be an object");
            }
            String type = null;
            if (task.containsKey("type")) {
                Object typeValue = task.get("type");
                if (!(typeValue instanceof String text)) {
                    throw protocol(GET_TASKS_OPERATION, "Task.type must be a string when present");
                }
                type = text;
            }
            tasks.add(new Task(
                    requiredText(task, "id", GET_TASKS_OPERATION),
                    requiredText(task, "name", GET_TASKS_OPERATION),
                    type,
                    requiredText(task, "status", GET_TASKS_OPERATION),
                    requiredText(task, "creationTimestamp", GET_TASKS_OPERATION)));
        }
        return new Page(List.copyOf(tasks), pageNumber, pageSize, totalElements, totalPages);
    }

    private static void requireJson(HttpResponse<?> response, String operationId) {
        List<String> values = response.headers().allValues("Content-Type");
        if (values.size() != 1) {
            throw protocol(operationId, "successful response must have one JSON media type");
        }
        String mediaType = values.get(0);
        int semicolon = mediaType.indexOf(';');
        if (semicolon >= 0) {
            mediaType = mediaType.substring(0, semicolon);
        }
        if (!mediaType.trim().equalsIgnoreCase("application/json")) {
            throw protocol(operationId, "successful response is not JSON");
        }
    }

    private static VcfApiException apiError(
            String operationId, int statusCode, String responseBody) {
        String errorCode = null;
        try {
            Object value = MiniJson.parse(responseBody);
            if (value instanceof Map<?, ?> error && error.get("errorCode") instanceof String text) {
                errorCode = text;
            }
        } catch (RuntimeException ignored) {
            // Error response bodies are not trusted or reflected into exception messages.
        }
        return new VcfApiException(operationId, statusCode, errorCode);
    }

    private static Map<?, ?> parseObject(String body, String operationId, String schema) {
        final Object parsed;
        try {
            parsed = MiniJson.parse(body);
        } catch (RuntimeException exception) {
            throw protocol(operationId, "malformed " + schema + " JSON");
        }
        if (!(parsed instanceof Map<?, ?> map)) {
            throw protocol(operationId, schema + " must be a JSON object");
        }
        return map;
    }

    private static int integer(Map<?, ?> map, String name, String operationId) {
        Object value = map.get(name);
        if (!(value instanceof Long number)
                || number < Integer.MIN_VALUE || number > Integer.MAX_VALUE) {
            throw protocol(operationId, "pageMetadata." + name + " must be an integer");
        }
        return number.intValue();
    }

    private static String requiredText(Map<?, ?> map, String name, String operationId) {
        Object value = map.get(name);
        if (!(value instanceof String text) || text.isBlank()) {
            throw protocol(operationId, "Task." + name + " must be a nonblank string");
        }
        return text;
    }

    private static ProtocolException protocol(String operationId, String problem) {
        return new ProtocolException(operationId, problem);
    }

    private static String validateBaseUrl(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("baseUrl must be nonblank");
        }
        final URI uri;
        try {
            uri = new URI(value);
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("baseUrl must be an absolute HTTP(S) URI");
        }
        String scheme = uri.getScheme();
        String path = uri.getRawPath();
        if (scheme == null
                || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))
                || uri.getHost() == null
                || uri.getRawUserInfo() != null
                || (path != null && !path.isEmpty() && !path.equals("/"))
                || uri.getRawQuery() != null
                || uri.getRawFragment() != null) {
            throw new IllegalArgumentException("baseUrl must be an HTTP(S) service root");
        }
        String normalized = uri.toString();
        return normalized.endsWith("/")
                ? normalized.substring(0, normalized.length() - 1)
                : normalized;
    }

    private static boolean headerSafeText(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            if (ch > 0xff
                    || (ch != ' ' && ch != '\t' && (ch < 0x21 || ch == 0x7f))) {
                return false;
            }
        }
        return true;
    }

    private static void parameter(StringBuilder query, String name, Object value) {
        if (value == null) {
            return;
        }
        if (query.length() > 0) {
            query.append('&');
        }
        query.append(name).append('=').append(percentEncode(String.valueOf(value)));
    }

    private static String percentEncode(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder result = new StringBuilder(bytes.length);
        final char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte item : bytes) {
            int valueByte = item & 0xff;
            if ((valueByte >= 'a' && valueByte <= 'z')
                    || (valueByte >= 'A' && valueByte <= 'Z')
                    || (valueByte >= '0' && valueByte <= '9')
                    || valueByte == '-' || valueByte == '.' || valueByte == '_'
                    || valueByte == '~') {
                result.append((char) valueByte);
            } else {
                result.append('%').append(hex[valueByte >>> 4]).append(hex[valueByte & 0x0f]);
            }
        }
        return result.toString();
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2).append('"');
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            switch (ch) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (ch < 0x20) {
                        String hex = Integer.toHexString(ch);
                        result.append("\\u").append("0".repeat(4 - hex.length())).append(hex);
                    } else {
                        result.append(ch);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    /** Strict JSON reader sufficient for the specification response subset. */
    private static final class MiniJson {
        private final String source;
        private int index;

        private MiniJson(String source) {
            this.source = Objects.requireNonNull(source, "source");
        }

        static Object parse(String source) {
            MiniJson parser = new MiniJson(source);
            Object value = parser.value();
            parser.space();
            if (parser.index != source.length()) {
                throw new IllegalArgumentException("trailing JSON data");
            }
            return value;
        }

        private Object value() {
            space();
            if (index >= source.length()) {
                throw new IllegalArgumentException("unexpected end of JSON");
            }
            return switch (source.charAt(index)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() {
            index++;
            Map<String, Object> result = new LinkedHashMap<>();
            space();
            if (take('}')) {
                return result;
            }
            while (true) {
                space();
                if (index >= source.length() || source.charAt(index) != '"') {
                    throw new IllegalArgumentException("object key must be a string");
                }
                String key = string();
                space();
                expect(':');
                if (result.put(key, value()) != null) {
                    throw new IllegalArgumentException("duplicate object key");
                }
                space();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> array() {
            index++;
            List<Object> result = new ArrayList<>();
            space();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                space();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (index < source.length()) {
                char ch = source.charAt(index++);
                if (ch == '"') {
                    return result.toString();
                }
                if (ch != '\\') {
                    if (ch < 0x20) {
                        throw new IllegalArgumentException("control character in string");
                    }
                    result.append(ch);
                    continue;
                }
                if (index >= source.length()) {
                    throw new IllegalArgumentException("incomplete escape");
                }
                char escaped = source.charAt(index++);
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> result.append(unicode());
                    default -> throw new IllegalArgumentException("invalid escape");
                }
            }
            throw new IllegalArgumentException("unterminated string");
        }

        private char unicode() {
            if (index + 4 > source.length()) {
                throw new IllegalArgumentException("incomplete unicode escape");
            }
            int value = 0;
            for (int count = 0; count < 4; count++) {
                int digit = Character.digit(source.charAt(index++), 16);
                if (digit < 0) {
                    throw new IllegalArgumentException("invalid unicode escape");
                }
                value = value * 16 + digit;
            }
            return (char) value;
        }

        private Object number() {
            int start = index;
            if (take('-')) {
                // sign consumed
            }
            if (take('0')) {
                // zero consumed
            } else {
                digits();
            }
            boolean integral = true;
            if (take('.')) {
                integral = false;
                digits();
            }
            if (index < source.length()
                    && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                integral = false;
                index++;
                if (index < source.length()
                        && (source.charAt(index) == '+' || source.charAt(index) == '-')) {
                    index++;
                }
                digits();
            }
            String token = source.substring(start, index);
            try {
                if (integral) {
                    return Long.valueOf(token);
                }
                return Double.valueOf(token);
            } catch (NumberFormatException exception) {
                throw new IllegalArgumentException("invalid number");
            }
        }

        private void digits() {
            int start = index;
            while (index < source.length() && Character.isDigit(source.charAt(index))) {
                index++;
            }
            if (start == index) {
                throw new IllegalArgumentException("expected digit");
            }
        }

        private Object literal(String token, Object value) {
            if (!source.startsWith(token, index)) {
                throw new IllegalArgumentException("invalid literal");
            }
            index += token.length();
            return value;
        }

        private void space() {
            while (index < source.length()
                    && (source.charAt(index) == ' ' || source.charAt(index) == '\n'
                    || source.charAt(index) == '\r' || source.charAt(index) == '\t')) {
                index++;
            }
        }

        private boolean take(char expected) {
            if (index < source.length() && source.charAt(index) == expected) {
                index++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                throw new IllegalArgumentException("expected " + expected);
            }
        }
    }
}
