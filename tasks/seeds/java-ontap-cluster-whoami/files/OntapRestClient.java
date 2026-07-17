import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;

/** Thin HTTP client for the ONTAP cluster-management /api endpoint. */
public final class OntapRestClient {

    public static final class Response {
        public final int status;
        public final String body;

        Response(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    public static final class Builder {
        private final String baseUrl;
        private String username;
        private String password;

        private Builder(String baseUrl) {
            this.baseUrl = baseUrl;
        }

        public Builder credentials(String username, String password) {
            this.username = username;
            this.password = password;
            return this;
        }

        public OntapRestClient build() {
            if (username == null || password == null) {
                throw new IllegalStateException("credentials are required");
            }
            return new OntapRestClient(baseUrl, username, password);
        }
    }

    public static Builder builder(String baseUrl) {
        return new Builder(baseUrl);
    }

    private final String baseUrl;
    private final String username;
    private final String authHeader;
    private final HttpClient http;

    private OntapRestClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.username = username;
        String token = Base64.getEncoder()
                .encodeToString((username + ":" + password).getBytes(StandardCharsets.UTF_8));
        this.authHeader = "Basic " + token;
        this.http = HttpClient.newHttpClient();
    }

    /** The login name this client authenticates as. */
    public String username() {
        return username;
    }

    public Response get(String pathAndQuery) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + pathAndQuery))
                .header("Authorization", authHeader)
                .header("Accept", "application/hal+json")
                .GET()
                .build();
        HttpResponse<String> response =
                http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        return new Response(response.statusCode(), response.body());
    }

    /** Decode the documented {"error": {code, message}} body into an exception. */
    public static OntapApiException apiError(Response response) {
        String code = "unknown";
        String message = "no error body";
        try {
            Map<String, Object> doc = Json.object(Json.parse(response.body));
            Map<String, Object> error = Json.object(doc.get("error"));
            Object codeValue = error.get("code");
            Object messageValue = error.get("message");
            if (codeValue != null) code = codeValue.toString();
            if (messageValue != null) message = messageValue.toString();
        } catch (RuntimeException ignored) {
            // fall through with placeholders; status alone still identifies the failure
        }
        return new OntapApiException(response.status, code, message);
    }
}
