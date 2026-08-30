import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Harness that exercises the single-file client under test.
 *
 * Usage: TestMain &lt;baseUrl&gt; &lt;bearerToken&gt; &lt;filtersJsonPath&gt; &lt;reportOutPath&gt;
 *
 * The filters file is a JSON object whose keys are getTasks filter parameter names. A value may be
 * a string, a JSON null, or a blank string; the last two mean "the caller did not set this filter".
 * The map handed to the client preserves the file's key order, which is deliberately NOT the order
 * the contract declares the parameters in.
 */
public final class TestMain {

    public static void main(String[] args) throws Exception {
        checkApiShape();
        if (args.length == 2 && "--validate-inputs".equals(args[0])) {
            checkInvalidInputs(args[1]);
            return;
        }
        if (args.length != 4) {
            System.err.println("usage: TestMain <baseUrl> <bearerToken> <filtersJson> <reportOut>");
            System.exit(2);
        }
        String baseUrl = args[0];
        String bearerToken = args[1];
        Path filtersPath = Path.of(args[2]);
        Path reportPath = Path.of(args[3]);

        Map<String, Object> raw = Json.obj(Json.parse(Files.readString(filtersPath, StandardCharsets.UTF_8)));
        Map<String, String> filters = new LinkedHashMap<>();
        for (Map.Entry<String, Object> e : raw.entrySet()) {
            Object v = e.getValue();
            filters.put(e.getKey(), v == null ? null : String.valueOf(v));
        }

        FleetTaskInventory client = new FleetTaskInventory(baseUrl, bearerToken);
        String report = client.collect(filters);
        if (report == null) {
            throw new IllegalStateException("collect() returned null");
        }
        Files.writeString(reportPath, report, StandardCharsets.UTF_8);
    }

    private static void checkApiShape() throws Exception {
        Class<?> type = FleetTaskInventory.class;
        int classModifiers = type.getModifiers();
        if (!Modifier.isPublic(classModifiers) || !Modifier.isFinal(classModifiers)) {
            throw new IllegalStateException("FleetTaskInventory must be public and final");
        }

        Constructor<?> constructor = type.getDeclaredConstructor(String.class, String.class);
        if (!Modifier.isPublic(constructor.getModifiers())) {
            throw new IllegalStateException("FleetTaskInventory(String, String) must be public");
        }

        Method collect = type.getDeclaredMethod("collect", Map.class);
        if (!Modifier.isPublic(collect.getModifiers()) || collect.getReturnType() != String.class) {
            throw new IllegalStateException("collect(Map) must be public and return String");
        }
    }

    private static void checkInvalidInputs(String liveBaseUrl) throws Exception {
        expectRejected(null, "valid-token");
        expectRejected(" \t\r\n", "valid-token");
        expectRejected(liveBaseUrl, null);
        expectRejected(liveBaseUrl, " \t\r\n");
    }

    private static void expectRejected(String baseUrl, String bearerToken) throws Exception {
        try {
            FleetTaskInventory client = new FleetTaskInventory(baseUrl, bearerToken);
            client.collect(Collections.emptyMap());
        } catch (Exception expected) {
            return;
        }
        throw new IllegalStateException("invalid baseUrl or bearerToken was accepted");
    }
}
