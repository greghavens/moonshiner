import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A minimal VCF Automation 9.1 project client. */
public final class VcfAutomationClient {
    private final URI baseUri;
    private final String tenant;
    private final String apiRefreshToken;
    private final HttpClient httpClient;
    private String accessToken;

    public VcfAutomationClient(URI baseUri, String tenant, String apiRefreshToken) {
        this.baseUri = baseUri;
        this.tenant = tenant;
        this.apiRefreshToken = apiRefreshToken;
        this.httpClient = HttpClient.newHttpClient();
    }

    /** Returns every project name in ascending Java String order. */
    public List<String> listProjectNames() throws IOException, InterruptedException {
        List<String> names = new ArrayList<>();
        int page = 0;

        while (true) {
            URI uri = endpoint("/project-service/api/projects?page=" + page
                    + "&size=500&apiVersion=2019-01-15");
            HttpResponse<String> response = authorizedGet(uri);
            Map<String, Object> document = asObject(Json.parse(response.body()), "project page");
            int responsePage = asInt(required(document, "number"), "number");
            if (responsePage != page) {
                throw new IOException("VCF Automation returned page " + responsePage
                        + " while page " + page + " was requested");
            }

            for (Object value : asArray(required(document, "content"), "content")) {
                Map<String, Object> project = asObject(value, "project");
                names.add(asString(required(project, "name"), "project name"));
            }

            if (asBoolean(required(document, "last"), "last")) {
                break;
            }
            int totalPages = asInt(required(document, "totalPages"), "totalPages");
            if (page + 1 >= totalPages) {
                throw new IOException("VCF Automation returned inconsistent pagination metadata");
            }
            page++;
        }

        // TODO: the service does not guarantee a stable collection order.
        return names;
    }

    private HttpResponse<String> authorizedGet(URI uri) throws IOException, InterruptedException {
        if (accessToken == null) {
            refreshAccessToken();
        }

        HttpResponse<String> response = sendGet(uri, accessToken);
        // TODO: an access token can expire while later pages are being fetched.
        if (response.statusCode() / 100 != 2) {
            throw new IOException("VCF Automation project request failed with HTTP "
                    + response.statusCode());
        }
        return response;
    }

