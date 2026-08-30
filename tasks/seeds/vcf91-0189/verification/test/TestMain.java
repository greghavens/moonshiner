import java.net.URI;
import java.time.Duration;

public final class TestMain {
    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain BASE_URI");
        }

        VcfLogClient client = new VcfLogClient(
                URI.create(args[0]),
                "integration-jwt-91",
                Duration.ofMillis(5),
                Duration.ofSeconds(2));

        VcfLogClient.Session session =
                client.provisionAgentSession("edge \"collector\" \\ west");

        require("agent-access-91".equals(session.accessToken()), "wrong access token");
        require("edge \"collector\" \\ west".equals(session.name()), "wrong session name");
        require("rotated-secret-43".equals(session.newSecret()), "wrong rotated secret");
        require(session.ttl() == 1_800_000L, "wrong default ttl");
        System.out.println("TestMain: session provisioned after terminal activation");
    }
}
