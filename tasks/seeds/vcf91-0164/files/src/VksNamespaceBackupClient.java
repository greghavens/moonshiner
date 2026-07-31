import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Dependency-free VCF 9.1 client for a Supervisor backup followed by a
 * deterministic VKS Cluster inventory.
 *
 * Complete the TODOs without adding another production source file.
 */
public final class VksNamespaceBackupClient {
    public static final String GET_NAMESPACE_OPERATION =
            "Vcenter.Namespaces.Instances_getV2";
    public static final String LIST_CLUSTERS_OPERATION =
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:list";
    public static final String CREATE_BACKUP_OPERATION =
            "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create";
    public static final String GET_TASK_OPERATION = "Cis.Tasks_get";

    private static final int MAX_RESPONSE_BYTES = 64 * 1024;

    public record Config(
            URI vcenterOrigin,
            URI vksOrigin,
            String vcenterSessionId,
            String kubernetesBearerToken,
            Duration requestTimeout,
            Duration pollInterval,
            int maxPolls) {
    }

    public record BackupRequest(String namespace, String comment) {
    }

    public record Cluster(String name, String topologyVersion) {
    }

    public record BackupMetadata(
            String createOperation,
            String taskOperation,
            String taskId,
            String status,
            int polls,
            Object result) {
    }

    public record Result(
            String namespace,
            String supervisor,
            List<Cluster> clusters,
            BackupMetadata backup) {
        public Result {
            clusters = List.copyOf(clusters);
        }
    }

    public static class ClientException extends RuntimeException {
        public ClientException(String message) {
            super(message);
        }
    }

    public static final class ApiException extends ClientException {
        private final String operation;
        private final Integer statusCode;

        public ApiException(String operation, Integer statusCode) {
            super(statusCode == null
                    ? operation + " transport failed"
                    : operation + " failed with HTTP " + statusCode);
            this.operation = operation;
            this.statusCode = statusCode;
        }

        public String operation() {
            return operation;
        }

        public Integer statusCode() {
            return statusCode;
        }
    }

    public static final class ProtocolException extends ClientException {
        private final String operation;

        public ProtocolException(String operation) {
            super(operation + " returned an invalid success response");
            this.operation = operation;
        }

        public String operation() {
            return operation;
        }
    }

    public static final class NamespaceNotReadyException extends ClientException {
        private final String status;

        public NamespaceNotReadyException(String status) {
            super("Supervisor namespace is not ready");
            this.status = status;
        }

        public String status() {
            return status;
        }
    }

    public static final class TaskFailedException extends ClientException {
        private final String taskId;
        private final int polls;

        public TaskFailedException(String taskId, int polls) {
            super("Supervisor backup task failed");
            this.taskId = taskId;
            this.polls = polls;
        }

        public String taskId() {
            return taskId;
        }

        public int polls() {
            return polls;
        }
    }

    public static final class PollTimeoutException extends ClientException {
        private final String taskId;
        private final int polls;

        public PollTimeoutException(String taskId, int polls) {
            super("Supervisor backup task did not reach a terminal state");
            this.taskId = taskId;
            this.polls = polls;
        }

        public String taskId() {
            return taskId;
        }

        public int polls() {
            return polls;
        }
    }

    static record WireResponse(int status, HttpHeaders headers, byte[] body) {
    }

    interface Exchange {
        WireResponse send(String operation, HttpRequest request)
                throws InterruptedException;
    }

    private record TaskSnapshot(String status, Object result) {
    }

    private final Config config;
    private final Exchange exchange;
    private final String vcenterOrigin;
    private final String vksOrigin;

    public VksNamespaceBackupClient(Config config) {
        this(config, null);
    }

