import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Acceptance tests for the Datadog v2 Reference Tables client.
 *
 * Runs a loopback fake Datadog API (reference-tables subset: uploads,
 * presigned part PUTs, table create/patch/get/list) and drives the client
 * against it. No real Datadog, no real credentials, no Thread.sleep —
 * waiting is injected and recorded. The wire contract the fake enforces is
 * pinned in docs/contract.json. This file and everything under docs/ are
 * protected.
 */
public final class TestMain {

    static final String API_KEY = "ddfixtureapi41d7e2b8a3f605dummy0";
    static final String APP_KEY = "ddfixtureapp9c41d7e2b8a3f605dummy00abcd";
    static final String TABLES = "/api/v2/reference-tables/tables";
    static final String UPLOADS = "/api/v2/reference-tables/uploads";

    static final String CSV =
            "item_sku,stock_level\nSKU-1001,25\nSKU-1002,0\nSKU-1003,113\n";

    static int checks = 0;

    static void check(boolean ok, String message) {
        checks++;
        if (!ok) {
            throw new AssertionError(message);
        }
    }

    record Req(String method, String path, String rawQuery,
               Map<String, String> headers, String body) {
    }

    static final class FakeDatadog {
        final List<Req> requests = new ArrayList<>();
        final Map<String, Deque<String>> tableStatusBodies = new LinkedHashMap<>();
        final Deque<int[]> createTableFailures = new ArrayDeque<>(); // [status] one-shots
        final List<Map<String, Object>> listFixture = new ArrayList<>();
        int uploadCounter = 0;
        HttpServer server;
        String baseUrl;

        void start() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
            baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void stop() {
            server.stop(0);
        }

        Req record(HttpExchange ex) throws IOException {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            Map<String, String> headers = new LinkedHashMap<>();
            ex.getRequestHeaders().forEach((k, v) -> headers.put(k.toLowerCase(), String.join(",", v)));
            Req req = new Req(ex.getRequestMethod(), ex.getRequestURI().getPath(),
                    ex.getRequestURI().getRawQuery(), headers, body);
            requests.add(req);
            return req;
        }

        void respond(HttpExchange ex, int status, String body) throws IOException {
            byte[] payload = body == null ? new byte[0] : body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().set("Content-Type", "application/json");
            ex.sendResponseHeaders(status, payload.length == 0 ? -1 : payload.length);
            if (payload.length > 0) {
                try (OutputStream out = ex.getResponseBody()) {
                    out.write(payload);
                }
            } else {
                ex.close();
            }
        }

        void handle(HttpExchange ex) throws IOException {
            Req req = record(ex);
            String path = req.path();
            try {
                if (req.method().equals("POST") && path.equals(UPLOADS)) {
                    uploadCounter++;
                    String uploadId = "upload-fixture-" + String.format("%04d", uploadCounter);
                    Map<String, Object> reqDoc = Json.parseObject(req.body());
                    Map<String, Object> attrs = attrsOf(reqDoc);
                    int partCount = (int) Math.round((Double) attrs.get("part_count"));
                    List<Object> partUrls = new ArrayList<>();
                    for (int i = 0; i < partCount; i++) {
                        partUrls.add(baseUrl + "/upload-part/" + uploadId + "/" + i);
                    }
                    respond(ex, 201, Json.write(Map.of(
                            "data", Map.of(
                                    "id", uploadId,
                                    "type", "upload",
                                    "attributes", Map.of("part_urls", partUrls)))));
                } else if (req.method().equals("PUT") && path.startsWith("/upload-part/")) {
                    respond(ex, 200, null);
                } else if (req.method().equals("POST") && path.equals(TABLES)) {
                    if (!createTableFailures.isEmpty()) {
                        respond(ex, createTableFailures.pop()[0],
                                "{\"errors\":[\"Table name already exists\"]}");
                        return;
                    }
                    respond(ex, 201, Json.write(Map.of(
                            "data", Map.of(
                                    "id", "tbl-fixture-0001",
                                    "type", "reference_table",
                                    "attributes", Map.of("table_name", "dc_inventory_lookup",
                                            "status", "PROCESSING")))));
                } else if (req.method().equals("GET") && path.equals(TABLES)) {
                    Map<String, String> q = decodeQuery(req.rawQuery());
                    int limit = Integer.parseInt(q.getOrDefault("page[limit]", "15"));
                    int offset = Integer.parseInt(q.getOrDefault("page[offset]", "0"));
                    List<Object> page = new ArrayList<>();
                    for (int i = offset; i < Math.min(offset + limit, listFixture.size()); i++) {
                        page.add(listFixture.get(i));
                    }
                    respond(ex, 200, Json.write(Map.of("data", page)));
                } else if (req.method().equals("GET") && path.startsWith(TABLES + "/")) {
                    String id = path.substring((TABLES + "/").length());
                    Deque<String> bodies = tableStatusBodies.get(id);
                    if (bodies == null || bodies.isEmpty()) {
                        respond(ex, 404, "{\"errors\":[\"Table not found\"]}");
                        return;
                    }
                    String body = bodies.size() > 1 ? bodies.pop() : bodies.peek();
                    respond(ex, 200, body);
                } else if (req.method().equals("PATCH") && path.startsWith(TABLES + "/")) {
                    respond(ex, 200, null);
                } else {
                    respond(ex, 404, "{\"errors\":[\"unexpected request\"]}");
                }
            } catch (RuntimeException e) {
                respond(ex, 500, "{\"errors\":[\"fake server bug: " + e + "\"]}");
            }
        }
    }

