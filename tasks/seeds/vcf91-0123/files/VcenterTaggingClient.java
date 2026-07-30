import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Single-file client for the focused vCenter tagging workflow.
 *
 * The client is intended to follow the operation and schema projection in
 * docs/contract.json and intentionally makes no discovery or rollback calls.
 */
public final class VcenterTaggingClient {
    private static final String CATEGORY_CREATE =
            "Cis.Tagging.Category_create";
    private static final String TAG_CREATE =
            "Cis.Tagging.Tag_create";
    private static final String TAG_ATTACH =
            "Cis.Tagging.TagAssociation_attach";

    private final String origin;
    private final String sessionId;
    private final HttpClient httpClient;

    public enum StepStatus {
        SUCCEEDED,
        FAILED
    }

    public record CategorySpec(
            String name,
            String description,
            String cardinality,
            List<String> associableTypes,
            String categoryId) {
        public CategorySpec {
            name = required(name, "category name");
            description = Objects.requireNonNull(
                    description, "category description");
            cardinality = required(cardinality, "category cardinality");
            associableTypes = List.copyOf(
                    Objects.requireNonNull(
                            associableTypes, "associableTypes"));
            optionalId(categoryId, "categoryId");
        }
    }

    public record TagSpec(
            String name,
            String description,
            String tagId) {
        public TagSpec {
            name = required(name, "tag name");
            description = Objects.requireNonNull(
                    description, "tag description");
            optionalId(tagId, "tagId");
        }
    }

    public record DynamicId(String type, String id) {
        public DynamicId {
            type = required(type, "target type");
            id = required(id, "target id");
        }
    }

    public record ChangeRequest(
            CategorySpec category,
            TagSpec tag,
            DynamicId target) {
        public ChangeRequest {
            Objects.requireNonNull(category, "category");
            Objects.requireNonNull(tag, "tag");
            Objects.requireNonNull(target, "target");
        }
    }

    public record StepOutcome(
            String operationId,
            StepStatus status,
            String resourceId,
            VcenterApiException error) {
    }

    public record ChangeReport(List<StepOutcome> outcomes) {
        public ChangeReport {
            outcomes = List.copyOf(outcomes);
        }
    }

    public static final class VcenterApiException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        private final int statusCode;
        private final String errorType;

        public VcenterApiException(
                int statusCode,
                String errorType,
                String message) {
            super(message);
            this.statusCode = statusCode;
            this.errorType = errorType;
        }

        public int statusCode() {
            return statusCode;
        }