    VksNamespaceBackupClient(Config config, Exchange suppliedExchange) {
        this.config = Objects.requireNonNull(config, "config");
        this.vcenterOrigin = validateOrigin(config.vcenterOrigin(), "vcenterOrigin");
        this.vksOrigin = validateOrigin(config.vksOrigin(), "vksOrigin");
        validateCredential(config.vcenterSessionId(), "vcenterSessionId");
        validateCredential(
                config.kubernetesBearerToken(), "kubernetesBearerToken");
        if (config.requestTimeout() == null
                || config.requestTimeout().isZero()
                || config.requestTimeout().isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
        }
        if (config.pollInterval() == null || config.pollInterval().isNegative()) {
            throw new IllegalArgumentException("pollInterval must not be negative");
        }
        if (config.maxPolls() < 1) {
            throw new IllegalArgumentException("maxPolls must be positive");
        }
        if (suppliedExchange == null) {
            HttpClient httpClient = HttpClient.newBuilder()
                    .connectTimeout(config.requestTimeout())
                    .followRedirects(HttpClient.Redirect.NEVER)
                    .version(HttpClient.Version.HTTP_1_1)
                    .build();
            this.exchange = (operation, request) -> {
                final HttpResponse<InputStream> response;
                try {
                    response = httpClient.send(
                            request, HttpResponse.BodyHandlers.ofInputStream());
                } catch (IOException error) {
                    throw new ApiException(operation, null);
                }
                final byte[] body;
                try (InputStream input = response.body()) {
                    body = readLimited(input, operation);
                } catch (IOException error) {
                    throw new ApiException(operation, null);
                }
                return new WireResponse(
                        response.statusCode(), response.headers(), body);
            };
        } else {
            this.exchange = suppliedExchange;
        }
    }

    public Result backupNamespace(BackupRequest request) throws InterruptedException {
        // TODO: validate the request, perform the five-step workflow, and return
        // only a terminal, stable, deterministically ordered result.
        throw new UnsupportedOperationException("TODO: backupNamespace");
    }

    private String getRunningNamespace(String namespace) throws InterruptedException {
        WireResponse response = sendGet(
                GET_NAMESPACE_OPERATION,
                vcenterOrigin
                        + "/api/vcenter/namespaces/instances/v2/"
                        + encodeSegment(namespace),
                true);
        Map<String, Object> info = requireJsonObject(
                response, GET_NAMESPACE_OPERATION);
        String supervisor = requiredNonblankString(
                info, "supervisor", GET_NAMESPACE_OPERATION);
        String status = requiredString(
                info, "config_status", GET_NAMESPACE_OPERATION);
        requiredString(info, "description", GET_NAMESPACE_OPERATION);
        requiredList(info, "messages", GET_NAMESPACE_OPERATION);
        requiredList(info, "access_list", GET_NAMESPACE_OPERATION);
        requiredList(info, "storage_specs", GET_NAMESPACE_OPERATION);
        Map<String, Object> stats = requiredObject(
                info, "stats", GET_NAMESPACE_OPERATION);
        requiredLong(stats, "cpu_used", GET_NAMESPACE_OPERATION);
        requiredLong(stats, "memory_used", GET_NAMESPACE_OPERATION);
        requiredLong(stats, "storage_used", GET_NAMESPACE_OPERATION);

        if (!Set.of("CONFIGURING", "REMOVING", "RUNNING", "ERROR")
                .contains(status)) {
            throw new ProtocolException(GET_NAMESPACE_OPERATION);
        }
        if (!"RUNNING".equals(status)) {
            throw new NamespaceNotReadyException(status);
        }
        return supervisor;
    }

    private List<Cluster> listClusters(String namespace)
            throws InterruptedException {
        // TODO: invoke LIST_CLUSTERS_OPERATION, validate the complete focused
        // ClusterList shape, reject duplicate names, and return a fresh list
        // sorted by Cluster.name using String's ordinal ordering.
        throw new UnsupportedOperationException("TODO: listClusters");
    }

    private String createBackup(String supervisor, String comment)
            throws InterruptedException {
        String body = comment == null
                ? "{}"
                : "{\"comment\":" + jsonString(comment) + "}";
        WireResponse response = sendPost(
                CREATE_BACKUP_OPERATION,
                vcenterOrigin
                        + "/api/vcenter/namespace-management/supervisors/"
                        + encodeSegment(supervisor)
                        + "/recovery/backup/jobs",
                body);
        requireSuccess(response, CREATE_BACKUP_OPERATION);
        Object parsed = parseJson(response.body(), CREATE_BACKUP_OPERATION);
        if (!(parsed instanceof String taskId) || taskId.isBlank()) {
            throw new ProtocolException(CREATE_BACKUP_OPERATION);
        }
        return taskId;
    }