    static Map<String, String> decodeQuery(String rawQuery) {
        Map<String, String> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&")) {
            int eq = pair.indexOf('=');
            String key = URLDecoder.decode(eq < 0 ? pair : pair.substring(0, eq), StandardCharsets.UTF_8);
            String value = eq < 0 ? "" : URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
            out.put(key, value);
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> attrsOf(Map<String, Object> doc) {
        Map<String, Object> data = (Map<String, Object>) doc.get("data");
        return (Map<String, Object>) data.get("attributes");
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> dataOf(String body) {
        return (Map<String, Object>) Json.parseObject(body).get("data");
    }

    static String tableBody(String id, String name, String status, long rowCount,
                            Map<String, Object> fileMetadata) {
        Map<String, Object> attrs = new LinkedHashMap<>();
        attrs.put("table_name", name);
        attrs.put("status", status);
        attrs.put("row_count", (double) rowCount);
        attrs.put("source", "LOCAL_FILE");
        if (fileMetadata != null) {
            attrs.put("file_metadata", fileMetadata);
        }
        return Json.write(Map.of("data", Map.of(
                "id", id, "type", "reference_table", "attributes", attrs)));
    }

    static Map<String, Object> listEntry(String id, String name, String status, long rowCount) {
        Map<String, Object> attrs = new LinkedHashMap<>();
        attrs.put("table_name", name);
        attrs.put("status", status);
        attrs.put("row_count", (double) rowCount);
        attrs.put("source", "LOCAL_FILE");
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("id", id);
        entry.put("type", "reference_table");
        entry.put("attributes", attrs);
        return entry;
    }

    static LinkedHashMap<String, String> schemaFields() {
        LinkedHashMap<String, String> fields = new LinkedHashMap<>();
        fields.put("item_sku", "STRING");
        fields.put("stock_level", "INT32");
        return fields;
    }

    static ReferenceTablesClient client(FakeDatadog fake, List<Long> sleeps) {
        return new ReferenceTablesClient(fake.baseUrl, API_KEY, APP_KEY, sleeps::add);
    }

    // ------------------------------------------------------------------

    static void testUploadPartPlanning() {
        check(ReferenceTablesClient.planUploadParts(42).partCount() == 1, "tiny file: 1 part");
        check(ReferenceTablesClient.planUploadParts(42).partSize() == 42,
                "single part carries the whole file");
        check(ReferenceTablesClient.planUploadParts(5_000_000).partCount() == 1,
                "exactly 5MB still fits one part");
        check(ReferenceTablesClient.planUploadParts(5_000_001).partCount() == 2,
                "5MB+1 splits into two parts");
        check(ReferenceTablesClient.planUploadParts(5_000_001).partSize() == 5_000_000,
                "non-final parts must be at least 5,000,000 bytes");
        check(ReferenceTablesClient.planUploadParts(12_345_678).partCount() == 3,
                "ceil division for part count");
        boolean threw = false;
        try {
            ReferenceTablesClient.planUploadParts(100_000_001);
        } catch (IllegalArgumentException e) {
            threw = true;
        }
        check(threw, "more than 20 parts of 5MB must be rejected");
        threw = false;
        try {
            ReferenceTablesClient.planUploadParts(0);
        } catch (IllegalArgumentException e) {
            threw = true;
        }
        check(threw, "empty upload must be rejected");
    }

    static void testLocalSchemaValidation() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            ReferenceTablesClient c = client(fake, new ArrayList<>());
            LinkedHashMap<String, String> badType = schemaFields();
            badType.put("updated_at", "INT64");
            boolean threw = false;
            try {
                c.createTable("dc_inventory_lookup", "desc", List.of(), badType, "item_sku", CSV);
            } catch (IllegalArgumentException e) {
                threw = true;
                check(e.getMessage().contains("INT64"),
                        "field-type error should name the bad type: " + e.getMessage());
            }
            check(threw, "only STRING and INT32 are documented field types");

            threw = false;
            try {
                c.createTable("dc_inventory_lookup", "desc", List.of(), schemaFields(),
                        "warehouse_id", CSV);
            } catch (IllegalArgumentException e) {
                threw = true;
            }
            check(threw, "primary key must be one of the schema fields");
            check(fake.requests.isEmpty(), "invalid specs must never reach the wire");
        } finally {
            fake.stop();
        }
    }

