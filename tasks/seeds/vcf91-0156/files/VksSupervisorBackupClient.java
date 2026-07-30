import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigDecimal;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Dependency-free client for a guarded Supervisor backup.
 *
 * Complete this file without adding another production source file.
 */
public final class VksSupervisorBackupClient {
    private static final int MAX_RESPONSE_BYTES = 64 * 1024;

    public record Config(
            URI vcenterEndpoint,
            URI kubernetesEndpoint,
            String vcenterSessionId,
            String kubernetesBearerToken,
            Duration requestTimeout,
            Duration pollInterval,
            int maxPolls) {
    }

    public record BackupRequest(
            String supervisorNamespace,
            String expectedSupervisor,
            String workloadNamespace,
            String deployment,
            String comment,
            Boolean ignoreHealthCheckFailure) {
    }

    public record BackupResult(
            String taskId,
            String status,
            int polls,
            String supervisor,
            String deployment) {
    }

    public static class ClientException extends RuntimeException {
        public ClientException(String message) {
            super(message);
        }
    }

    public static final class ApiException extends ClientException {
        private final String operation;
        private final int statusCode;

        public ApiException(String operation, int statusCode) {
            super(operation + " failed with HTTP " + statusCode);
            this.operation = operation;
            this.statusCode = statusCode;
        }

        public String operation() {
            return operation;
        }

        public int statusCode() {
            return statusCode;
        }
    }

    public static final class ProtocolException extends ClientException {
        public ProtocolException(String operation) {
            super(operation + " returned an invalid success response");
        }
    }

    public static final class NamespaceNotReadyException extends ClientException {
        public NamespaceNotReadyException() {
            super("Supervisor namespace is not ready");
        }
    }

    public static final class DeploymentNotStableException extends ClientException {
        public DeploymentNotStableException() {
            super("VKS deployment is not stable");
        }
    }

    public static final class TaskFailedException extends ClientException {
        public TaskFailedException() {
            super("Supervisor backup task failed");
        }
    }

    public static final class PollLimitException extends ClientException {
        public PollLimitException() {
            super("Supervisor backup task did not reach a terminal state");
        }
    }

    static record WireResponse(int status, byte[] body) {
    }

    interface Exchange {
        WireResponse send(String operation, HttpRequest request)
                throws InterruptedException;
    }

    private final Config config;
    private final Exchange exchange;
    private final String vcenterOrigin;
    private final String kubernetesOrigin;

    public VksSupervisorBackupClient(Config config) {
        this(config, null);
    }

