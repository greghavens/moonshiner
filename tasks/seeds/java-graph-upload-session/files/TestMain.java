import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Acceptance harness: loopback fake of the Microsoft Graph v1.0 drive API
 * (metadata reads plus the createUploadSession / upload-session protocol
 * pinned in docs/contract.json). Checks the existing GraphDriveClient
 * behavior and the new LargeFileUploader feature. Run: java TestMain.java
 * Protected — do not modify this file or anything under docs/.
 */
public class TestMain {

    static final String TOKEN = "dummy-token-77aa41"; // dummy; must never leak
    static final String DRIVE = "d-eng";
    static final String PARENT = "p-root";
    static final int KIB320 = 327_680;
    static final Pattern CONTENT_RANGE = Pattern.compile("bytes (\\d+)-(\\d+)/(\\d+)");

    static int checks = 0;

    static void check(boolean cond, String msg) {
        if (!cond) throw new AssertionError(msg);
        checks++;
    }

    static void checkEq(Object got, Object want, String msg) {
        check(Objects.equals(got, want), msg + " — got <" + got + ">, want <" + want + ">");
    }

    /** Deterministic pseudo-random content so corrupted bytes are caught. */
    static byte[] lcgBytes(int n, long seed) {
        byte[] out = new byte[n];
        long state = seed;
        for (int i = 0; i < n; i++) {
            state = state * 6364136223846793005L + 1442695040888963407L;
            out[i] = (byte) (state >>> 56);
        }
        return out;
    }

    // ---------------------------------------------------------------- fake

    static final class FakeDrive implements AutoCloseable {
        final HttpServer server;
        final String base;
        final String graphBase;

        byte[] expectedContent = new byte[0];
        String expectedName = "";
        String expectedConflict = "replace";

        int sessionCreates = 0;
        final List<String> events = new ArrayList<>();
        final Map<Long, Deque<String>> putFaults = new HashMap<>();
        Long alwaysFault503Start = null;
        int createFaultStatus = 0;
        String createFaultCode = null;
        String createFaultMessage = null;

        String sessionToken = null;
        long nextExpected = 0;
        long declaredTotal = -1;
        ByteArrayOutputStream received = new ByteArrayOutputStream();

        FakeDrive() throws IOException {
            server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
            base = "http://127.0.0.1:" + server.getAddress().getPort();
            graphBase = base + "/v1.0";
            server.createContext("/v1.0/", this::handleGraph);
            server.createContext("/upload/", this::handleUpload);
            server.start();
        }

        void queuePutFault(long start, String mode) {
            putFaults.computeIfAbsent(start, k -> new ArrayDeque<>()).add(mode);
        }

        long countEvents(String prefix) {
            return events.stream().filter(e -> e.startsWith(prefix)).count();
        }

        static String envelope(String code, String message) {
            Map<String, Object> err = new LinkedHashMap<>();
            err.put("code", code);
            err.put("message", message);
            Map<String, Object> root = new LinkedHashMap<>();
            root.put("error", err);
            return Json.write(root);
        }

