import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Drives VcfOpsCredentialRotator against the loopback mock and prints the
 * outcome as a single RESULT line for the verifier to read.
 *
 * Usage: java TestMain <baseUrl> [--expect-timeout]
 * where baseUrl is the mock origin, e.g. http://127.0.0.1:41234
 */
public final class TestMain {

    private static final String USERNAME = "svc-rotation";
    private static final String PASSWORD = "OldRotationPassw0rd!";
    private static final String NAMED_AUTH_SOURCE = "Imported LDAP Server";

    private static final String OLD_CREDENTIAL_ID =
            "7b3f9c14-2e5a-4d68-9a01-3c6d5e8f1a20";
    private static final String NEW_CREDENTIAL_NAME =
            "vCenter Principal Credential (rotated)";
    private static final int MAX_DRAIN_POLLS = 8;

    public static void main(String[] args) {
        if (args.length < 1 || args.length > 2
                || (args.length == 2 && !"--expect-timeout".equals(args[1]))) {
            System.err.println(
                    "usage: java TestMain <baseUrl> [--expect-timeout]");
            System.exit(2);
        }
        String baseUrl = args[0];
        boolean expectTimeout = args.length == 2;

        Map<String, String> newFields = new LinkedHashMap<>();
        newFields.put("USER", "svc-vcops@vsphere.local");
        newFields.put("PASSWORD", "NewRotationPassw0rd!");

        try {
            VcfOpsCredentialRotator rotator =
                    new VcfOpsCredentialRotator(baseUrl, USERNAME, PASSWORD,
                            expectTimeout ? NAMED_AUTH_SOURCE : null);
            VcfOpsCredentialRotator.RotationResult result = rotator.rotate(
                    OLD_CREDENTIAL_ID, NEW_CREDENTIAL_NAME, newFields,
                    expectTimeout ? 1 : MAX_DRAIN_POLLS);
            if (expectTimeout) {
                System.err.println(
                        "rotate() returned even though the outgoing credential "
                                + "was still in use after the allowed drain poll");
                System.exit(1);
                return;
            }
            System.out.println("RESULT " + toJson(result));
        } catch (Throwable t) {
            if (expectTimeout) {
                System.out.println("EXPECTED_TIMEOUT " + t.getClass().getName()
                        + ": " + t.getMessage());
                return;
            }
            System.out.println("ERROR " + t.getClass().getName() + ": " + t.getMessage());
            t.printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static String toJson(VcfOpsCredentialRotator.RotationResult r) {
        if (r == null) {
            throw new IllegalStateException("rotate() returned null");
        }
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"newCredentialId\":").append(quote(r.newCredentialId));
        sb.append(",\"repointedAdapterIds\":[");
        List<String> ids = r.repointedAdapterIds;
        if (ids != null) {
            for (int i = 0; i < ids.size(); i++) {
                if (i > 0) {
                    sb.append(',');
                }
                sb.append(quote(ids.get(i)));
            }
        }
        sb.append(']');
        sb.append(",\"drainPolls\":").append(r.drainPolls);
        sb.append(",\"oldCredentialDeleted\":").append(r.oldCredentialDeleted);
        sb.append('}');
        return sb.toString();
    }

    private static String quote(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    sb.append("\\\"");
                    break;
                case '\\':
                    sb.append("\\\\");
                    break;
                case '\n':
                    sb.append("\\n");
                    break;
                case '\r':
                    sb.append("\\r");
                    break;
                case '\t':
                    sb.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        return sb.append('"').toString();
    }
}
