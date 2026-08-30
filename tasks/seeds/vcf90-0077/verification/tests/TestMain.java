import java.net.URI;
import java.util.Arrays;
import java.util.List;

public final class TestMain {
    private static final List<String> EXPECTED = Arrays.asList(
            "33333333-3333-4333-8333-333333333333",
            "11111111-1111-4111-8111-111111111111",
            "55555555-5555-4555-8555-555555555555",
            "22222222-2222-4222-8222-222222222222",
            "44444444-4444-4444-8444-444444444444");

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <appliance-origin>");
        }

        VcfOperationsClient client = new VcfOperationsClient(
                URI.create(args[0]), "ops-user", "p@ss\"word", null);
        List<String> actual = client.listResourceIds(
                "VMWARE", "VirtualMachine", null, 2);

        if (!EXPECTED.equals(actual)) {
            throw new AssertionError("resource identifiers: expected "
                    + EXPECTED + " but got " + actual);
        }
        System.out.println(String.join(",", actual));
    }
}