        static void respond(HttpExchange ex, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            ex.sendResponseHeaders(status, bytes.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
        }

        String statusBody() {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("expirationDateTime", "2026-08-01T00:00:00Z");
            body.put("nextExpectedRanges", List.of(nextExpected + "-"));
            return Json.write(body);
        }

        void handleGraph(HttpExchange ex) throws IOException {
            try {
                String auth = ex.getRequestHeaders().getFirst("Authorization");
                String path = ex.getRequestURI().getPath();
                String query = ex.getRequestURI().getRawQuery();
                String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                if (!("Bearer " + TOKEN).equals(auth)) {
                    respond(ex, 401, envelope("InvalidAuthenticationToken", "Access token is empty or invalid."));
                    return;
                }

                String createPath = "/v1.0/drives/" + DRIVE + "/items/" + PARENT
                        + ":/" + expectedName + ":/createUploadSession";
                if ("POST".equals(ex.getRequestMethod()) && path.equals(createPath)) {
                    handleCreate(ex, body);
                    return;
                }
                if ("GET".equals(ex.getRequestMethod())
                        && path.equals("/v1.0/drives/" + DRIVE + "/items/itm-legacy")) {
                    if (!"$select=id,name,size".equals(query)) {
                        respond(ex, 400, envelope("invalidRequest", "expected $select=id,name,size, got " + query));
                        return;
                    }
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("id", "itm-legacy");
                    item.put("name", "handbook.pdf");
                    item.put("size", 48213);
                    respond(ex, 200, Json.write(item));
                    return;
                }
                if ("GET".equals(ex.getRequestMethod())
                        && path.equals("/v1.0/drives/" + DRIVE + "/items/itm-nope")) {
                    respond(ex, 404, envelope("itemNotFound", "The resource could not be found."));
                    return;
                }
                respond(ex, 404, envelope("itemNotFound", "No such endpoint: " + path));
            } catch (RuntimeException e) {
                respond(ex, 500, envelope("InternalServerError", "mock failure: " + e.getMessage()));
            }
        }

        void handleCreate(HttpExchange ex, String body) throws IOException {
            if (createFaultStatus != 0) {
                respond(ex, createFaultStatus, envelope(createFaultCode, createFaultMessage));
                return;
            }
            String ctype = ex.getRequestHeaders().getFirst("Content-Type");
            if (ctype == null || !ctype.contains("application/json")) {
                respond(ex, 400, envelope("invalidRequest", "createUploadSession needs Content-Type application/json"));
                return;
            }
            Map<String, Object> root;
            Map<String, Object> item;
            try {
                root = Json.object(Json.parse(body));
                item = Json.object(root.get("item"));
            } catch (RuntimeException e) {
                respond(ex, 400, envelope("invalidRequest", "unparseable createUploadSession body: " + body));
                return;
            }
            if (!expectedConflict.equals(item.get("@microsoft.graph.conflictBehavior"))) {
                respond(ex, 400, envelope("invalidRequest",
                        "item.@microsoft.graph.conflictBehavior must be " + expectedConflict));
                return;
            }
            if (!expectedName.equals(item.get("name"))) {
                respond(ex, 400, envelope("invalidRequest",
                        "item.name must match the path segment " + expectedName));
                return;
            }
            sessionCreates++;
            sessionToken = "sess-" + sessionCreates;
            nextExpected = 0;
            declaredTotal = -1;
            received = new ByteArrayOutputStream();
            events.add("CREATE");
            Map<String, Object> resp = new LinkedHashMap<>();
            resp.put("uploadUrl", base + "/upload/" + sessionToken);
            resp.put("expirationDateTime", "2026-08-01T00:00:00Z");
            resp.put("nextExpectedRanges", List.of("0-"));
            respond(ex, 200, Json.write(resp));
        }

        void handleUpload(HttpExchange ex) throws IOException {
            try {
                byte[] body = ex.getRequestBody().readAllBytes();
                if (ex.getRequestHeaders().getFirst("Authorization") != null) {
                    events.add("AUTH-ON-UPLOAD-URL");
                    respond(ex, 401, envelope("unauthenticated",
                            "Preauthenticated upload URLs must not carry an Authorization header."));
                    return;
                }
                String token = ex.getRequestURI().getPath().substring("/upload/".length());
                if (sessionToken == null || !sessionToken.equals(token)) {
                    respond(ex, 404, envelope("itemNotFound", "The upload session no longer exists."));
                    return;
                }
                switch (ex.getRequestMethod()) {
                    case "GET" -> {
                        events.add("STATUS");
                        respond(ex, 200, statusBody());
                    }
                    case "DELETE" -> {
                        events.add("CANCEL");
                        sessionToken = null;
                        ex.sendResponseHeaders(204, -1);
                        ex.close();
                    }
                    case "PUT" -> handlePut(ex, body);
                    default -> respond(ex, 405, envelope("invalidRequest", "unexpected method"));
                }
            } catch (RuntimeException e) {
                respond(ex, 500, envelope("InternalServerError", "mock failure: " + e.getMessage()));
            }
        }

        void handlePut(HttpExchange ex, byte[] body) throws IOException {
            String rangeHeader = ex.getRequestHeaders().getFirst("Content-Range");
            Matcher m = rangeHeader == null ? null : CONTENT_RANGE.matcher(rangeHeader);
            if (m == null || !m.matches()) {
                respond(ex, 400, envelope("invalidRequest", "bad Content-Range: " + rangeHeader));
                return;
            }
            long first = Long.parseLong(m.group(1));
            long last = Long.parseLong(m.group(2));
            long total = Long.parseLong(m.group(3));
            long fragLen = last - first + 1;
            events.add("PUT " + first + "-" + last);

            Deque<String> queue = putFaults.get(first);
            String fault = null;
            if (alwaysFault503Start != null && alwaysFault503Start == first) fault = "503";
            else if (queue != null && !queue.isEmpty()) fault = queue.poll();
            if (fault != null) {
                switch (fault) {
                    case "503" -> {
                        respond(ex, 503, envelope("serviceNotAvailable", "The service is temporarily unavailable."));
                        return;
                    }
                    case "503-store" -> {
                        storeFragment(first, last, total, body);
                        respond(ex, 503, envelope("serviceNotAvailable", "Response lost after the write."));
                        return;
                    }
                    case "404" -> {
                        sessionToken = null;
                        respond(ex, 404, envelope("itemNotFound", "The upload session no longer exists."));
                        return;
                    }
                    default -> throw new IllegalStateException("unknown fault " + fault);
                }
            }

            if (declaredTotal != -1 && declaredTotal != total) {
                respond(ex, 400, envelope("invalidRequest",
                        "total file size changed between fragments: " + declaredTotal + " -> " + total));
                return;
            }
            String lenHeader = ex.getRequestHeaders().getFirst("Content-Length");
            if (lenHeader == null || Long.parseLong(lenHeader) != fragLen || body.length != fragLen) {
                respond(ex, 400, envelope("invalidRequest",
                        "Content-Length/body must match the Content-Range span"));
                return;
            }
            boolean isFinal = last == total - 1;
            if (!isFinal && fragLen % KIB320 != 0) {
                respond(ex, 400, envelope("invalidRequest",
                        "non-final fragments must be multiples of 320 KiB, got " + fragLen));
                return;
            }
            if (fragLen >= 60L * 1024 * 1024) {
                respond(ex, 400, envelope("invalidRequest", "fragment must stay under 60 MiB"));
                return;
            }
            if (first != nextExpected) {
                respond(ex, 416, envelope("invalidRange",
                        "The uploaded fragment is out of sequence with the expected ranges."));
                return;
            }

            storeFragment(first, last, total, body);
            if (nextExpected == total) {
                byte[] got = received.toByteArray();
                if (got.length != expectedContent.length || !java.util.Arrays.equals(got, expectedContent)) {
                    respond(ex, 400, envelope("invalidRequest", "assembled bytes do not match the source file"));
                    return;
                }
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("id", "itm-9001");
                item.put("name", expectedName);
                item.put("size", total);
                item.put("file", new LinkedHashMap<>());
                respond(ex, 201, Json.write(item));
                return;
            }
            respond(ex, 202, statusBody());
        }

        void storeFragment(long first, long last, long total, byte[] body) {
            declaredTotal = total;
            if (first == nextExpected) {
                received.write(body, 0, body.length);
                nextExpected = last + 1;
            }
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    // ---------------------------------------------------------------- helpers

    static final class DelayLog implements java.util.function.LongConsumer {
        final List<Long> seconds = new ArrayList<>();

        @Override
        public void accept(long value) {
            seconds.add(value);
        }
    }

    static String eventString(FakeDrive fake) {
        return String.join(";", fake.events);
    }

    // ---------------------------------------------------------------- tests

    static void testExistingGetItem() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            DriveItem item = client.getItem(DRIVE, "itm-legacy");
            checkEq(item.id(), "itm-legacy", "existing getItem id");
            checkEq(item.name(), "handbook.pdf", "existing getItem name");
            checkEq(item.size(), 48213L, "existing getItem size");
        }
    }