    VksSupervisorBackupClient(Config config, Exchange suppliedExchange) {
        this.config = Objects.requireNonNull(config, "config");
        this.vcenterOrigin = validateOrigin(config.vcenterEndpoint(), "vcenterEndpoint");
        this.kubernetesOrigin =
                validateOrigin(config.kubernetesEndpoint(), "kubernetesEndpoint");
        validateCredential(config.vcenterSessionId(), "vcenterSessionId");
        validateCredential(config.kubernetesBearerToken(), "kubernetesBearerToken");
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
                    .build();
            this.exchange = (operation, request) -> {
                HttpResponse<InputStream> response;
                try {
                    response = httpClient.send(
                            request, HttpResponse.BodyHandlers.ofInputStream());
                } catch (IOException error) {
                    throw new ClientException(operation + " transport failed");
                }
                byte[] responseBody;
                try (InputStream input = response.body()) {
                    responseBody = readLimited(input, operation);
                } catch (IOException error) {
                    throw new ClientException(operation + " response read failed");
                }
                return new WireResponse(response.statusCode(), responseBody);
            };
        } else {
            this.exchange = suppliedExchange;
        }
    }

    public BackupResult backupWhenDeploymentStable(BackupRequest request)
            throws InterruptedException {
        Objects.requireNonNull(request, "request");
        validateRequired(request.supervisorNamespace(), "supervisorNamespace");
        validateRequired(request.expectedSupervisor(), "expectedSupervisor");
        validateRequired(request.workloadNamespace(), "workloadNamespace");
        validateRequired(request.deployment(), "deployment");
        if (request.comment() != null && request.comment().isBlank()) {
            throw new IllegalArgumentException("comment must be null or nonblank");
        }

        String supervisor = getRunningNamespace(
                request.supervisorNamespace(), request.expectedSupervisor());
        requireStableDeployment(request.workloadNamespace(), request.deployment());
        String body = createBackupBody(
                request.comment(), request.ignoreHealthCheckFailure());
        String taskId = createBackup(supervisor, body);
        return pollTask(taskId, supervisor, request.deployment());
    }

    private String getRunningNamespace(String namespace, String expectedSupervisor)
            throws InterruptedException {
        String operation = "Vcenter.Namespaces.Instances_getV2";
        WireResponse response = send(
                operation,
                vcenterOrigin
                        + "/api/vcenter/namespaces/instances/v2/"
                        + encodeSegment(namespace),
                "GET",
                null,
                true);
        requireStatus(response, operation, 200);
        Map<String, Object> info = parseObject(response.body(), operation);
        String supervisor = requiredString(info, "supervisor", operation);
        String status = requiredString(info, "config_status", operation);
        requiredString(info, "description", operation);
        requiredList(info, "messages", operation);
        requiredList(info, "access_list", operation);
        requiredList(info, "storage_specs", operation);
        Map<String, Object> stats = requiredObject(info, "stats", operation);
        requiredLong(stats, "cpu_used", operation);
        requiredLong(stats, "memory_used", operation);
        requiredLong(stats, "storage_used", operation);

        if (!List.of("CONFIGURING", "REMOVING", "RUNNING", "ERROR").contains(status)) {
            throw new ProtocolException(operation);
        }
        if (!supervisor.equals(expectedSupervisor)) {
            throw new ProtocolException(operation);
        }
        if (!status.equals("RUNNING")) {
            throw new NamespaceNotReadyException();
        }
        return supervisor;
    }

    private void requireStableDeployment(String namespace, String deployment)
            throws InterruptedException {
        String operation = "apps/v1:namespaced-deployments:read";
        WireResponse response = send(
                operation,
                kubernetesOrigin
                        + "/apis/apps/v1/namespaces/"
                        + encodeSegment(namespace)
                        + "/deployments/"
                        + encodeSegment(deployment),
                "GET",
                null,
                false);
        requireStatus(response, operation, 200);
        Map<String, Object> value = parseObject(response.body(), operation);
        if (!"apps/v1".equals(requiredString(value, "apiVersion", operation))
                || !"Deployment".equals(requiredString(value, "kind", operation))) {
            throw new ProtocolException(operation);
        }
        Map<String, Object> metadata = requiredObject(value, "metadata", operation);
        if (!deployment.equals(requiredString(metadata, "name", operation))
                || !namespace.equals(requiredString(metadata, "namespace", operation))) {
            throw new ProtocolException(operation);
        }
        long generation = requiredLong(metadata, "generation", operation);
        long desired = requiredLong(requiredObject(value, "spec", operation),
                "replicas", operation);
        Map<String, Object> status = requiredObject(value, "status", operation);
        long observed = requiredLong(status, "observedGeneration", operation);
        long available = requiredLong(status, "availableReplicas", operation);
        long updated = requiredLong(status, "updatedReplicas", operation);
        long unavailable = optionalLong(status, "unavailableReplicas", 0L, operation);
        if (desired <= 0
                || observed < generation
                || available != desired
                || updated != desired
                || unavailable != 0) {
            throw new DeploymentNotStableException();
        }
    }

    private String createBackup(String supervisor, String body)
            throws InterruptedException {
        String operation =
                "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create";
        WireResponse response = send(
                operation,
                vcenterOrigin
                        + "/api/vcenter/namespace-management/supervisors/"
                        + encodeSegment(supervisor)
                        + "/recovery/backup/jobs",
                "POST",
                body,
                true);
        requireStatus(response, operation, 200);
        Object parsed = parseJson(response.body(), operation);
        if (!(parsed instanceof String taskId) || taskId.isBlank()) {
            throw new ProtocolException(operation);
        }
        return taskId;
    }

    private String createBackupBody(String comment, Boolean ignoreHealthCheckFailure) {
        // TODO: serialize only fields that are set. Preserve an explicit false.
        throw new UnsupportedOperationException("TODO: create backup request body");
    }

    private BackupResult pollTask(String taskId, String supervisor, String deployment)
            throws InterruptedException {
        // TODO: poll through every nonterminal state until SUCCEEDED or FAILED.
        throw new UnsupportedOperationException("TODO: poll task");
    }

    private WireResponse send(
            String operation,
            String uri,
            String method,
            String body,
            boolean vcenter)
            throws InterruptedException {
        HttpRequest.Builder builder;
        try {
            builder = HttpRequest.newBuilder(URI.create(uri));
        } catch (IllegalArgumentException error) {
            throw new ClientException(operation + " request could not be constructed");
        }
        builder.timeout(config.requestTimeout());
        builder.header("Accept", "application/json");
        if (vcenter) {
            builder.header("vmware-api-session-id", config.vcenterSessionId());
        } else {
            builder.header(
                    "Authorization", "Bearer " + config.kubernetesBearerToken());
        }
        if ("POST".equals(method)) {
            builder.header("Content-Type", "application/json");
            builder.POST(HttpRequest.BodyPublishers.ofString(
                    Objects.requireNonNull(body, "body"), StandardCharsets.UTF_8));
        } else if ("GET".equals(method)) {
            builder.GET();
        } else {
            throw new IllegalArgumentException("unsupported method");
        }

        return exchange.send(operation, builder.build());
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
                throw new ClientException(operation + " response exceeded limit");
            }
            output.write(buffer, 0, read);
        }
    }

    private static void requireStatus(
            WireResponse response, String operation, int expected) {
        if (response.status() != expected) {
            throw new ApiException(operation, response.status());
        }
    }

    private static String validateOrigin(URI uri, String name) {
        if (uri == null || !uri.isAbsolute()) {
            throw new IllegalArgumentException(name + " must be absolute");
        }
        String scheme = uri.getScheme();
        if (!"http".equalsIgnoreCase(scheme) && !"https".equalsIgnoreCase(scheme)) {
            throw new IllegalArgumentException(name + " must use HTTP(S)");
        }
        String rawPath = uri.getRawPath();
        if (uri.getHost() == null
                || uri.getUserInfo() != null
                || (rawPath != null && !rawPath.isEmpty() && !rawPath.equals("/"))
                || uri.getRawQuery() != null
                || uri.getRawFragment() != null) {
            throw new IllegalArgumentException(name + " must be an origin");
        }
        String value = uri.toString();
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
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
        final char[] hex = "0123456789ABCDEF".toCharArray();
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
            return new JsonParser(new String(bytes, StandardCharsets.UTF_8)).parse();
        } catch (RuntimeException error) {
            if (error instanceof ProtocolException) {
                throw error;
            }
            throw new ProtocolException(operation);
        }
    }

    private static Map<String, Object> parseObject(byte[] bytes, String operation) {
        Object parsed = parseJson(bytes, operation);
        if (!(parsed instanceof Map<?, ?> map)) {
            throw new ProtocolException(operation);
        }
        return stringMap(map, operation);
    }

    private static Map<String, Object> requiredObject(
            Map<String, Object> object, String key, String operation) {
        Object value = object.get(key);
        if (!(value instanceof Map<?, ?> map)) {
            throw new ProtocolException(operation);
        }
        return stringMap(map, operation);
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

    private static long optionalLong(
            Map<String, Object> object,
            String key,
            long missing,
            String operation) {
        if (!object.containsKey(key)) {
            return missing;
        }
        return requiredLong(object, key, operation);
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
                if (result.putIfAbsent(key, value()) != null) {
                    throw new IllegalArgumentException("duplicate key");
                }
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
            if (consume('-')) {
                // optional sign
            }
            if (consume('0')) {
                // zero
            } else {
                digits();
            }
            if (consume('.')) {
                digits();
            }
            if (consume('e') || consume('E')) {
                if (consume('+') || consume('-')) {
                    // optional exponent sign
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
