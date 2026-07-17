import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Acceptance harness: loopback fake Vault Transit engine (wire contract
 * pinned in docs/contract.json) plus regression checks for the existing
 * VaultClient transport. No real Vault, no real credentials.
 * Protected — do not modify. Run with: java TestMain.java
 */
public class TestMain {

    static final String TOKEN = "hvs.dummy-transit-4417"; // dummy credential
    static final String NAMESPACE = "eng/payments/";

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    static String b64(String s) {
        return Base64.getEncoder().encodeToString(s.getBytes(StandardCharsets.UTF_8));
    }

    static String b64(byte[] b) {
        return Base64.getEncoder().encodeToString(b);
    }

    /** RFC 3986 percent-decoding: %XX only; '+' stays a literal plus. */
    static String pctDecode(String raw) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (int i = 0; i < raw.length(); i++) {
            char c = raw.charAt(i);
            if (c == '%') {
                out.write(Integer.parseInt(raw.substring(i + 1, i + 3), 16));
                i += 2;
            } else {
                out.write((byte) c);
            }
        }
        return out.toString(StandardCharsets.UTF_8);
    }

    // ---------------------------------------------------------------- fake

    static final class Recorded {
        final String method;
        final String rawPath;
        final String decodedPath;
        final Map<String, String> headers = new HashMap<>();
        final boolean hasNamespace;
        final String body;

        Recorded(HttpExchange ex, String body) {
            this.method = ex.getRequestMethod();
            this.rawPath = ex.getRequestURI().getRawPath();
            this.decodedPath = pctDecode(rawPath);
            for (String name : ex.getRequestHeaders().keySet()) {
                headers.put(name.toLowerCase(java.util.Locale.ROOT),
                        ex.getRequestHeaders().getFirst(name));
            }
            this.hasNamespace = ex.getRequestHeaders().containsKey("X-Vault-Namespace");
            this.body = body;
        }

        Map<String, Object> json() {
            return Json.parseObject(body);
        }
    }

    static final class Canned {
        final int status;
        final String body;

        Canned(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    static final class Fake implements AutoCloseable {
        final HttpServer srv;
        final List<Recorded> reqs = new ArrayList<>();
        final Map<String, ArrayDeque<Canned>> routes = new HashMap<>();

        Fake() throws IOException {
            srv = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            srv.createContext("/", ex -> {
                byte[] raw = ex.getRequestBody().readAllBytes();
                Recorded rec = new Recorded(ex, new String(raw, StandardCharsets.UTF_8));
                Canned resp;
                synchronized (this) {
                    reqs.add(rec);
                    ArrayDeque<Canned> q = routes.get(rec.method + " " + rec.decodedPath);
                    if (q == null || q.isEmpty()) {
                        resp = new Canned(404, "{\"errors\":[]}");
                    } else {
                        resp = q.size() > 1 ? q.poll() : q.peek();
                    }
                }
                byte[] body = resp.body == null ? new byte[0] : resp.body.getBytes(StandardCharsets.UTF_8);
                if (body.length == 0) {
                    ex.sendResponseHeaders(resp.status, -1);
                } else {
                    ex.getResponseHeaders().set("Content-Type", "application/json");
                    ex.sendResponseHeaders(resp.status, body.length);
                    try (OutputStream os = ex.getResponseBody()) {
                        os.write(body);
                    }
                }
                ex.close();
            });
            srv.start();
        }

        void route(String method, String decodedPath, int status, String body) {
            synchronized (this) {
                routes.computeIfAbsent(method + " " + decodedPath, k -> new ArrayDeque<>())
                        .add(new Canned(status, body));
            }
        }

        String url() {
            return "http://127.0.0.1:" + srv.getAddress().getPort();
        }

        synchronized Recorded last() {
            check(!reqs.isEmpty(), "expected at least one recorded request");
            return reqs.get(reqs.size() - 1);
        }

        @Override
        public void close() {
            srv.stop(0);
        }
    }

    static VaultClient client(Fake f) {
        return new VaultClient(f.url(), TOKEN, NAMESPACE);
    }

    // --------------------------------------------- existing client behavior

    static void testClientPostContract() throws Exception {
        try (Fake f = new Fake()) {
            f.route("POST", "/v1/sys/capabilities-self", 200,
                    "{\"capabilities\":[\"update\"],\"data\":{\"capabilities\":[\"update\"]}}");
            Map<String, Object> out = client(f)
                    .post("sys/capabilities-self", Map.of("paths", List.of("transit/encrypt/app-billing")));
            checkEq(out.get("capabilities"), List.of("update"), "post() must return the decoded response document");

            Recorded r = f.last();
            checkEq(r.method, "POST", "transport method");
            checkEq(r.headers.get("x-vault-token"), TOKEN, "X-Vault-Token must be sent on every request");
            checkEq(r.headers.get("x-vault-namespace"), NAMESPACE, "X-Vault-Namespace must carry the configured namespace");
            check(r.headers.getOrDefault("content-type", "").startsWith("application/json"),
                    "Content-Type must be application/json, got " + r.headers.get("content-type"));
            checkEq(r.json().get("paths"), List.of("transit/encrypt/app-billing"), "request body must round-trip");
        }
    }

    static void testClientErrorDecoding() throws Exception {
        try (Fake f = new Fake()) {
            f.route("POST", "/v1/sys/capabilities-self", 403,
                    "{\"errors\":[\"1 error occurred:\\n\\t* permission denied\\n\\n\"]}");
            try {
                client(f).post("sys/capabilities-self", Map.of());
                check(false, "a 403 must raise VaultApiException");
            } catch (VaultApiException e) {
                checkEq(e.status(), 403, "exception status");
                check(e.errors().size() == 1 && e.errors().get(0).contains("permission denied"),
                        "errors array must be preserved, got " + e.errors());
                check(e.getMessage().contains("403") && e.getMessage().contains("permission denied"),
                        "message must carry status and detail, got " + e.getMessage());
            }
        }
    }

    // ----------------------------------------------------- transit batches

    static final String SPICY_KEY = "eu payments#2026";
    static final String SPICY_PATH = "/v1/transit/encrypt/" + SPICY_KEY;

    static void testEncryptBatchContract() throws Exception {
        try (Fake f = new Fake()) {
            f.route("POST", SPICY_PATH, 200, "{\"data\":{\"batch_results\":["
                    + "{\"ciphertext\":\"vault:v2:c1-blue\",\"key_version\":2},"
                    + "{\"ciphertext\":\"vault:v1:c2-hdr\",\"key_version\":1},"
                    + "{\"ciphertext\":\"vault:v2:c3-bare\",\"key_version\":2}]}}");

            TransitBatch tb = new TransitBatch(client(f));
            List<TransitBatch.EncryptResult> results = tb.encrypt(SPICY_KEY, List.of(
                    new TransitBatch.EncryptItem("invoice-7781:total=1249.50".getBytes(StandardCharsets.UTF_8))
                            .withContext("tenant-blue".getBytes(StandardCharsets.UTF_8)),
                    new TransitBatch.EncryptItem("card-last4=4242".getBytes(StandardCharsets.UTF_8))
                            .withAssociatedData("hdr:v2".getBytes(StandardCharsets.UTF_8))
                            .withKeyVersion(1),
                    new TransitBatch.EncryptItem("plain-note".getBytes(StandardCharsets.UTF_8))));

            Recorded r = f.last();
            checkEq(r.decodedPath, SPICY_PATH,
                    "key names must be percent-escaped into the encrypt path (space and # included)");
            check(!r.rawPath.contains("+"),
                    "the raw path must not contain '+': that is form-encoding, not path escaping — got " + r.rawPath);
            check(!r.rawPath.contains("#") && !r.rawPath.contains(" "),
                    "raw '#' or space must never appear unescaped in the path — got " + r.rawPath);
            checkEq(r.headers.get("x-vault-namespace"), NAMESPACE, "batch requests must be namespace-aware");
            checkEq(r.headers.get("x-vault-token"), TOKEN, "batch requests must be authenticated");

            Map<String, Object> body = r.json();
            checkEq(body.keySet(), Set.of("batch_input", "partial_failure_response_code"),
                    "request body must be exactly batch_input + partial_failure_response_code");
            checkEq(body.get("partial_failure_response_code"), 200L,
                    "partial_failure_response_code must ask Vault to return 200 on per-item failures");

            @SuppressWarnings("unchecked")
            List<Map<String, Object>> items = (List<Map<String, Object>>) (List<?>) ((List<?>) body.get("batch_input"));
            checkEq(items.size(), 3, "batch_input size");
            checkEq(items.get(0).keySet(), Set.of("plaintext", "context"),
                    "item 1 must carry plaintext+context and omit absent optionals entirely");
            checkEq(items.get(0).get("plaintext"), b64("invoice-7781:total=1249.50"), "item 1 plaintext must be base64");
            checkEq(items.get(0).get("context"), b64("tenant-blue"), "item 1 context must be base64");
            checkEq(items.get(1).keySet(), Set.of("plaintext", "associated_data", "key_version"),
                    "item 2 must carry plaintext+associated_data+key_version");
            checkEq(items.get(1).get("associated_data"), b64("hdr:v2"), "item 2 associated_data must be base64");
            checkEq(items.get(1).get("key_version"), 1L, "item 2 must pin the requested key_version");
            checkEq(items.get(2).keySet(), Set.of("plaintext"), "item 3 must carry only plaintext");

            checkEq(results.size(), 3, "result count");
            check(results.get(0).ok() && results.get(1).ok() && results.get(2).ok(), "all items succeeded");
            checkEq(results.get(0).ciphertext(), "vault:v2:c1-blue", "item 1 ciphertext");
            checkEq(results.get(0).keyVersion(), 2, "item 1 key_version from the response");
            checkEq(results.get(1).ciphertext(), "vault:v1:c2-hdr", "item 2 ciphertext");
            checkEq(results.get(1).keyVersion(), 1, "item 2 must preserve the pinned key version");
            checkEq(results.get(2).keyVersion(), 2, "item 3 key_version");
        }
    }

    static void testEncryptPartialFailure() throws Exception {
        try (Fake f = new Fake()) {
            f.route("POST", "/v1/transit/encrypt/app-billing", 200, "{\"data\":{\"batch_results\":["
                    + "{\"ciphertext\":\"vault:v2:ok1\",\"key_version\":2},"
                    + "{\"error\":\"cipher operation failed: invalid associated data\"},"
                    + "{\"ciphertext\":\"vault:v2:ok3\",\"key_version\":2}]}}");

            TransitBatch tb = new TransitBatch(client(f));
            List<TransitBatch.EncryptResult> results = tb.encrypt("app-billing", List.of(
                    new TransitBatch.EncryptItem("a".getBytes(StandardCharsets.UTF_8)),
                    new TransitBatch.EncryptItem("b".getBytes(StandardCharsets.UTF_8)),
                    new TransitBatch.EncryptItem("c".getBytes(StandardCharsets.UTF_8))));

            checkEq(results.size(), 3, "partial failure keeps one result per input, in order");
            check(results.get(0).ok(), "item 1 succeeded");
            check(!results.get(1).ok(), "item 2 failed");
            checkEq(results.get(1).error(), "cipher operation failed: invalid associated data",
                    "the per-item error string must be surfaced verbatim");
            check(results.get(1).ciphertext() == null, "a failed item has no ciphertext");
            check(results.get(2).ok(), "item 3 succeeded — later siblings survive an earlier failure");
            checkEq(results.get(2).ciphertext(), "vault:v2:ok3", "item 3 ciphertext");
        }
    }

    static void testDecryptBatch() throws Exception {
        byte[] binary = new byte[] {0, 1, 2, (byte) 0xFA};
        try (Fake f = new Fake()) {
            f.route("POST", "/v1/transit/decrypt/clé-décrypt", 200, "{\"data\":{\"batch_results\":["
                    + "{\"plaintext\":\"" + b64("montant=42,90€") + "\"},"
                    + "{\"plaintext\":\"" + b64(binary) + "\"},"
                    + "{\"error\":\"invalid ciphertext: no prefix\"}]}}");

            TransitBatch tb = new TransitBatch(client(f));
            List<TransitBatch.DecryptResult> results = tb.decrypt("clé-décrypt", List.of(
                    new TransitBatch.DecryptItem("vault:v2:zz1")
                            .withContext("tenant-blue".getBytes(StandardCharsets.UTF_8)),
                    new TransitBatch.DecryptItem("vault:v1:zz2"),
                    new TransitBatch.DecryptItem("garbage")));

            Recorded r = f.last();
            checkEq(r.decodedPath, "/v1/transit/decrypt/clé-décrypt",
                    "non-ASCII key names must percent-escape as UTF-8 bytes");
            Map<String, Object> body = r.json();
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> items = (List<Map<String, Object>>) (List<?>) ((List<?>) body.get("batch_input"));
            checkEq(items.get(0).keySet(), Set.of("ciphertext", "context"), "decrypt item 1 fields");
            checkEq(items.get(0).get("ciphertext"), "vault:v2:zz1", "ciphertext is sent verbatim, not base64d");
            checkEq(items.get(1).keySet(), Set.of("ciphertext"), "decrypt item 2 fields");

            checkEq(results.size(), 3, "decrypt result count");
            check(results.get(0).ok(), "item 1 ok");
            checkEq(new String(results.get(0).plaintext(), StandardCharsets.UTF_8), "montant=42,90€",
                    "plaintext must be base64-decoded back to the original UTF-8 bytes");
            check(java.util.Arrays.equals(results.get(1).plaintext(), binary),
                    "binary plaintext must survive the base64 round trip");
            check(!results.get(2).ok() && results.get(2).error().contains("invalid ciphertext"),
                    "per-item decrypt errors must be surfaced");
            check(results.get(2).plaintext() == null, "a failed decrypt item has no plaintext");
        }
    }

    static void testWholeRequestFailure() throws Exception {
        try (Fake f = new Fake()) {
            f.route("POST", "/v1/transit/encrypt/missing-key", 400,
                    "{\"errors\":[\"encryption key not found\"]}");
            TransitBatch tb = new TransitBatch(client(f));
            try {
                tb.encrypt("missing-key", List.of(
                        new TransitBatch.EncryptItem("x".getBytes(StandardCharsets.UTF_8))));
                check(false, "a request-level 400 must raise VaultApiException");
            } catch (VaultApiException e) {
                checkEq(e.status(), 400, "request-level failure status");
                checkEq(e.errors(), List.of("encryption key not found"), "request-level errors preserved");
            }
        }
    }

    static void testCustomMount() throws Exception {
        try (Fake f = new Fake()) {
            f.route("POST", "/v1/transit-eu/encrypt/app-key", 200,
                    "{\"data\":{\"batch_results\":[{\"ciphertext\":\"vault:v1:eu\",\"key_version\":1}]}}");
            TransitBatch tb = new TransitBatch(client(f), "transit-eu");
            List<TransitBatch.EncryptResult> results = tb.encrypt("app-key", List.of(
                    new TransitBatch.EncryptItem("x".getBytes(StandardCharsets.UTF_8))));
            checkEq(f.last().decodedPath, "/v1/transit-eu/encrypt/app-key",
                    "the transit mount name must be part of the path");
            checkEq(results.get(0).ciphertext(), "vault:v1:eu", "custom-mount result");
        }
    }

    public static void main(String[] args) throws Exception {
        testClientPostContract();
        testClientErrorDecoding();
        testEncryptBatchContract();
        testEncryptPartialFailure();
        testDecryptBatch();
        testWholeRequestFailure();
        testCustomMount();
        System.out.println("all tests passed (" + checks + " checks)");
    }
}