    static void testExistingGetItemError() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            try {
                client.getItem(DRIVE, "itm-nope");
                check(false, "getItem for a missing item must throw");
            } catch (GraphApiException e) {
                checkEq(e.statusCode(), 404, "missing item status");
                checkEq(e.errorCode(), "itemNotFound", "missing item error code");
                check(e.getMessage().contains("could not be found"), "missing item message");
                check(!e.getMessage().contains(TOKEN), "token must not leak into errors");
            }
        }
    }

    static void testFragmentSizeValidation() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            byte[] content = lcgBytes(1024, 7);

            int[] badSizes = { 500_000, 62_914_560, 0, -KIB320 };
            for (int bad : badSizes) {
                DelayLog delay = new DelayLog();
                try {
                    new LargeFileUploader(client, bad, delay, 3)
                            .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");
                    check(false, "fragment size " + bad + " must be rejected");
                } catch (IllegalArgumentException expected) {
                    checks++;
                }
                check(delay.seconds.isEmpty(), "no delays for rejected fragment size " + bad);
            }
            checkEq(fake.sessionCreates, 0, "no session may be created for an illegal fragment size");
            checkEq(fake.events.size(), 0, "no HTTP traffic for an illegal fragment size");
        }
    }

    static void testHappyPathMultiFragment() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            byte[] content = lcgBytes(1_410_720, 42);
            fake.expectedContent = content;
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            DelayLog delay = new DelayLog();

            DriveItem item = new LargeFileUploader(client, 655_360, delay, 3)
                    .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");

            checkEq(item.id(), "itm-9001", "uploaded item id");
            checkEq(item.name(), "walkthrough-2026.mp4", "uploaded item name");
            checkEq(item.size(), 1_410_720L, "uploaded item size");
            checkEq(eventString(fake),
                    "CREATE;PUT 0-655359;PUT 655360-1310719;PUT 1310720-1410719",
                    "exact fragment sequence");
            checkEq(fake.sessionCreates, 1, "one session for a clean upload");
            check(delay.seconds.isEmpty(), "no delays on the happy path");
            checkEq(fake.countEvents("AUTH-ON-UPLOAD-URL"), 0L,
                    "upload PUTs must not carry the bearer token");
        }
    }

    static void testHappyPathSingleFragment() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "notes.txt";
            byte[] content = lcgBytes(100, 3);
            fake.expectedContent = content;
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);

            DriveItem item = new LargeFileUploader(client, KIB320, new DelayLog(), 3)
                    .upload(DRIVE, PARENT, "notes.txt", content, "replace");

            checkEq(item.size(), 100L, "small file size");
            checkEq(eventString(fake), "CREATE;PUT 0-99", "single-fragment sequence");
        }
    }

    static void testTransient503RetriesSameFragment() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            byte[] content = lcgBytes(1_410_720, 42);
            fake.expectedContent = content;
            fake.queuePutFault(655_360, "503");
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            DelayLog delay = new DelayLog();

            DriveItem item = new LargeFileUploader(client, 655_360, delay, 3)
                    .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");

            checkEq(item.size(), 1_410_720L, "size after transient 503");
            checkEq(delay.seconds, List.of(1L), "one backoff delay of 1s");
            checkEq(eventString(fake),
                    "CREATE;PUT 0-655359;PUT 655360-1310719;PUT 655360-1310719;PUT 1310720-1410719",
                    "failed fragment retried in place");
            checkEq(fake.sessionCreates, 1, "no session churn on a transient 503");
        }
    }

    static void testPersistent503ExhaustsBackoff() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            byte[] content = lcgBytes(1_410_720, 42);
            fake.expectedContent = content;
            fake.alwaysFault503Start = 655_360L;
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            DelayLog delay = new DelayLog();

            try {
                new LargeFileUploader(client, 655_360, delay, 3)
                        .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");
                check(false, "persistent 503 must eventually throw");
            } catch (GraphApiException e) {
                checkEq(e.statusCode(), 503, "persistent 503 status");
                check(!e.getMessage().contains(TOKEN), "token must not leak into errors");
            }
            checkEq(delay.seconds, List.of(1L, 2L, 4L), "exponential backoff 1,2,4");
            checkEq(fake.countEvents("PUT 655360-"), 4L, "initial attempt plus maxRetries");
        }
    }

    static void testAmbiguous503Then416RecoversViaStatus() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            byte[] content = lcgBytes(1_410_720, 42);
            fake.expectedContent = content;
            // The server stores the fragment but the 202 is lost: the retry
            // then hits 416 because those bytes already landed.
            fake.queuePutFault(655_360, "503-store");
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            DelayLog delay = new DelayLog();

            DriveItem item = new LargeFileUploader(client, 655_360, delay, 3)
                    .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");

            checkEq(item.size(), 1_410_720L, "size after 416 recovery");
            checkEq(delay.seconds, List.of(1L), "single backoff before the 416");
            checkEq(eventString(fake),
                    "CREATE;PUT 0-655359;PUT 655360-1310719;PUT 655360-1310719;STATUS;PUT 1310720-1410719",
                    "416 consults upload status and resumes from nextExpectedRanges");
            checkEq(fake.sessionCreates, 1, "416 recovery must not create a new session");
        }
    }

    static void testExpiredSession404RestartsOnce() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            byte[] content = lcgBytes(1_410_720, 42);
            fake.expectedContent = content;
            fake.queuePutFault(655_360, "404");
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);
            DelayLog delay = new DelayLog();

            DriveItem item = new LargeFileUploader(client, 655_360, delay, 3)
                    .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");

            checkEq(item.size(), 1_410_720L, "size after session restart");
            checkEq(fake.sessionCreates, 2, "expired session must trigger one fresh session");
            checkEq(eventString(fake),
                    "CREATE;PUT 0-655359;PUT 655360-1310719;"
                    + "CREATE;PUT 0-655359;PUT 655360-1310719;PUT 1310720-1410719",
                    "restart re-uploads from byte 0");
            check(delay.seconds.isEmpty(), "a 404 restart is immediate, not backed off");
        }
    }

    static void testSecondSession404IsTerminal() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            byte[] content = lcgBytes(1_410_720, 42);
            fake.expectedContent = content;
            fake.queuePutFault(655_360, "404");
            fake.queuePutFault(655_360, "404");
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);

            try {
                new LargeFileUploader(client, 655_360, new DelayLog(), 3)
                        .upload(DRIVE, PARENT, "walkthrough-2026.mp4", content, "replace");
                check(false, "a second expired session must be terminal");
            } catch (GraphApiException e) {
                checkEq(e.statusCode(), 404, "terminal expiry status");
            }
            checkEq(fake.sessionCreates, 2, "exactly one restart is allowed");
        }
    }

    static void testCreateSessionDenied() throws Exception {
        try (FakeDrive fake = new FakeDrive()) {
            fake.expectedName = "walkthrough-2026.mp4";
            fake.createFaultStatus = 403;
            fake.createFaultCode = "accessDenied";
            fake.createFaultMessage = "The caller does not have permission to perform the action.";
            GraphDriveClient client = new GraphDriveClient(fake.graphBase, TOKEN);

            try {
                new LargeFileUploader(client, 655_360, new DelayLog(), 3)
                        .upload(DRIVE, PARENT, "walkthrough-2026.mp4", lcgBytes(64, 5), "replace");
                check(false, "denied session creation must throw");
            } catch (GraphApiException e) {
                checkEq(e.statusCode(), 403, "create denial status");
                checkEq(e.errorCode(), "accessDenied", "create denial code");
                check(e.getMessage().contains("permission"), "create denial message");
                check(!e.getMessage().contains(TOKEN), "token must not leak into errors");
            }
            checkEq(fake.sessionCreates, 0, "no session recorded on denial");
        }
    }

    public static void main(String[] args) throws Exception {
        testExistingGetItem();
        testExistingGetItemError();
        testFragmentSizeValidation();
        testHappyPathMultiFragment();
        testHappyPathSingleFragment();
        testTransient503RetriesSameFragment();
        testPersistent503ExhaustsBackoff();
        testAmbiguous503Then416RecoversViaStatus();
        testExpiredSession404RestartsOnce();
        testSecondSession404IsTerminal();
        testCreateSessionDenied();
        System.out.println("ALL TESTS PASSED (" + checks + " checks)");
    }
}
