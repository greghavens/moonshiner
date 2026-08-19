import java.net.URI;

public final class TestMain {
    public static void main(String[] args) throws Exception {
        if (args.length != 6) {
            throw new IllegalArgumentException(
                    "usage: TestMain <base-uri> <token> <project> <integration> <first-result> <second-result>");
        }

        VcfAutomationClient client = new VcfAutomationClient(URI.create(args[0]), args[1]);
        if ("<throws>".equals(args[4])) {
            try {
                client.ensureProjectIntegration(args[2], args[3]);
            } catch (Exception expected) {
                System.out.println("threw");
                return;
            }
            throw new AssertionError("ensureProjectIntegration returned normally; expected an exception");
        }

        String first = client.ensureProjectIntegration(args[2], args[3]);
        String second = client.ensureProjectIntegration(args[2], args[3]);

        if (!args[4].equals(first)) {
            throw new AssertionError("first call returned " + first + ", expected " + args[4]);
        }
        if (!args[5].equals(second)) {
            throw new AssertionError("second call returned " + second + ", expected " + args[5]);
        }
        System.out.println(first + "," + second);
    }
}
