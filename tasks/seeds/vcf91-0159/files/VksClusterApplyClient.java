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
 * Dependency-free client for one guarded VKS Cluster server-side apply.
 *
 * Complete this file without adding another production source file.
 */
public final class VksClusterApplyClient {
    public static final String NAMESPACE_OPERATION =
            "Vcenter.Namespaces.Instances_getV2";
    public static final String APPLY_OPERATION =
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:server-side-apply";

    private static final int MAX_RESPONSE_BYTES = 64 * 1024;

    public record Config(
            URI vcenterEndpoint,
            URI kubernetesEndpoint,
            String vcenterSessionId,
            String kubernetesBearerToken,
            Duration requestTimeout) {
    }

    public record ApplyRequest(
            String supervisor,
            String namespace,
            String clusterName,
            String fieldManager,
            String clusterClass,
            String kubernetesVersion,
            String vmClass,
            String storageClass,
            int controlPlaneReplicas,
            Integer workerReplicas,
            List<String> podCidrs,
            List<String> serviceCidrs,
            Boolean force) {
    }

    public record ApplyResult(
            String uid,
            String resourceVersion,
            long generation,
            int patchAttempts) {
    }

    public static class ClientException extends RuntimeException {
        public ClientException(String message) {
            super(message);
        }
    }

    public static final class ApiException extends ClientException {
        private final String operation;
        private final int statusCode;
        private final byte[] responseBody;

        public ApiException(String operation, int statusCode, byte[] responseBody) {
            super(operation + " failed with HTTP " + statusCode);
            this.operation = operation;
            this.statusCode = statusCode;
            this.responseBody = responseBody.clone();
        }

        public String operation() {
            return operation;
        }

        public int statusCode() {
            return statusCode;
        }

        public byte[] responseBody() {
            return responseBody.clone();
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
        private final String configStatus;

        public NamespaceNotReadyException(String configStatus) {
            super("Supervisor namespace is not RUNNING");
            this.configStatus = configStatus;
        }

        public String configStatus() {
            return configStatus;
        }
    }

    public static final class TransportException extends ClientException {
        private final String operation;

        public TransportException(String operation) {
            super(operation + " transport failed");
            this.operation = operation;
        }

        public String operation() {
            return operation;
        }
    }

    record WireResponse(int status, byte[] body) {
    }

    interface Exchange {
        WireResponse send(String operation, HttpRequest request)
                throws IOException, InterruptedException;
    }

    private final Config config;
    private final Exchange exchange;
    private final String vcenterOrigin;
    private final String kubernetesOrigin;

    public VksClusterApplyClient(Config config) {
        this(config, null);
    }

