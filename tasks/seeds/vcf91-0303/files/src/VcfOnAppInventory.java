import java.io.PrintStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A VCF Operations for Networks application-inventory client.
 *
 * Authenticates, walks the paginated application collection to exhaustion, resolves each
 * application's detail record, and prints the inventory in a stable order.
 *
 * See README.md for the output format and docs/contract.json for the wire contract.
 * MiniJson (harness/MiniJson.java) is on the classpath -- use it rather than adding a dependency.
 */
public final class VcfOnAppInventory {

    /** Everything the client was told on the command line. */
    record Config(String baseUrl,
                  String username,
                  String password,
                  String domainType,
                  String domainValue,
                  int pageSize,
                  Long modifiedAfter) {}

    /** One fully resolved application, ready to print. */
    record Application(String entityId, String name, int tierCount) {}

    public static void main(String[] args) {
        Config config;
        try {
            config = parseArgs(args);
        } catch (IllegalArgumentException ex) {
            System.err.println("error: " + ex.getMessage());
            System.err.println("usage: VcfOnAppInventory --base-url URL --username U --password P "
                    + "--page-size N [--domain-type T] [--domain-value V] [--modified-after MS]");
            System.exit(2);
            return;
        }

        try {
            List<Application> inventory = collectInventory(config);
            emit(inventory, System.out);
        } catch (Exception ex) {
            System.err.println("error: " + ex);
            System.exit(1);
        }
    }

    /**
     * Authenticate, walk every page of the application collection, resolve each application's
     * detail record, and return the inventory sorted into the stable order README.md defines.
     */
    static List<Application> collectInventory(Config config) throws Exception {
        // TODO: obtain a token with the 'create' operation.
        String token = authenticate(config);

        // TODO: walk 'listApplications' to exhaustion and collect the entity ids in the order
        // they are discovered.
        List<String> entityIds = listAllApplicationIds(config, token);

        // TODO: resolve each id with 'getApplicationById', in discovery order, exactly once each.
        List<Application> applications = new ArrayList<>();
        for (String entityId : entityIds) {
            applications.add(fetchApplication(config, token, entityId));
        }

        // TODO: sort into the stable order README.md defines before returning.
        return applications;
    }

    /**
     * POST /api/ni/auth/token -- operationId 'create'.
     *
     * The body is a UserCredential. 'domain' is optional: include it only when a domain is
     * configured, and when it is included give it only the sub-properties that are set.
     *
     * @return the token string from the response.
     */
    static String authenticate(Config config) throws Exception {
        throw new UnsupportedOperationException("authenticate is not implemented");
    }

    /**
     * GET /api/ni/groups/applications -- operationId 'listApplications'.
     *
     * Walks the collection to exhaustion using the opaque cursor and returns every entity id in
     * discovery order. See docs/contract.json for how a walk starts, continues and terminates.
     */
    static List<String> listAllApplicationIds(Config config, String token) throws Exception {
        throw new UnsupportedOperationException("listAllApplicationIds is not implemented");
    }

    /**
     * GET /api/ni/groups/applications/{id} -- operationId 'getApplicationById'.
     *
     * Neither optional query parameter is set in this scenario.
     */
    static Application fetchApplication(Config config, String token, String entityId)
            throws Exception {
        throw new UnsupportedOperationException("fetchApplication is not implemented");
    }

    /** Writes the inventory in the format README.md defines. */
    static void emit(List<Application> applications, PrintStream out) {
        for (Application application : applications) {
            out.println(application.entityId() + "\t" + application.name() + "\t"
                    + application.tierCount());
        }
        out.println("total=" + applications.size());
    }

    // ------------------------------------------------------------------------
    // Command-line plumbing -- provided; the exercise is the wire contract above.
    // ------------------------------------------------------------------------

    static Config parseArgs(String[] args) {
        Map<String, String> values = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i += 2) {
            String key = args[i];
            if (!key.startsWith("--")) {
                throw new IllegalArgumentException("expected a --flag, got '" + key + "'");
            }
            if (i + 1 >= args.length) {
                throw new IllegalArgumentException("flag '" + key + "' has no value");
            }
            values.put(key.substring(2), args[i + 1]);
        }
        String baseUrl = required(values, "base-url");
        while (baseUrl.endsWith("/")) {
            baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
        }
        String pageSize = required(values, "page-size");
        String modifiedAfter = values.get("modified-after");
        return new Config(
                baseUrl,
                required(values, "username"),
                required(values, "password"),
                values.get("domain-type"),
                values.get("domain-value"),
                Integer.parseInt(pageSize),
                modifiedAfter == null ? null : Long.valueOf(modifiedAfter));
    }

    private static String required(Map<String, String> values, String key) {
        String value = values.get(key);
        if (value == null) {
            throw new IllegalArgumentException("missing required flag --" + key);
        }
        return value;
    }
}
