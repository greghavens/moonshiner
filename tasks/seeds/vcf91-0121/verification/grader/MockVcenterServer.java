import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Hermetic loopback service. It loads the operation binding from contract.json,
 * exposes only that operation, and keeps an immutable-view request log for tests.
 */
public final class MockVcenterServer implements AutoCloseable {
    private static final String PAGE_ONE = """
            {
              "marker": "next marker/2?after=cat-z&full=true+keep",
              "items": [
                {
                  "category_id": "cat-z",
                  "info": {
                    "name": "Zulu",
                    "description": "last page-order item",
                    "cardinality": "MULTIPLE",
                    "associable_types": ["VirtualMachine"],
                    "used_by": []
                  }
                },
                {
                  "category_id": "cat-a2",
                  "info": {
                    "name": "Alpha",
                    "description": "second alpha",
                    "cardinality": "SINGLE",
                    "associable_types": [],
                    "used_by": ["com.acme.ops"]
                  }
                }
              ]
            }
            """;

    private static final String PAGE_TWO = """
            {
              "items": [
                {
                  "category_id": "cat-omega",
                  "info": {
                    "name": "\\u03a9mega",
                    "description": "unicode name",
                    "cardinality": "MULTIPLE",
                    "associable_types": ["VirtualMachine", "Datastore"],
                    "used_by": []
                  }
                },
                {
                  "category_id": "cat-a1",
                  "info": {
                    "name": "Alpha",
                    "description": "first alpha\\nline",
                    "cardinality": "MULTIPLE",
                    "associable_types": ["Datastore"],
                    "used_by": []
                  }
                }
              ],
              "marker": "final+page/3"
            }
            """;

    private static final String PAGE_THREE = """
            {
              "items": [
                {
                  "category_id": "cat-q",
                  "info": {
                    "name": "Quote \\"Ops\\"",
                    "description": "path C:\\\\inventory",
                    "cardinality": "SINGLE",
                    "associable_types": ["Folder"],
                    "used_by": ["team-a", "team-b"]
                  }
                },
                {
                  "category_id": "cat-b",
                  "info": {
                    "name": "Beta",
                    "description": "middle",
                    "cardinality": "SINGLE",
                    "associable_types": [],
                    "used_by": []
                  }
                }
              ]
            }
            """;

    private final ContractBinding binding;
    private final ResponseMode responseMode;
    private final HttpServer server;
    private final ExecutorService executor;
    private final CopyOnWriteArrayList<LoggedRequest> requestLog =
            new CopyOnWriteArrayList<>();

    public MockVcenterServer(Path contractPath) throws IOException {
        this(contractPath, ResponseMode.PAGINATED);
    }

    public MockVcenterServer(Path contractPath, ResponseMode responseMode)
            throws IOException {
        binding = ContractBinding.load(contractPath);
        this.responseMode = responseMode;
        server = HttpServer.create(
                new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        executor = Executors.newSingleThreadExecutor();
        server.setExecutor(executor);
        server.createContext(binding.basePath() + binding.path(), new Handler());
        server.start();
    }

    public URI apiBaseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort()
                + binding.basePath() + "/");
    }

    public List<LoggedRequest> requestLogSnapshot() {
        return List.copyOf(requestLog);
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }

    private final class Handler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            byte[] body = exchange.getRequestBody().readAllBytes();
            String rawPath = exchange.getRequestURI().getRawPath();
            String rawQuery = exchange.getRequestURI().getRawQuery();
            requestLog.add(new LoggedRequest(
                    binding.operationId(),
                    exchange.getRequestMethod(),
                    rawPath,
                    rawQuery,
                    exchange.getRequestHeaders().getFirst("vmware-api-session-id"),
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    body.length));

            if (!rawPath.equals(binding.basePath() + binding.path())) {
                send(exchange, 404, "{\"error\":\"unknown path\"}");
                return;
            }
            if (!exchange.getRequestMethod().equals(binding.method())) {
                send(exchange, 405, "{\"error\":\"wrong method\"}");
                return;
            }
            if (responseMode == ResponseMode.HTTP_ERROR) {
                send(exchange, 503, "{\"error\":\"fixture outage\"}");
                return;
            }
            if (responseMode == ResponseMode.MALFORMED_RESPONSE) {
                send(exchange, 200, "{\"marker\":null}");
                return;
            }

            String response;
            if (rawQuery == null || rawQuery.equals("names=&marker=&page_size=")) {
                response = PAGE_ONE;
            } else if (rawQuery.equals(
                    "marker=next+marker%2F2%3Fafter%3Dcat-z%26full%3Dtrue%2Bkeep")) {
                response = PAGE_TWO;
            } else if (rawQuery.equals("marker=final%2Bpage%2F3")) {
                response = finalPage();
            } else {
                send(exchange, 400, "{\"error\":\"unexpected query\"}");
                return;
            }
            send(exchange, 200, response);
        }
    }

    private String finalPage() {
        String marker = switch (responseMode) {
            case PAGINATED_NULL_MARKER -> "null";
            case PAGINATED_EMPTY_MARKER -> "\"\"";
            default -> null;
        };
        if (marker == null) {
            return PAGE_THREE;
        }
        int itemsMember = PAGE_THREE.indexOf("\"items\"");
        return PAGE_THREE.substring(0, itemsMember)
                + "\"marker\": " + marker + ",\n  "
                + PAGE_THREE.substring(itemsMember);
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] encoded = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, encoded.length);
        exchange.getResponseBody().write(encoded);
        exchange.close();
    }

    public record LoggedRequest(
            String operationId,
            String method,
            String rawPath,
            String rawQuery,
            String sessionHeader,
            String acceptHeader,
            String contentTypeHeader,
            int bodyBytes) {
    }

    public enum ResponseMode {
        PAGINATED,
        PAGINATED_NULL_MARKER,
        PAGINATED_EMPTY_MARKER,
        HTTP_ERROR,
        MALFORMED_RESPONSE
    }

    private record ContractBinding(
            String basePath, String operationId, String method, String path) {
        private static final Pattern BASE_PATH = Pattern.compile(
                "\\\"basePath\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        private static final Pattern OPERATION_ID = Pattern.compile(
                "\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        private static final Pattern METHOD = Pattern.compile(
                "\\\"method\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        private static final Pattern PATH = Pattern.compile(
                "\\\"path\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");

        static ContractBinding load(Path contractPath) throws IOException {
            String json = Files.readString(contractPath, StandardCharsets.UTF_8);
            String basePath = exactlyOne(BASE_PATH, json, "basePath");
            String operationId = exactlyOne(OPERATION_ID, json, "operationId");
            String method = exactlyOne(METHOD, json, "method");
            String path = exactlyOne(PATH, json, "path");
            if (!json.contains("\"name\": \"vmware-api-session-id\"")) {
                throw new IOException("contract does not pin the session header");
            }
            return new ContractBinding(basePath, operationId, method, path);
        }

        private static String exactlyOne(Pattern pattern, String text, String field)
                throws IOException {
            Matcher matcher = pattern.matcher(text);
            if (!matcher.find()) {
                throw new IOException("contract is missing " + field);
            }
            String value = matcher.group(1);
            if (matcher.find()) {
                throw new IOException("mock contract must contain exactly one " + field);
            }
            return value;
        }
    }
}
