import java.net.URI;
import java.util.List;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("usage: TestMain <base-uri> <session-id>");
        }

        VcfLogsClient client = new VcfLogsClient(URI.create(args[0]), args[1]);
        List<VcfLogsClient.Event> events = client.fetchAllEvents(1_700_000_000_100L, 2);
        for (VcfLogsClient.Event event : events) {
            System.out.println(event.timestamp() + "\t" + event.text());
        }
    }
}