    @SuppressWarnings("unchecked")
    static void testCreateFlowWireContract() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            ReferenceTablesClient c = client(fake, new ArrayList<>());
            String tableId = c.createTable("dc_inventory_lookup",
                    "SKU to stock level lookup for the DC enrichment processor",
                    List.of("team:logistics"), schemaFields(), "item_sku", CSV);
            check("tbl-fixture-0001".equals(tableId), "create returns the table id");

            check(fake.requests.size() == 3, "create is upload -> part PUT -> table POST, got "
                    + fake.requests.size());
            Req upload = fake.requests.get(0);
            Req part = fake.requests.get(1);
            Req create = fake.requests.get(2);

            check(upload.method().equals("POST") && upload.path().equals(UPLOADS),
                    "step 1 must POST " + UPLOADS);
            check(API_KEY.equals(upload.headers().get("dd-api-key")),
                    "DD-API-KEY header on uploads");
            check(APP_KEY.equals(upload.headers().get("dd-application-key")),
                    "DD-APPLICATION-KEY header on uploads");
            check("application/json".equals(upload.headers().get("content-type")),
                    "uploads content type");
            check(upload.rawQuery() == null, "credentials must never travel in the query string");
            Map<String, Object> uploadDoc = Json.parseObject(upload.body());
            check("upload".equals(((Map<String, Object>) uploadDoc.get("data")).get("type")),
                    "upload resource type");
            Map<String, Object> uploadAttrs = attrsOf(uploadDoc);
            check("dc_inventory_lookup".equals(uploadAttrs.get("table_name")),
                    "upload names its table");
            check(List.of("item_sku", "stock_level").equals(uploadAttrs.get("headers")),
                    "upload headers list the CSV columns in order: " + uploadAttrs.get("headers"));
            check(Double.valueOf(1).equals(uploadAttrs.get("part_count")), "small CSV is one part");
            check(Double.valueOf(CSV.getBytes(StandardCharsets.UTF_8).length)
                            .equals(uploadAttrs.get("part_size")),
                    "single-part size is the CSV byte length");

            check(part.method().equals("PUT")
                            && part.path().equals("/upload-part/upload-fixture-0001/0"),
                    "step 2 PUTs the presigned part URL");
            check(CSV.equals(part.body()), "the part body is the raw CSV");
            check(!part.headers().containsKey("dd-api-key")
                            && !part.headers().containsKey("dd-application-key"),
                    "Datadog keys must NOT be forwarded to presigned storage URLs");

