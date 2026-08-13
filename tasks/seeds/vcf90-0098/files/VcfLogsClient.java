import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.util.List;

public final class VcfLogsClient {
    public record Event(long timestamp, String text) {}

    private final URI baseUri;
    private final String sessionId;
    private final HttpClient httpClient;

    public VcfLogsClient(URI baseUri, String sessionId) {
        this.baseUri = baseUri;
        this.sessionId = sessionId;
        this.httpClient = HttpClient.newHttpClient();
    }

    public List<Event> fetchAllEvents(long startTimestampInclusive, int pageSize)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the VCF Operations for Logs client");
    }
}