    VksClusterApplyClient(Config config, Exchange suppliedExchange) {
        this.config = Objects.requireNonNull(config, "config");
        this.vcenterOrigin = validateOrigin(config.vcenterEndpoint(), "vcenterEndpoint");
        this.kubernetesOrigin =
                validateOrigin(config.kubernetesEndpoint(), "kubernetesEndpoint");
        validateCredential(config.vcenterSessionId(), "vcenterSessionId");
        validateCredential(
                config.kubernetesBearerToken(), "kubernetesBearerToken");
        if (config.requestTimeout() == null
                || config.requestTimeout().isZero()
                || config.requestTimeout().isNegative()) {
            throw new IllegalArgumentException("requestTimeout must be positive");
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
                } catch (InterruptedException error) {
                    Thread.currentThread().interrupt();
                    throw error;
                }
                byte[] body;
                try (InputStream input = response.body()) {
                    body = readLimited(input, operation);
                }
                return new WireResponse(response.statusCode(), body);
            };
        } else {
            this.exchange = suppliedExchange;
        }
    }

    public ApplyResult apply(ApplyRequest request) throws InterruptedException {
        validateRequest(request);
        requireRunningNamespace(request.namespace(), request.supervisor());

        byte[] body = buildApplyBody(request);
        String target = kubernetesOrigin
                + "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                + encodeComponent(request.namespace())
                + "/clusters/"
                + encodeComponent(request.clusterName())
                + "?fieldManager="
                + encodeComponent(request.fieldManager());
        if (request.force() != null) {
            target += "&force=" + request.force().booleanValue();
        }

        HttpRequest patch = requestBuilder(target)
                .header("Accept", "application/json")
                .header(
                        "Authorization",
                        "Bearer " + config.kubernetesBearerToken())
                .header("Content-Type", "application/apply-patch+yaml")
                .method("PATCH", HttpRequest.BodyPublishers.ofByteArray(body))
                .build();

        return applyWithAmbiguousRetry(
                patch, request.namespace(), request.clusterName());
    }

    private void requireRunningNamespace(String namespace, String supervisor)
            throws InterruptedException {
        String target = vcenterOrigin
                + "/api/vcenter/namespaces/instances/v2/"
                + encodeComponent(namespace);
        HttpRequest get = requestBuilder(target)
                .header("Accept", "application/json")
                .header("vmware-api-session-id", config.vcenterSessionId())
                .GET()
                .build();

        WireResponse response;
        try {
            response = exchangeOnce(NAMESPACE_OPERATION, get);
        } catch (IOException error) {
            throw new TransportException(NAMESPACE_OPERATION);
        }
        requireStatus(response, NAMESPACE_OPERATION, 200);

        Map<String, Object> info = parseObject(response.body(), NAMESPACE_OPERATION);
        String actualSupervisor =
                requiredString(info, "supervisor", NAMESPACE_OPERATION);
        String status = requiredString(info, "config_status", NAMESPACE_OPERATION);
        requiredString(info, "description", NAMESPACE_OPERATION);
        requiredList(info, "messages", NAMESPACE_OPERATION);
        requiredList(info, "access_list", NAMESPACE_OPERATION);
        requiredList(info, "storage_specs", NAMESPACE_OPERATION);
        Map<String, Object> stats =
                requiredObject(info, "stats", NAMESPACE_OPERATION);
        requiredLong(stats, "cpu_used", NAMESPACE_OPERATION);
        requiredLong(stats, "memory_used", NAMESPACE_OPERATION);
        requiredLong(stats, "storage_used", NAMESPACE_OPERATION);

        if (actualSupervisor.isBlank()
                || !List.of("CONFIGURING", "REMOVING", "RUNNING", "ERROR")
                        .contains(status)
                || !actualSupervisor.equals(supervisor)) {
            throw new ProtocolException(NAMESPACE_OPERATION);
        }
        if (!"RUNNING".equals(status)) {
            throw new NamespaceNotReadyException(status);
        }
    }

    private static byte[] buildApplyBody(ApplyRequest request) {
        // TODO: build the compact apply object with exact ordering and omission.
        throw new UnsupportedOperationException("TODO: build apply body");
    }

    private ApplyResult applyWithAmbiguousRetry(
            HttpRequest patch, String namespace, String clusterName)
            throws InterruptedException {
        // TODO: retry the exact same immutable request once after the first
        // IOException, and never retry an HTTP or protocol response.
        throw new UnsupportedOperationException("TODO: apply with safe replay");
    }

    private ApplyResult parseApplyResponse(
            WireResponse response,
            String namespace,
            String clusterName,
            int attempts) {
        requireStatus(response, APPLY_OPERATION, 200);
        Map<String, Object> value = parseObject(response.body(), APPLY_OPERATION);
        if (!"cluster.x-k8s.io/v1beta2".equals(
                        requiredString(value, "apiVersion", APPLY_OPERATION))
                || !"Cluster".equals(
                        requiredString(value, "kind", APPLY_OPERATION))) {
            throw new ProtocolException(APPLY_OPERATION);
        }
        Map<String, Object> metadata =
                requiredObject(value, "metadata", APPLY_OPERATION);
        String actualName = requiredString(metadata, "name", APPLY_OPERATION);
        String actualNamespace =
                requiredString(metadata, "namespace", APPLY_OPERATION);
        String uid = requiredString(metadata, "uid", APPLY_OPERATION);
        String resourceVersion =
                requiredString(metadata, "resourceVersion", APPLY_OPERATION);
        long generation =
                requiredLong(metadata, "generation", APPLY_OPERATION);
        if (!clusterName.equals(actualName)
                || !namespace.equals(actualNamespace)
                || uid.isBlank()
                || resourceVersion.isBlank()
                || generation <= 0) {
            throw new ProtocolException(APPLY_OPERATION);
        }
        return new ApplyResult(uid, resourceVersion, generation, attempts);
    }

    private WireResponse exchangeOnce(String operation, HttpRequest request)
            throws IOException, InterruptedException {
        try {
            return exchange.send(operation, request);
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw error;
        }
    }

    private HttpRequest.Builder requestBuilder(String target) {
        try {
            return HttpRequest.newBuilder(URI.create(target))
                    .timeout(config.requestTimeout());
        } catch (IllegalArgumentException error) {
            throw new ClientException("request could not be constructed");
        }
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
            throw new ApiException(operation, response.status(), response.body());
        }
    }

    private static void validateRequest(ApplyRequest request) {
        Objects.requireNonNull(request, "request");
        validateRequired(request.supervisor(), "supervisor");
        validateRequired(request.namespace(), "namespace");
        validateRequired(request.clusterName(), "clusterName");
        validateRequired(request.fieldManager(), "fieldManager");
        validateRequired(request.clusterClass(), "clusterClass");
        validateRequired(request.kubernetesVersion(), "kubernetesVersion");
        validateRequired(request.vmClass(), "vmClass");
        validateRequired(request.storageClass(), "storageClass");
        if (request.controlPlaneReplicas() < 1) {
            throw new IllegalArgumentException(
                    "controlPlaneReplicas must be positive");
        }
        if (request.workerReplicas() != null
                && request.workerReplicas().intValue() < 0) {
            throw new IllegalArgumentException(
                    "workerReplicas must not be negative");
        }
        validateCidrs(request.podCidrs(), "podCidrs");
        validateCidrs(request.serviceCidrs(), "serviceCidrs");
    }

    private static void validateCidrs(List<String> values, String name) {
        if (values == null) {
            throw new IllegalArgumentException(name + " must not be null");
        }
        for (String value : values) {
            if (value == null || value.isBlank()) {
                throw new IllegalArgumentException(
                        name + " must not contain blank values");
            }
        }
    }

    private static String validateOrigin(URI uri, String name) {
        if (uri == null || !uri.isAbsolute()) {
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
                        && !rawPath.equals("/"))
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

    private static String encodeComponent(String value) {
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
            return new JsonParser(
                            new String(bytes, StandardCharsets.UTF_8))
                    .parse();
        } catch (RuntimeException error) {
            if (error instanceof ProtocolException) {
                throw error;
            }
            throw new ProtocolException(operation);
        }
    }

    private static Map<String, Object> parseObject(
            byte[] bytes, String operation) {
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
                // zero
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
                if (item == ' '
                        || item == '\n'
                        || item == '\r'
                        || item == '\t') {
                    offset++;
                } else {
                    return;
                }
            }
        }

        private boolean consume(char expected) {
            if (offset < input.length()
                    && input.charAt(offset) == expected) {
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
