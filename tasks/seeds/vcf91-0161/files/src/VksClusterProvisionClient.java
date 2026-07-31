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
 * Dependency-free client for a guarded VKS Cluster API create.
 *
 * Complete this file without adding another production source file.
 */
public final class VksClusterProvisionClient {
    private static final int MAX_RESPONSE_BYTES = 64 * 1024;
    private static final String VCENTER_OPERATION =
            "Vcenter.Namespaces.Instances_getV2";
    private static final String KUBERNETES_OPERATION =
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:create";

    public record Config(
            URI vcenterEndpoint,
            URI kubernetesEndpoint,
            String vcenterSessionId,
            String kubernetesBearerToken,
            Duration requestTimeout) {
    }

    public record ClusterRequest(
            String supervisorNamespace,
            String expectedSupervisor,
            String clusterName,
            String clusterClass,
            String kubernetesVersion,
            String vmClass,
            String storageClass,
            String classNamespace,
            Integer controlPlaneReplicas,
            String workerPoolName,
            Integer workerReplicas,
            String serviceDomain) {
    }

    public record ProvisionedCluster(
            String namespace,
            String name,
            String uid,
            String resourceVersion,
            String phase) {
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

    public VksClusterProvisionClient(Config config) {
        this(config, null);
    }

    VksClusterProvisionClient(Config config, Exchange suppliedExchange) {
        this.config = Objects.requireNonNull(config, "config");
        this.vcenterOrigin =
                validateOrigin(config.vcenterEndpoint(), "vcenterEndpoint");
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
                } catch (IOException error) {
                    throw new ClientException(operation + " transport failed");
                }
                byte[] responseBody;
                try (InputStream stream = response.body()) {
                    responseBody = readLimited(stream, operation);
                } catch (IOException error) {
                    throw new ClientException(
                            operation + " response read failed");
                }
                return new WireResponse(response.statusCode(), responseBody);
            };
        } else {
            this.exchange = suppliedExchange;
        }
    }

    public ProvisionedCluster createIfNamespaceReady(ClusterRequest request)
            throws InterruptedException {
        throw new UnsupportedOperationException(
                "TODO: validate, precheck, then create");
    }

    private void requireReadyNamespace(String namespace, String expectedSupervisor)
            throws InterruptedException {
        WireResponse response = send(
                VCENTER_OPERATION,
                vcenterOrigin
                        + "/api/vcenter/namespaces/instances/v2/"
                        + encodeSegment(namespace),
                true,
                null);
        requireStatus(response, VCENTER_OPERATION, 200);
        Map<String, Object> value =
                parseObject(response.body(), VCENTER_OPERATION);
        String supervisor =
                requiredNonblankString(value, "supervisor", VCENTER_OPERATION);
        String configStatus =
                requiredNonblankString(value, "config_status", VCENTER_OPERATION);
        requiredString(value, "description", VCENTER_OPERATION);
        requiredList(value, "messages", VCENTER_OPERATION);
        requiredList(value, "access_list", VCENTER_OPERATION);
        requiredList(value, "storage_specs", VCENTER_OPERATION);
        Map<String, Object> stats =
                requiredObject(value, "stats", VCENTER_OPERATION);
        requiredLong(stats, "cpu_used", VCENTER_OPERATION);
        requiredLong(stats, "memory_used", VCENTER_OPERATION);
        requiredLong(stats, "storage_used", VCENTER_OPERATION);

        if (!List.of("CONFIGURING", "REMOVING", "RUNNING", "ERROR")
                .contains(configStatus)) {
            throw new ProtocolException(VCENTER_OPERATION);
        }
        if (!supervisor.equals(expectedSupervisor)) {
            throw new ProtocolException(VCENTER_OPERATION);
        }
        if (!configStatus.equals("RUNNING")) {
            throw new NamespaceNotReadyException();
        }
    }

    private ProvisionedCluster decodeCreatedCluster(
            byte[] body, ClusterRequest request) {
        Map<String, Object> value = parseObject(body, KUBERNETES_OPERATION);
        if (!"cluster.x-k8s.io/v1beta2".equals(
                        requiredString(
                                value, "apiVersion", KUBERNETES_OPERATION))
                || !"Cluster".equals(
                        requiredString(value, "kind", KUBERNETES_OPERATION))) {
            throw new ProtocolException(KUBERNETES_OPERATION);
        }
        Map<String, Object> metadata =
                requiredObject(value, "metadata", KUBERNETES_OPERATION);
        String name = requiredNonblankString(
                metadata, "name", KUBERNETES_OPERATION);
        String namespace = requiredNonblankString(
                metadata, "namespace", KUBERNETES_OPERATION);
        String uid = requiredNonblankString(
                metadata, "uid", KUBERNETES_OPERATION);
        String resourceVersion = requiredNonblankString(
                metadata, "resourceVersion", KUBERNETES_OPERATION);
        String phase = requiredNonblankString(
                requiredObject(value, "status", KUBERNETES_OPERATION),
                "phase",
                KUBERNETES_OPERATION);
        if (!name.equals(request.clusterName())
                || !namespace.equals(request.supervisorNamespace())) {
            throw new ProtocolException(KUBERNETES_OPERATION);
        }
        return new ProvisionedCluster(
                namespace, name, uid, resourceVersion, phase);
    }

    private static void validateRequest(ClusterRequest request) {
        Objects.requireNonNull(request, "request");
        validateRequired(request.supervisorNamespace(), "supervisorNamespace");
        validateRequired(request.expectedSupervisor(), "expectedSupervisor");
        validateRequired(request.clusterName(), "clusterName");
        validateRequired(request.clusterClass(), "clusterClass");
        validateRequired(request.kubernetesVersion(), "kubernetesVersion");
        validateRequired(request.vmClass(), "vmClass");
        validateRequired(request.storageClass(), "storageClass");
        validateOptional(request.classNamespace(), "classNamespace");
        validateOptional(request.workerPoolName(), "workerPoolName");
        validateOptional(request.serviceDomain(), "serviceDomain");
        if (request.controlPlaneReplicas() != null
                && request.controlPlaneReplicas() < 1) {
            throw new IllegalArgumentException(
                    "controlPlaneReplicas must be positive");
        }
        if ((request.workerPoolName() == null)
                != (request.workerReplicas() == null)) {
            throw new IllegalArgumentException(
                    "workerPoolName and workerReplicas must be set together");
        }
        if (request.workerReplicas() != null && request.workerReplicas() < 0) {
            throw new IllegalArgumentException(
                    "workerReplicas must not be negative");
        }
    }

    private static String createClusterBody(ClusterRequest request) {
        throw new UnsupportedOperationException(
                "TODO: serialize the Cluster create body");
    }

    private WireResponse send(
            String operation, String uri, boolean vcenter, String body)
            throws InterruptedException {
        HttpRequest.Builder builder;
        try {
            builder = HttpRequest.newBuilder(URI.create(uri))
                    .timeout(config.requestTimeout())
                    .header("Accept", "application/json");
        } catch (IllegalArgumentException error) {
            throw new ClientException(operation + " request could not be constructed");
        }
        if (vcenter) {
            builder.header(
                    "vmware-api-session-id", config.vcenterSessionId());
            builder.GET();
        } else {
            builder.header(
                            "Authorization",
                            "Bearer " + config.kubernetesBearerToken())
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofByteArray(
                            Objects.requireNonNull(body, "body")
                                    .getBytes(StandardCharsets.UTF_8)));
        }

        return exchange.send(operation, builder.build());
    }

    private static byte[] readLimited(InputStream stream, String operation)
            throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int total = 0;
        while (true) {
            int read = stream.read(buffer);
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
        String rawPath = uri.getRawPath();
        if ((!"http".equalsIgnoreCase(scheme)
                        && !"https".equalsIgnoreCase(scheme))
                || uri.getHost() == null
                || uri.getRawAuthority() == null
                || uri.getRawAuthority().isBlank()
                || uri.getRawUserInfo() != null
                || (rawPath != null
                        && !rawPath.isEmpty()
                        && !rawPath.equals("/"))
                || uri.getRawQuery() != null
                || uri.getRawFragment() != null
                || uri.getPort() > 65535) {
            throw new IllegalArgumentException(name + " must be an HTTP(S) origin");
        }
        return scheme.toLowerCase()
                + "://"
                + uri.getRawAuthority();
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

    private static void validateOptional(String value, String name) {
        if (value != null && value.isBlank()) {
            throw new IllegalArgumentException(name + " must be null or nonblank");
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
                encoded.append('%')
                        .append(hex[unsigned >>> 4])
                        .append(hex[unsigned & 0x0f]);
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
                        result.append("\\u");
                        String hex = Integer.toHexString(item).toUpperCase();
                        result.append("0".repeat(4 - hex.length())).append(hex);
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

    private static String requiredNonblankString(
            Map<String, Object> object, String key, String operation) {
        String value = requiredString(object, key, operation);
        if (value.isBlank()) {
            throw new ProtocolException(operation);
        }
        return value;
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
        private int depth;

        private JsonParser(String input) {
            this.input = input;
        }

        private Object parse() {
            skipSpace();
            Object result = value();
            skipSpace();
            if (offset != input.length()) {
                throw new IllegalArgumentException("trailing JSON");
            }
            return result;
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
            enter();
            try {
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
            } finally {
                depth--;
            }
        }

        private List<Object> array() {
            enter();
            try {
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
            } finally {
                depth--;
            }
        }

        private void enter() {
            depth++;
            if (depth > 64) {
                throw new IllegalArgumentException("JSON nesting too deep");
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
            if (!consume('0')) {
                digits();
            } else if (offset < input.length()
                    && Character.isDigit(input.charAt(offset))) {
                throw new IllegalArgumentException("leading zero");
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
                    && Character.isDigit(input.charAt(offset))) {
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
