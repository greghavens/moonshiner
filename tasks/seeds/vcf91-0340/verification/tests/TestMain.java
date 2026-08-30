import java.net.URI;
import java.util.List;

/** Small process boundary used by the protected verifier. */
public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException("base URI, tenant, refresh token, and output marker required");
        }

        VcfAutomationClient client = new VcfAutomationClient(
                URI.create(args[0]), args[1], args[2]);
        List<String> names = client.listProjectNames();

        System.out.println(args[3] + names.size());
        for (String name : names) {
            System.out.println(args[3] + name);
        }
    }
}