    private TaskSnapshot getTask(String taskId) throws InterruptedException {
        WireResponse response = sendGet(
                GET_TASK_OPERATION,
                vcenterOrigin + "/api/cis/tasks/" + encodeSegment(taskId),
                true);
        Map<String, Object> task = requireJsonObject(response, GET_TASK_OPERATION);
        Map<String, Object> description = requiredObject(
                task, "description", GET_TASK_OPERATION);
        requiredString(description, "id", GET_TASK_OPERATION);
        requiredString(description, "default_message", GET_TASK_OPERATION);
        requiredList(description, "args", GET_TASK_OPERATION);
        requiredString(task, "service", GET_TASK_OPERATION);
        requiredString(task, "operation", GET_TASK_OPERATION);
        requiredBoolean(task, "cancelable", GET_TASK_OPERATION);
        String status = requiredString(task, "status", GET_TASK_OPERATION);
        if (!Set.of("PENDING", "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED")
                .contains(status)) {
            throw new ProtocolException(GET_TASK_OPERATION);
        }
        Object result = task.containsKey("result")
                ? deepFreeze(task.get("result"))
                : null;
        return new TaskSnapshot(status, result);
    }

    private WireResponse sendGet(String operation, String target, boolean vcenter)
            throws InterruptedException {
        HttpRequest.Builder builder = requestBuilder(operation, target, vcenter);
        return exchange(operation, builder.GET().build());
    }

    private WireResponse sendPost(String operation, String target, String body)
            throws InterruptedException {
        HttpRequest.Builder builder = requestBuilder(operation, target, true);
        builder.header("Content-Type", "application/json");
        builder.POST(HttpRequest.BodyPublishers.ofString(
                body, StandardCharsets.UTF_8));
        return exchange(operation, builder.build());
    }

    private HttpRequest.Builder requestBuilder(
            String operation, String target, boolean vcenter) {
        final HttpRequest.Builder builder;
        try {
            builder = HttpRequest.newBuilder(URI.create(target));
        } catch (IllegalArgumentException error) {
            throw new ClientException(operation + " request construction failed");
        }
        builder.timeout(config.requestTimeout());
        builder.header("Accept", "application/json");
        if (vcenter) {
            builder.header(
                    "vmware-api-session-id", config.vcenterSessionId());
        } else {
            builder.header(
                    "Authorization",
                    "Bearer " + config.kubernetesBearerToken());
        }
        return builder;
    }

    private WireResponse exchange(String operation, HttpRequest request)
            throws InterruptedException {
        return exchange.send(operation, request);
    }

