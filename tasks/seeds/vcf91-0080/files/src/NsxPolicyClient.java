import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Objects;

/**
 * Minimal VCF 9.1 NSX Policy client for the two operations in
 * docs/contract.json. No third-party JSON or HTTP dependencies are used.
 */
public final class NsxPolicyClient {
    public interface AccessTokenProvider {
        String currentToken();

        String refreshToken() throws IOException;
    }

    public record Condition(
            String memberType,
            String key,
            String operator,
            String value,
            String scopeOperator) {
        public Condition {
            Objects.requireNonNull(memberType, "memberType");
            Objects.requireNonNull(key, "key");
            Objects.requireNonNull(value, "value");
        }
    }

    public record Group(
            String displayName,
            String description,
            List<String> groupType,
            List<Condition> expression) {
        public Group {
            Objects.requireNonNull(displayName, "displayName");
            expression = List.copyOf(Objects.requireNonNull(expression, "expression"));
            groupType = groupType == null ? null : List.copyOf(groupType);
        }
    }

    private final URI endpoint;
    private final AccessTokenProvider tokens;
    private final HttpClient http;

    public NsxPolicyClient(URI endpoint, AccessTokenProvider tokens) {
        this.endpoint = Objects.requireNonNull(endpoint, "endpoint");
        this.tokens = Objects.requireNonNull(tokens, "tokens");
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    public void patchGroup(String domainId, String groupId, Group group)
            throws IOException, InterruptedException {
        Objects.requireNonNull(group, "group");
        HttpResponse<String> response = send(
                "PATCH",
                groupUri(domainId, groupId),
                groupJson(group),
                tokens.currentToken());
        requireSuccess(response);
    }

    public String readGroup(String domainId, String groupId)
            throws IOException, InterruptedException {
        HttpResponse<String> response = send(
                "GET",
                groupUri(domainId, groupId),
                null,
                tokens.currentToken());
        requireSuccess(response);
        return response.body();
    }

    private HttpResponse<String> send(String method, URI uri, String body, String token)
            throws IOException, InterruptedException {
        HttpRequest.Builder request = HttpRequest.newBuilder(uri)
                .header("Accept", "application/json")
                .header("Authorization", "Bearer " + token);
        if (body == null) {
            request.GET();
        } else {
            request.header("Content-Type", "application/json");
            request.method(method, HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8));
        }
        return http.send(request.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    private URI groupUri(String domainId, String groupId) {
        Objects.requireNonNull(domainId, "domainId");
        Objects.requireNonNull(groupId, "groupId");
        return URI.create(endpoint.toString()
                + "/policy/api/v1/infra/domains/" + encodeSegment(domainId)
                + "/groups/" + encodeSegment(groupId));
    }

    private static String groupJson(Group group) {
        StringBuilder json = new StringBuilder();
        json.append("{\"display_name\":\"").append(escape(group.displayName())).append('"');

        // TODO: optional properties which are not set must be omitted.
        json.append(",\"description\":\"")
                .append(group.description() == null ? "" : escape(group.description()))
                .append('"');
        json.append(",\"group_type\":[");
        if (group.groupType() != null) {
            for (int i = 0; i < group.groupType().size(); i++) {
                if (i > 0) {
                    json.append(',');
                }
                json.append('"').append(escape(group.groupType().get(i))).append('"');
            }
        }
        json.append(']');

        json.append(",\"expression\":[");
        for (int i = 0; i < group.expression().size(); i++) {
            if (i > 0) {
                json.append(',');
            }
            Condition condition = group.expression().get(i);
            json.append("{\"resource_type\":\"Condition\"")
                    .append(",\"member_type\":\"").append(escape(condition.memberType())).append('"')
                    .append(",\"key\":\"").append(escape(condition.key())).append('"');
            if (condition.operator() != null) {
                json.append(",\"operator\":\"").append(escape(condition.operator())).append('"');
            }
            json.append(",\"value\":\"").append(escape(condition.value())).append('"');

            // TODO: a missing scope_operator is not the same as an empty one.
            json.append(",\"scope_operator\":\"")
                    .append(condition.scopeOperator() == null ? "" : escape(condition.scopeOperator()))
                    .append("\"}");
        }
        return json.append("]}").toString();
    }

    private static String encodeSegment(String value) {
        StringBuilder encoded = new StringBuilder();
        for (byte current : value.getBytes(StandardCharsets.UTF_8)) {
            int b = current & 0xff;
            if ((b >= 'a' && b <= 'z')
                    || (b >= 'A' && b <= 'Z')
                    || (b >= '0' && b <= '9')
                    || b == '-' || b == '.' || b == '_' || b == '~') {
                encoded.append((char) b);
            } else {
                encoded.append('%');
                encoded.append(Character.toUpperCase(Character.forDigit((b >>> 4) & 0xf, 16)));
                encoded.append(Character.toUpperCase(Character.forDigit(b & 0xf, 16)));
            }
        }
        return encoded.toString();
    }

    private static String escape(String value) {
        StringBuilder escaped = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> escaped.append("\\\"");
                case '\\' -> escaped.append("\\\\");
                case '\b' -> escaped.append("\\b");
                case '\f' -> escaped.append("\\f");
                case '\n' -> escaped.append("\\n");
                case '\r' -> escaped.append("\\r");
                case '\t' -> escaped.append("\\t");
                default -> {
                    if (c < 0x20) {
                        escaped.append(String.format("\\u%04x", (int) c));
                    } else {
                        escaped.append(c);
                    }
                }
            }
        }
        return escaped.toString();
    }

    private static void requireSuccess(HttpResponse<String> response) throws IOException {
        if (response.statusCode() < 200 || response.statusCode() >= 300) {
            throw new IOException("NSX Policy request failed with HTTP " + response.statusCode());
        }
    }
}
