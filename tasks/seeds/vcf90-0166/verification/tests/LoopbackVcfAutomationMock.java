import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Loopback-only mock for the sole operation named by docs/contract.json. */
final class LoopbackVcfAutomationMock implements AutoCloseable {
    record RequestLog(String method, String rawPath, String rawQuery,
                      List<String> authorization, List<String> accept, byte[] body) {}

    private static final Map<String, String> PAGES = Map.of(
            "$top=2&$skip=0", """
                    {"content":[
                      {"id":"dep-05","name":"Zulu","projectId":"project-b"},
                      {"id":"dep-01","name":"Alpha","projectId":"project-a"}
                    ],"totalElements":5,"numberOfElements":2}
                    """,
            "$top=2&$skip=2", """
                    {"content":[
                      {"id":"dep-03","name":"Mike","projectId":"project-b"},
                      {"id":"dep-02","name":"Bravo","projectId":"project-a"}
                    ]}
                    """,
            "$top=2&$skip=4", """
                    {"content":[
                      {"id":"dep-04","name":"Sierra","projectId":"project-c"}
                    ],"totalElements":5,"numberOfElements":1}
                    """,
            "$top=3&$skip=0", """
                    {"content":[
                      {"id":"dep-03","name":"Mike","projectId":"project-b"},
                      {"id":"dep-01","name":"Alpha","projectId":"project-a"},
                      {"id":"dep-05","name":"Zulu","projectId":"project-b"}
                    ]}
                    """,
            "$top=3&$skip=3", """
                    {"content":[
                      {"id":"dep-04","name":"Sierra","projectId":"project-c"},
                      {"id":"dep-02","name":"Bravo","projectId":"project-a"}
                    ]}
                    """,
            "$top=6&$skip=0", """
                    {}
                    """);

    private final HttpServer server;
    private final List<RequestLog> requests = new ArrayList<>();

    LoopbackVcfAutomationMock() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
        server.start();
    }

    URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    synchronized List<RequestLog> requests() {
        return List.copyOf(requests);
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        synchronized (this) {
            requests.add(new RequestLog(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getRawPath(),
                    exchange.getRequestURI().getRawQuery(),
                    List.copyOf(exchange.getRequestHeaders().getOrDefault("Authorization", List.of())),
                    List.copyOf(exchange.getRequestHeaders().getOrDefault("Accept", List.of())),
                    requestBody));
        }

        String response = null;
        int status = 404;
        if ("GET".equals(exchange.getRequestMethod())
                && "/iaas/api/deployments".equals(exchange.getRequestURI().getRawPath())) {
            if ("$top=7&$skip=0".equals(exchange.getRequestURI().getRawQuery())) {
                response = "{\"content\":[]}";
                status = 403;
            } else {
                response = PAGES.get(exchange.getRequestURI().getRawQuery());
                status = response == null ? 400 : 200;
            }
        }

        byte[] bytes = response == null ? new byte[0] : response.getBytes(StandardCharsets.UTF_8);
        if (status == 200) {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
        }
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