        public String errorType() {
            return errorType;
        }
    }

    public VcenterTaggingClient(
            URI serverOrigin,
            String sessionId,
            HttpClient httpClient) {
        Objects.requireNonNull(serverOrigin, "serverOrigin");
        if (!serverOrigin.isAbsolute()
                || serverOrigin.isOpaque()
                || serverOrigin.getHost() == null
                || !("http".equalsIgnoreCase(serverOrigin.getScheme())
                || "https".equalsIgnoreCase(serverOrigin.getScheme()))
                || serverOrigin.getUserInfo() != null
                || serverOrigin.getRawQuery() != null
                || serverOrigin.getRawFragment() != null
                || !(serverOrigin.getRawPath().isEmpty()
                || "/".equals(serverOrigin.getRawPath()))) {
            throw new IllegalArgumentException(
                    "serverOrigin must be an HTTP(S) origin");
        }
        if (sessionId == null || sessionId.isBlank()) {
            throw new IllegalArgumentException(
                    "sessionId must not be blank");
        }
        this.origin = serverOrigin.getScheme().toLowerCase()
                + "://" + serverOrigin.getRawAuthority();
        this.sessionId = sessionId;
        this.httpClient = Objects.requireNonNull(
                httpClient, "httpClient");
    }

    public ChangeReport createAndAttach(ChangeRequest request) {
        Objects.requireNonNull(request, "request");
        List<StepOutcome> outcomes = new ArrayList<>();

        String categoryId;
        try {
            categoryId = createCategory(request.category());
            outcomes.add(succeeded(CATEGORY_CREATE, categoryId));
        } catch (VcenterApiException error) {
            outcomes.add(failed(CATEGORY_CREATE, error));
            return new ChangeReport(outcomes);
        }

        String tagId;
        try {
            tagId = createTag(request.tag(), categoryId);
            outcomes.add(succeeded(TAG_CREATE, tagId));
        } catch (VcenterApiException error) {
            outcomes.add(failed(TAG_CREATE, error));
            return new ChangeReport(outcomes);
        }

        try {
            attach(tagId, request.target());
            outcomes.add(succeeded(TAG_ATTACH, null));
        } catch (VcenterApiException error) {
            // TODO: preserve the two successful create outcomes and this
            // failed attachment in the returned report.
            throw error;
        }
        return new ChangeReport(outcomes);
    }

    private String createCategory(CategorySpec spec) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", spec.name());
        body.put("description", spec.description());
        body.put("cardinality", spec.cardinality());
        body.put("associable_types", spec.associableTypes());
        // TODO: the server generates this identifier when it is unset.
        body.put("category_id",
                spec.categoryId() == null ? "" : spec.categoryId());
        HttpResponse<String> response = post(
                "/api/cis/tagging/category", body);
        if (response.statusCode() != 201) {
            throw decodeError(response);
        }
        return responseIdentifier(response, CATEGORY_CREATE);
    }

    private String createTag(TagSpec spec, String categoryId) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", spec.name());
        body.put("description", spec.description());
        body.put("category_id", categoryId);
        // TODO: the server generates this identifier when it is unset.
        body.put("tag_id", spec.tagId() == null ? "" : spec.tagId());
        HttpResponse<String> response = post(
                "/api/cis/tagging/tag", body);
        if (response.statusCode() != 201) {
            throw decodeError(response);
        }
        return responseIdentifier(response, TAG_CREATE);
    }

    private void attach(String tagId, DynamicId target) {
        Map<String, Object> objectId = new LinkedHashMap<>();
        objectId.put("type", target.type());
        objectId.put("id", target.id());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("object_id", objectId);
        HttpResponse<String> response = post(
                "/api/cis/tagging/tag-association/"
                        + pathSegment(tagId) + "?action=attach",
                body);
        if (response.statusCode() != 204) {
            throw decodeError(response);
        }
    }

    private HttpResponse<String> post(
            String pathAndQuery, Map<String, Object> body) {
        HttpRequest request = HttpRequest.newBuilder(
                        URI.create(origin + pathAndQuery))
                .header("vmware-api-session-id", sessionId)
                .header("Accept", "application/json")
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(
                        Json.write(body), StandardCharsets.UTF_8))
                .build();
        try {
            return httpClient.send(
                    request, HttpResponse.BodyHandlers.ofString(
                            StandardCharsets.UTF_8));
        } catch (IOException error) {
            throw new VcenterApiException(
                    0, "TRANSPORT_ERROR", "vCenter request failed");
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new VcenterApiException(
                    0, "INTERRUPTED", "vCenter request interrupted");
        }
    }

    private static String responseIdentifier(
            HttpResponse<String> response, String operationId) {
        try {
            Object parsed = Json.read(response.body());
            if (parsed instanceof String id && !id.isBlank()) {
                return id;
            }
        } catch (RuntimeException ignored) {
            // Converted below into a stable, credential-free API exception.
        }
        throw new VcenterApiException(
                response.statusCode(),
                "INVALID_RESPONSE",
                operationId + " returned no identifier");
    }

    private static VcenterApiException decodeError(
            HttpResponse<String> response) {
        String errorType = null;
        String message = null;
        try {
            Map<String, Object> envelope =
                    object(Json.read(response.body()));
            Object type = envelope.get("error_type");
            if (type instanceof String value) {
                errorType = value;
            }
            Object messages = envelope.get("messages");
            if (messages instanceof List<?> list && !list.isEmpty()
                    && list.get(0) instanceof Map<?, ?> first) {
                Object defaultMessage = first.get("default_message");
                if (defaultMessage instanceof String value) {
                    message = value;
                }
            }
        } catch (RuntimeException ignored) {
            // Use the status-only fallback below.
        }
        if (message == null || message.isBlank()) {
            message = "vCenter operation returned HTTP "
                    + response.statusCode();
        }
        return new VcenterApiException(
                response.statusCode(), errorType, message);
    }

    private static StepOutcome succeeded(
            String operationId, String resourceId) {
        return new StepOutcome(
                operationId, StepStatus.SUCCEEDED, resourceId, null);
    }

    private static StepOutcome failed(
            String operationId, VcenterApiException error) {
        return new StepOutcome(
                operationId, StepStatus.FAILED, null, error);
    }

    private static String required(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value;
    }

    private static void optionalId(String value, String name) {
        if (value != null && value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must be null or non-blank");
        }
    }

    private static String pathSegment(String value) {
        StringBuilder encoded = new StringBuilder();
        for (byte item : value.getBytes(StandardCharsets.UTF_8)) {
            int octet = item & 0xff;
            if ((octet >= 'a' && octet <= 'z')
                    || (octet >= 'A' && octet <= 'Z')
                    || (octet >= '0' && octet <= '9')
                    || octet == '-' || octet == '.'
                    || octet == '_' || octet == '~') {
                encoded.append((char) octet);
            } else {
                encoded.append('%');
                encoded.append(Character.toUpperCase(
                        Character.forDigit((octet >>> 4) & 0xf, 16)));
                encoded.append(Character.toUpperCase(
                        Character.forDigit(octet & 0xf, 16)));
            }
        }
        return encoded.toString();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        if (!(value instanceof Map<?, ?>)) {
            throw new IllegalArgumentException("expected JSON object");
        }
        return (Map<String, Object>) value;
    }

    /** Minimal JSON codec for the focused vAPI bodies and error envelopes. */
    private static final class Json {
        private final String text;
        private int offset;

        private Json(String text) {
            this.text = text;
        }

        static Object read(String text) {
            Json parser = new Json(text);
            Object value = parser.value();
            parser.whitespace();
            if (parser.offset != text.length()) {
                throw new IllegalArgumentException("trailing JSON");
            }
            return value;
        }

        static String write(Object value) {
            StringBuilder output = new StringBuilder();
            append(output, value);
            return output.toString();
        }

        private static void append(StringBuilder out, Object value) {
            if (value == null) {
                out.append("null");
            } else if (value instanceof String string) {
                quote(out, string);
            } else if (value instanceof Boolean
                    || value instanceof Number) {
                out.append(value);
            } else if (value instanceof Map<?, ?> map) {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    quote(out, (String) entry.getKey());
                    out.append(':');
                    append(out, entry.getValue());
                }
                out.append('}');
            } else if (value instanceof Iterable<?> values) {
                out.append('[');
                boolean first = true;
                for (Object item : values) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    append(out, item);
                }
                out.append(']');
            } else {
                throw new IllegalArgumentException(
                        "unsupported JSON value");
            }
        }

        private static void quote(StringBuilder out, String value) {
            out.append('"');
            for (int i = 0; i < value.length(); i++) {
                char character = value.charAt(i);
                switch (character) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\b' -> out.append("\\b");
                    case '\f' -> out.append("\\f");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (character < 0x20) {
                            out.append(String.format(
                                    "\\u%04x", (int) character));
                        } else {
                            out.append(character);
                        }
                    }
                }
            }
            out.append('"');
        }

        private Object value() {
            whitespace();
            if (offset >= text.length()) {
                throw new IllegalArgumentException("missing JSON value");
            }
            return switch (text.charAt(offset)) {
                case '"' -> string();
                case '{' -> object();
                case '[' -> array();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() {
            Map<String, Object> value = new LinkedHashMap<>();
            offset++;
            whitespace();
            if (take('}')) {
                return value;
            }
            while (true) {
                whitespace();
                String key = string();
                whitespace();
                expect(':');
                value.put(key, value());
                whitespace();
                if (take('}')) {
                    return value;
                }
                expect(',');
            }
        }

        private List<Object> array() {
            List<Object> value = new ArrayList<>();
            offset++;
            whitespace();
            if (take(']')) {
                return value;
            }
            while (true) {
                value.add(value());
                whitespace();
                if (take(']')) {
                    return value;
                }
                expect(',');
            }
        }

        private String string() {
            expect('"');
            StringBuilder value = new StringBuilder();
            while (offset < text.length()) {
                char character = text.charAt(offset++);
                if (character == '"') {
                    return value.toString();
                }
                if (character != '\\') {
                    value.append(character);
                    continue;
                }
                if (offset >= text.length()) {
                    throw new IllegalArgumentException("bad JSON escape");
                }
                char escape = text.charAt(offset++);
                switch (escape) {
                    case '"' -> value.append('"');
                    case '\\' -> value.append('\\');
                    case '/' -> value.append('/');
                    case 'b' -> value.append('\b');
                    case 'f' -> value.append('\f');
                    case 'n' -> value.append('\n');
                    case 'r' -> value.append('\r');
                    case 't' -> value.append('\t');
                    case 'u' -> {
                        if (offset + 4 > text.length()) {
                            throw new IllegalArgumentException(
                                    "bad unicode escape");
                        }
                        value.append((char) Integer.parseInt(
                                text.substring(offset, offset + 4), 16));
                        offset += 4;
                    }
                    default -> throw new IllegalArgumentException(
                            "bad JSON escape");
                }
            }
            throw new IllegalArgumentException("unterminated JSON string");
        }

        private Object number() {
            int start = offset;
            while (offset < text.length()
                    && "-+0123456789.eE".indexOf(
                    text.charAt(offset)) >= 0) {
                offset++;
            }
            String token = text.substring(start, offset);
            if (token.isEmpty()) {
                throw new IllegalArgumentException("bad JSON value");
            }
            return token.contains(".") || token.contains("e")
                    || token.contains("E")
                    ? Double.valueOf(token)
                    : Long.valueOf(token);
        }

        private Object literal(String token, Object value) {
            if (!text.startsWith(token, offset)) {
                throw new IllegalArgumentException("bad JSON literal");
            }
            offset += token.length();
            return value;
        }

        private void whitespace() {
            while (offset < text.length()
                    && Character.isWhitespace(text.charAt(offset))) {
                offset++;
            }
        }

        private boolean take(char expected) {
            if (offset < text.length()
                    && text.charAt(offset) == expected) {
                offset++;
                return true;
            }
            return false;
        }

        private void expect(char expected) {
            if (!take(expected)) {
                throw new IllegalArgumentException(
                        "expected '" + expected + "'");
            }
        }
    }
}