    private HttpResponse<String> sendGet(URI uri, String token)
            throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(uri)
                .header("Accept", "application/json")
                .header("Authorization", "Bearer " + token)
                .GET()
                .build();
        return httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
    }

    private void refreshAccessToken() throws IOException, InterruptedException {
        String form = "grant_type=refresh_token&refresh_token="
                + URLEncoder.encode(apiRefreshToken, StandardCharsets.UTF_8);
        URI uri = endpoint("/tm/oauth/tenant/" + encodePathSegment(tenant) + "/token");
        HttpRequest request = HttpRequest.newBuilder(uri)
                .header("Accept", "application/json")
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(form, StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> response = httpClient.send(
                request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() / 100 != 2) {
            throw new IOException("VCF Automation token exchange failed with HTTP "
                    + response.statusCode());
        }
        Map<String, Object> document = asObject(Json.parse(response.body()), "token response");
        accessToken = asString(required(document, "access_token"), "access_token");
    }

    private URI endpoint(String pathAndQuery) {
        String base = baseUri.toString();
        while (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        return URI.create(base + pathAndQuery);
    }

    private static String encodePathSegment(String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        StringBuilder encoded = new StringBuilder(bytes.length);
        char[] hex = "0123456789ABCDEF".toCharArray();
        for (byte item : bytes) {
            int valueByte = item & 0xff;
            if ((valueByte >= 'a' && valueByte <= 'z')
                    || (valueByte >= 'A' && valueByte <= 'Z')
                    || (valueByte >= '0' && valueByte <= '9')
                    || valueByte == '-' || valueByte == '.' || valueByte == '_'
                    || valueByte == '~') {
                encoded.append((char) valueByte);
            } else {
                encoded.append('%').append(hex[valueByte >>> 4]).append(hex[valueByte & 0x0f]);
            }
        }
        return encoded.toString();
    }

    private static Object required(Map<String, Object> object, String key) throws IOException {
        if (!object.containsKey(key)) {
            throw new IOException("JSON response is missing " + key);
        }
        return object.get(key);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> asObject(Object value, String label) throws IOException {
        if (!(value instanceof Map<?, ?>)) {
            throw new IOException(label + " must be a JSON object");
        }
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> asArray(Object value, String label) throws IOException {
        if (!(value instanceof List<?>)) {
            throw new IOException(label + " must be a JSON array");
        }
        return (List<Object>) value;
    }

    private static String asString(Object value, String label) throws IOException {
        if (!(value instanceof String)) {
            throw new IOException(label + " must be a JSON string");
        }
        return (String) value;
    }

    private static int asInt(Object value, String label) throws IOException {
        if (!(value instanceof Long number) || number < Integer.MIN_VALUE || number > Integer.MAX_VALUE) {
            throw new IOException(label + " must be a JSON integer");
        }
        return number.intValue();
    }

    private static boolean asBoolean(Object value, String label) throws IOException {
        if (!(value instanceof Boolean)) {
            throw new IOException(label + " must be a JSON boolean");
        }
        return (Boolean) value;
    }

    /** Small standards-compliant JSON reader to keep the client dependency-free. */
    private static final class Json {
        private final String input;
        private int position;

        private Json(String input) {
            this.input = input;
        }

        static Object parse(String input) throws IOException {
            Json parser = new Json(input);
            Object value = parser.value();
            parser.whitespace();
            if (parser.position != input.length()) {
                throw parser.error("unexpected trailing input");
            }
            return value;
        }

        private Object value() throws IOException {
            whitespace();
            if (position >= input.length()) {
                throw error("expected a JSON value");
            }
            return switch (input.charAt(position)) {
                case '{' -> object();
                case '[' -> array();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() throws IOException {
            expect('{');
            Map<String, Object> result = new LinkedHashMap<>();
            whitespace();
            if (take('}')) {
                return result;
            }
            while (true) {
                whitespace();
                if (position >= input.length() || input.charAt(position) != '"') {
                    throw error("expected an object key");
                }
                String key = string();
                whitespace();
                expect(':');
                result.put(key, value());
                whitespace();
                if (take('}')) {
                    return result;
                }
                expect(',');
            }
        }

        private List<Object> array() throws IOException {
            expect('[');
            List<Object> result = new ArrayList<>();
            whitespace();
            if (take(']')) {
                return result;
            }
            while (true) {
                result.add(value());
                whitespace();
                if (take(']')) {
                    return result;
                }
                expect(',');
            }
        }

        private String string() throws IOException {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (position < input.length()) {
                char character = input.charAt(position++);
                if (character == '"') {
                    return result.toString();
                }
                if (character == '\\') {
                    if (position >= input.length()) {
                        throw error("unterminated escape");
                    }
                    char escaped = input.charAt(position++);
                    switch (escaped) {
                        case '"', '\\', '/' -> result.append(escaped);
                        case 'b' -> result.append('\b');
                        case 'f' -> result.append('\f');
                        case 'n' -> result.append('\n');
                        case 'r' -> result.append('\r');
                        case 't' -> result.append('\t');
                        case 'u' -> result.append(unicodeEscape());
                        default -> throw error("invalid escape");
                    }
                } else {
                    if (character < 0x20) {
                        throw error("unescaped control character");
                    }
                    result.append(character);
                }
            }
            throw error("unterminated string");
        }

        private char unicodeEscape() throws IOException {
            if (position + 4 > input.length()) {
                throw error("incomplete unicode escape");
            }
            int value = 0;
            for (int index = 0; index < 4; index++) {
                int digit = Character.digit(input.charAt(position++), 16);
                if (digit < 0) {
                    throw error("invalid unicode escape");
                }
                value = (value << 4) | digit;
            }
            return (char) value;
        }

        private Object number() throws IOException {
            int start = position;
            if (take('-') && position >= input.length()) {
                throw error("incomplete number");
            }
            if (take('0')) {
                // A leading zero is the whole integer part.
            } else {
                digits();
            }
            boolean fractional = false;
            if (take('.')) {
                fractional = true;
                digits();
            }
            if (take('e') || take('E')) {
                fractional = true;
                if (!take('+')) {
                    take('-');
                }
                digits();
            }
            String token = input.substring(start, position);
            try {
                if (fractional) {
                    return Double.valueOf(token);
                }
                return Long.valueOf(token);
            } catch (NumberFormatException exception) {
                throw error("invalid number");
            }
        }

        private void digits() throws IOException {
            int start = position;
            while (position < input.length() && Character.isDigit(input.charAt(position))) {
                position++;
            }
            if (start == position) {
                throw error("expected a digit");
            }
        }

        private Object literal(String text, Object value) throws IOException {
            if (!input.startsWith(text, position)) {
                throw error("invalid literal");
            }
            position += text.length();
            return value;
        }

        private void whitespace() {
            while (position < input.length()
                    && (input.charAt(position) == ' ' || input.charAt(position) == '\n'
                    || input.charAt(position) == '\r' || input.charAt(position) == '\t')) {
                position++;
            }
        }

        private boolean take(char character) {
            if (position < input.length() && input.charAt(position) == character) {
                position++;
                return true;
            }
            return false;
        }

        private void expect(char character) throws IOException {
            if (!take(character)) {
                throw error("expected '" + character + "'");
            }
        }

        private IOException error(String message) {
            return new IOException("Invalid JSON at offset " + position + ": " + message);
        }
    }
}