            check(create.method().equals("POST") && create.path().equals(TABLES),
                    "step 3 must POST " + TABLES);
            Map<String, Object> createData = (Map<String, Object>) Json.parseObject(create.body()).get("data");
            check("reference_table".equals(createData.get("type")), "table resource type");
            Map<String, Object> attrs = (Map<String, Object>) createData.get("attributes");
            check("dc_inventory_lookup".equals(attrs.get("table_name")), "table_name attribute");
            check("LOCAL_FILE".equals(attrs.get("source")), "source must be LOCAL_FILE");
            check(List.of("team:logistics").equals(attrs.get("tags")), "tags attribute");
            Map<String, Object> schema = (Map<String, Object>) attrs.get("schema");
            List<Object> fields = (List<Object>) schema.get("fields");
            check(fields.size() == 2, "two schema fields");
            check(Map.of("name", "item_sku", "type", "STRING").equals(fields.get(0)),
                    "first schema field: " + fields.get(0));
            check(Map.of("name", "stock_level", "type", "INT32").equals(fields.get(1)),
                    "second schema field: " + fields.get(1));
            check(List.of("item_sku").equals(schema.get("primary_keys")),
                    "primary_keys carries the single documented primary key");
            Map<String, Object> fileMeta = (Map<String, Object>) attrs.get("file_metadata");
            check("upload-fixture-0001".equals(fileMeta.get("upload_id")),
                    "file_metadata links the upload id");
        } finally {
            fake.stop();
        }
    }

    static void testStatusPollingUntilDone() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            Deque<String> bodies = new ArrayDeque<>();
            bodies.add(tableBody("tbl-fixture-0001", "dc_inventory_lookup", "PROCESSING", 0, null));
            bodies.add(tableBody("tbl-fixture-0001", "dc_inventory_lookup", "PROCESSING", 0, null));
            bodies.add(tableBody("tbl-fixture-0001", "dc_inventory_lookup", "DONE", 3, null));
            fake.tableStatusBodies.put("tbl-fixture-0001", bodies);

            List<Long> sleeps = new ArrayList<>();
            ReferenceTablesClient c = client(fake, sleeps);
            ReferenceTablesClient.TableStatus status =
                    c.waitUntilReady("tbl-fixture-0001", 250, 5);
            check("DONE".equals(status.status()), "terminal status is DONE");
            check(status.rowCount() == 3, "row_count decoded from the DONE table");
            check(fake.requests.size() == 3, "one GET per poll, got " + fake.requests.size());
            for (Req r : fake.requests) {
                check(r.method().equals("GET")
                                && r.path().equals(TABLES + "/tbl-fixture-0001"),
                        "polls GET the table by id: " + r.method() + " " + r.path());
            }
            check(List.of(250L, 250L).equals(sleeps),
                    "sleeps only between polls, via the injected sleeper: " + sleeps);
        } finally {
            fake.stop();
        }
    }

    static void testProcessingErrorsAreReported() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            Deque<String> bodies = new ArrayDeque<>();
            bodies.add(tableBody("tbl-fixture-0001", "dc_inventory_lookup", "PROCESSING", 0, null));
            Map<String, Object> errorMeta = new LinkedHashMap<>();
            errorMeta.put("upload_id", "upload-fixture-0001");
            errorMeta.put("error_message", "2 rows failed schema validation");
            errorMeta.put("error_row_count", (double) 2);
            errorMeta.put("error_type", "VALIDATION_ERROR");
            bodies.add(tableBody("tbl-fixture-0001", "dc_inventory_lookup", "ERROR", 1, errorMeta));
            fake.tableStatusBodies.put("tbl-fixture-0001", bodies);

            List<Long> sleeps = new ArrayList<>();
            ReferenceTablesClient c = client(fake, sleeps);
            boolean threw = false;
            try {
                c.waitUntilReady("tbl-fixture-0001", 250, 5);
            } catch (ReferenceTablesClient.TableProcessingException e) {
                threw = true;
                check("2 rows failed schema validation".equals(e.errorMessage()),
                        "error_message surfaced: " + e.errorMessage());
                check(e.errorRowCount() == 2, "error_row_count surfaced");
                check("VALIDATION_ERROR".equals(e.errorType()), "error_type surfaced");
                check(e.getMessage().contains("2 rows failed schema validation"),
                        "exception message carries the vendor detail");
            }
            check(threw, "an ERROR table must raise TableProcessingException");
            check(fake.requests.size() == 2, "stops polling at the terminal ERROR state");
            check(List.of(250L).equals(sleeps), "one sleep before the second poll");
        } finally {
            fake.stop();
        }
    }

    static void testPollingGivesUpAfterMaxPolls() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            Deque<String> bodies = new ArrayDeque<>();
            bodies.add(tableBody("tbl-fixture-0001", "dc_inventory_lookup", "PROCESSING", 0, null));
            fake.tableStatusBodies.put("tbl-fixture-0001", bodies); // last body repeats

            List<Long> sleeps = new ArrayList<>();
            ReferenceTablesClient c = client(fake, sleeps);
            boolean threw = false;
            try {
                c.waitUntilReady("tbl-fixture-0001", 100, 3);
            } catch (IllegalStateException e) {
                threw = true;
            }
            check(threw, "a table still processing after maxPolls raises IllegalStateException");
            check(fake.requests.size() == 3, "maxPolls bounds the GET count");
            check(List.of(100L, 100L).equals(sleeps), "sleeps between polls only");
        } finally {
            fake.stop();
        }
    }

    static void testReplaceRowsPatchesWithNewUpload() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            fake.uploadCounter = 1; // pretend upload-fixture-0001 was already used
            ReferenceTablesClient c = client(fake, new ArrayList<>());
            String newCsv = "item_sku,stock_level\nSKU-1001,7\n";
            c.replaceRows("tbl-fixture-0001", "dc_inventory_lookup",
                    List.of("item_sku", "stock_level"), newCsv);

            check(fake.requests.size() == 3, "replace is upload -> part PUT -> PATCH");
            Req upload = fake.requests.get(0);
            Req part = fake.requests.get(1);
            Req patch = fake.requests.get(2);
            check(upload.method().equals("POST") && upload.path().equals(UPLOADS),
                    "replace starts with a fresh upload");
            check(part.method().equals("PUT") && part.body().equals(newCsv),
                    "replacement CSV goes to the presigned part URL");
            check(patch.method().equals("PATCH")
                            && patch.path().equals(TABLES + "/tbl-fixture-0001"),
                    "row replacement is PATCH on the table, got "
                            + patch.method() + " " + patch.path());
            check("application/json".equals(patch.headers().get("content-type")),
                    "PATCH content type");
            Map<String, Object> data = dataOf(patch.body());
            check("tbl-fixture-0001".equals(data.get("id")), "PATCH data.id is the table id");
            check("reference_table".equals(data.get("type")), "PATCH resource type");
            @SuppressWarnings("unchecked")
            Map<String, Object> attrs = (Map<String, Object>) data.get("attributes");
            @SuppressWarnings("unchecked")
            Map<String, Object> fileMeta = (Map<String, Object>) attrs.get("file_metadata");
            check("upload-fixture-0002".equals(fileMeta.get("upload_id")),
                    "PATCH links the NEW upload id");
            check(!attrs.containsKey("schema"),
                    "PATCH must not resend the schema when only replacing rows");
            check(!attrs.containsKey("source"),
                    "source cannot change after creation and must not be resent");
        } finally {
            fake.stop();
        }
    }

    static void testListTablesOffsetPagination() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            fake.listFixture.add(listEntry("tbl-0001", "dc_inventory_lookup", "DONE", 3));
            fake.listFixture.add(listEntry("tbl-0002", "carrier_zones", "DONE", 120));
            fake.listFixture.add(listEntry("tbl-0003", "sku_owners", "PROCESSING", 0));
            fake.listFixture.add(listEntry("tbl-0004", "site_codes", "DONE", 48));
            fake.listFixture.add(listEntry("tbl-0005", "vendor_ids", "ERROR", 0));

            ReferenceTablesClient c = client(fake, new ArrayList<>());
            List<ReferenceTablesClient.TableSummary> tables = c.listTables(2);

            check(fake.requests.size() == 3, "5 tables at page[limit]=2 is 3 pages, got "
                    + fake.requests.size());
            long[] expectedOffsets = {0, 2, 4};
            for (int i = 0; i < 3; i++) {
                Req r = fake.requests.get(i);
                check(r.method().equals("GET") && r.path().equals(TABLES),
                        "list uses GET " + TABLES);
                Map<String, String> q = decodeQuery(r.rawQuery());
                check("2".equals(q.get("page[limit]")),
                        "documented page[limit] control, got query " + q);
                check(String.valueOf(expectedOffsets[i]).equals(q.get("page[offset]")),
                        "page[offset] advances by the limit: " + q);
                check(API_KEY.equals(r.headers().get("dd-api-key"))
                                && APP_KEY.equals(r.headers().get("dd-application-key")),
                        "auth headers on every list page");
            }
            check(tables.size() == 5, "all pages concatenated");
            check("tbl-0001".equals(tables.get(0).id()), "ids decoded in order");
            check("dc_inventory_lookup".equals(tables.get(0).tableName()), "table_name decoded");
            check("DONE".equals(tables.get(0).status()), "status decoded");
            check(tables.get(1).rowCount() == 120, "row_count decoded");
            check("ERROR".equals(tables.get(4).status()), "last page short row present");
        } finally {
            fake.stop();
        }
    }

    static void testApiErrorEnvelopeAndRedaction() throws IOException {
        FakeDatadog fake = new FakeDatadog();
        fake.start();
        try {
            fake.createTableFailures.add(new int[]{400});
            ReferenceTablesClient c = client(fake, new ArrayList<>());
            boolean threw = false;
            try {
                c.createTable("dc_inventory_lookup", "desc", List.of(), schemaFields(),
                        "item_sku", CSV);
            } catch (ReferenceTablesClient.DatadogApiException e) {
                threw = true;
                check(e.statusCode() == 400, "status code surfaced");
                check(e.errors().contains("Table name already exists"),
                        "errors decoded from the envelope: " + e.errors());
                check(e.getMessage().contains("400")
                                && e.getMessage().contains("Table name already exists"),
                        "message carries status and vendor detail");
                check(!e.getMessage().contains(API_KEY) && !e.getMessage().contains(APP_KEY),
                        "credentials must never leak into exceptions");
            }
            check(threw, "a 400 create must raise DatadogApiException");
        } finally {
            fake.stop();
        }
    }

    static void testProtectedFixturesParse() throws IOException {
        String sources = Files.readString(Path.of("docs", "official_sources.json"));
        Map<String, Object> doc = Json.parseObject(sources);
        @SuppressWarnings("unchecked")
        Map<String, Object> research = (Map<String, Object>) doc.get("research");
        check(Boolean.TRUE.equals(research.get("required")), "research.required");
        check(((List<?>) research.get("official_sources")).size() >= 2,
                "at least two official sources");
        Map<String, Object> contract =
                Json.parseObject(Files.readString(Path.of("docs", "contract.json")));
        check(contract.get("product").equals("Datadog Reference Tables"), "contract product");
    }

    public static void main(String[] args) throws Exception {
        testUploadPartPlanning();
        System.out.println("ok  upload part planning");
        testLocalSchemaValidation();
        System.out.println("ok  local schema validation");
        testCreateFlowWireContract();
        System.out.println("ok  create flow wire contract");
        testStatusPollingUntilDone();
        System.out.println("ok  status polling until DONE");
        testProcessingErrorsAreReported();
        System.out.println("ok  processing errors reported");
        testPollingGivesUpAfterMaxPolls();
        System.out.println("ok  polling gives up after maxPolls");
        testReplaceRowsPatchesWithNewUpload();
        System.out.println("ok  replace rows via new upload + PATCH");
        testListTablesOffsetPagination();
        System.out.println("ok  list tables offset pagination");
        testApiErrorEnvelopeAndRedaction();
        System.out.println("ok  API error envelope and redaction");
        testProtectedFixturesParse();
        System.out.println("ok  protected fixtures parse");
        System.out.println("all tests passed (" + checks + " checks)");
    }
}