    private static byte[] readLimited(InputStream input, String operation)
            throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int total = 0;
        while (true) {
            int read = input.read(buffer);
            if (read == -1) {
                return output.toByteArray();
            }
            total += read;
            if (total > MAX_RESPONSE_BYTES) {
                throw new ProtocolException(operation);
            }
            output.write(buffer, 0, read);
        }
    }

    private static Map<String, Object> requireJsonObject(
            WireResponse response, String operation) {
        requireSuccess(response, operation);
        Object parsed = parseJson(response.body(), operation);
        if (!(parsed instanceof Map<?, ?> map)) {
            throw new ProtocolException(operation);
        }
        return stringMap(map, operation);
    }

    private static void requireSuccess(WireResponse response, String operation) {
        if (response.status() != 200) {
            throw new ApiException(operation, response.status());
        }
        List<String> values = response.headers().allValues("Content-Type");
        if (values.size() != 1 || !isJsonMediaType(values.get(0))) {
            throw new ProtocolException(operation);
        }
    }

    private static boolean isJsonMediaType(String value) {
        String first = value.split(";", 2)[0].trim().toLowerCase(Locale.ROOT);
        return "application/json".equals(first);
    }

    private static String validateOrigin(URI uri, String name) {
        if (uri == null || !uri.isAbsolute() || uri.isOpaque()) {
            throw new IllegalArgumentException(name + " must be absolute");
        }
        String scheme = uri.getScheme();
        if (!"http".equalsIgnoreCase(scheme)
                && !"https".equalsIgnoreCase(scheme)) {
            throw new IllegalArgumentException(name + " must use HTTP(S)");
        }
        String rawPath = uri.getRawPath();
        if (uri.getHost() == null
                || uri.getUserInfo() != null
                || (rawPath != null
                        && !rawPath.isEmpty()
                        && !"/".equals(rawPath))
                || uri.getRawQuery() != null
                || uri.getRawFragment() != null) {
            throw new IllegalArgumentException(name + " must be an origin");
        }
        String value = uri.toString();
        return value.endsWith("/")
                ? value.substring(0, value.length() - 1)
                : value;
    }

    private static void validateCredential(String value, String name) {
        if (value == null
                || value.isBlank()
                || value.indexOf('\r') >= 0
                || value.indexOf('\n') >= 0) {
            throw new IllegalArgumentException(name + " is invalid");
        }
    }

    private static void validateRequired(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " is required");
        }
    }

    private static String encodeSegment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder encoded = new StringBuilder(bytes.length);
        char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte item : bytes) {
            int unsigned = item & 0xff;
            if ((unsigned >= 'a' && unsigned <= 'z')
                    || (unsigned >= 'A' && unsigned <= 'Z')
                    || (unsigned >= '0' && unsigned <= '9')
                    || unsigned == '-'
                    || unsigned == '.'
                    || unsigned == '_'
                    || unsigned == '~') {
                encoded.append((char) unsigned);
            } else {
                encoded.append('%');
                encoded.append(hex[unsigned >>> 4]);
                encoded.append(hex[unsigned & 0x0f]);
            }
        }
        return encoded.toString();
    }

    private static String jsonString(String value) {
        StringBuilder result = new StringBuilder(value.length() + 2);
        result.append('"');
        for (int index = 0; index < value.length(); index++) {
            char item = value.charAt(index);
            switch (item) {
                case '"' -> result.append("\\\"");
                case '\\' -> result.append("\\\\");
                case '\b' -> result.append("\\b");
                case '\f' -> result.append("\\f");
                case '\n' -> result.append("\\n");
                case '\r' -> result.append("\\r");
                case '\t' -> result.append("\\t");
                default -> {
                    if (item < 0x20) {
                        result.append(String.format("\\u%04X", (int) item));
                    } else {
                        result.append(item);
                    }
                }
            }
        }
        return result.append('"').toString();
    }

    private static Object parseJson(byte[] bytes, String operation) {
        try {
            String text = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString();
            return new JsonParser(text).parse();
        } catch (CharacterCodingException | IllegalArgumentException error) {
            throw new ProtocolException(operation);
        }
    }

    private static Map<String, Object> stringMap(
            Map<?, ?> input, String operation) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (Map.Entry<?, ?> entry : input.entrySet()) {
            if (!(entry.getKey() instanceof String key)) {
                throw new ProtocolException(operation);
            }
            result.put(key, entry.getValue());
        }
        return result;
    }

    private static Map<String, Object> requiredObject(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof Map<?, ?> map)) {
            throw new ProtocolException(operation);
        }
        return stringMap(map, operation);
    }

    private static List<?> requiredList(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof List<?> list)) {
            throw new ProtocolException(operation);
        }
        return list;
    }

    private static String requiredString(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof String text)) {
            throw new ProtocolException(operation);
        }
        return text;
    }

    private static String requiredNonblankString(
            Map<String, Object> object, String key, String operation) {
        String value = requiredString(object, key, operation);
        if (value.isBlank()) {
            throw new ProtocolException(operation);
        }
        return value;
    }

    private static boolean requiredBoolean(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof Boolean flag)) {
            throw new ProtocolException(operation);
        }
        return flag;
    }

    private static long requiredLong(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof BigDecimal number)) {
            throw new ProtocolException(operation);
        }
        try {
            return number.longValueExact();
        } catch (ArithmeticException error) {
            throw new ProtocolException(operation);
        }
    }

    private static Object deepFreeze(Object value) {
        if (value instanceof Map<?, ?> map) {
            Map<String, Object> copy = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                copy.put(String.valueOf(entry.getKey()), deepFreeze(entry.getValue()));
            }
            return Collections.unmodifiableMap(copy);
        }
        if (value instanceof List<?> list) {
            List<Object> copy = new ArrayList<>();
            for (Object item : list) {
                copy.add(deepFreeze(item));
            }
            return Collections.unmodifiableList(copy);
        }
        return value;
    }

    private static final class JsonParser {
        private final String input;
        private int offset;

        private JsonParser(String input) {
            this.input = input;
        }

        private Object parse() {
            skipSpace();
            Object value = value();
            skipSpace();
            if (offset != input.length()) {
                throw new IllegalArgumentException("trailing JSON");
            }
            return value;
        }

        private Object value() {
            if (offset >= input.length()) {
                throw new IllegalArgumentException("missing JSON value");
            }
            return switch (input.charAt(offset)) {
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
            expect('{');
            skipSpace();
            Map<String, Object> result = new LinkedHashMap<>();
            if (consume('}')) {
                return result;
            }
            while (true) {
                skipSpace();
                String key = string();
                skipSpace();
                expect(':');
                skipSpace();
                if (result.containsKey(key)) {
                    throw new IllegalArgumentException("duplicate key");
                }
                result.put(key, value());
                skipSpace();
                if (consume('}')) {
                    return result;
                }
                expect(',');
                skipSpace();
            }
        }

        private List<Object> array() {
            expect('[');
            skipSpace();
            List<Object> result = new ArrayList<>();
            if (consume(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                skipSpace();
                if (consume(']')) {
                    return result;
                }
                expect(',');
                skipSpace();
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (offset < input.length()) {
                char item = input.charAt(offset++);
                if (item == '"') {
                    return result.toString();
                }
                if (item == '\\') {
                    if (offset >= input.length()) {
                        throw new IllegalArgumentException("bad escape");
                    }
                    char escaped = input.charAt(offset++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicodeEscape());
                        default -> throw new IllegalArgumentException("bad escape");
                    }
                } else {
                    if (item < 0x20) {
                        throw new IllegalArgumentException("control in string");
                    }
                    result.append(item);
                }
            }
            throw new IllegalArgumentException("unterminated string");
        }

        private char unicodeEscape() {
            if (offset + 4 > input.length()) {
                throw new IllegalArgumentException("short unicode escape");
            }
            int value = 0;
            for (int count = 0; count < 4; count++) {
                int digit = Character.digit(input.charAt(offset++), 16);
                if (digit < 0) {
                    throw new IllegalArgumentException("bad unicode escape");
                }
                value = (value << 4) | digit;
            }
            return (char) value;
        }

        private Object literal(String text, Object value) {
            if (!input.startsWith(text, offset)) {
                throw new IllegalArgumentException("bad literal");
            }
            offset += text.length();
            return value;
        }

        private BigDecimal number() {
            int start = offset;
            consume('-');
            if (consume('0')) {
                // A zero integer component is complete.
            } else {
                digits();
            }
            if (consume('.')) {
                digits();
            }
            if (consume('e') || consume('E')) {
                if (!consume('+')) {
                    consume('-');
                }
                digits();
            }
            if (start == offset) {
                throw new IllegalArgumentException("bad number");
            }
            return new BigDecimal(input.substring(start, offset));
        }

        private void digits() {
            int start = offset;
            while (offset < input.length()
                    && input.charAt(offset) >= '0'
                    && input.charAt(offset) <= '9') {
                offset++;
            }
            if (start == offset) {
                throw new IllegalArgumentException("digits required");
            }
        }

        private void skipSpace() {
            while (offset < input.length()) {
                char item = input.charAt(offset);
                if (item == ' ' || item == '\n' || item == '\r' || item == '\t') {
                    offset++;
                } else {
                    return;
                }
            }
        }

        private boolean consume(char expected) {
            if (offset < input.length() && input.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!consume(expected)) {
                throw new IllegalArgumentException("unexpected JSON token");
            }
        }
    }
}
