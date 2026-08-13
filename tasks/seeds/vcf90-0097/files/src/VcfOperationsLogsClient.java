import java.io.IOException;
import java.net.URI;
import java.util.List;

/** Client implementation exercise for the supplied VCF Operations for Logs contract. */
public final class VcfOperationsLogsClient {
    public record Query(
            String constraintPath,
            Integer limit,
            Integer timeout,
            String view,
            List<String> contentPackFields,
            String orderByDirection) {
    }

    public VcfOperationsLogsClient(
            URI baseUri, String username, String password, String provider) {
        throw new UnsupportedOperationException("Not implemented");
    }

    public List<String> queryEventTexts(List<Query> queries)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("Not implemented");
    }
}
