import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/**
 * Minimal Java 17 client for the two vCenter operations selected in
 * docs/contract.json.
 */
public final class VcenterVmClient {
    private final URI baseUri;
    private final Duration requestTimeout;
    private final HttpClient http;

    public record VmSummary(
            String vm,
            String name,
            String powerState,
            Long cpuCount,
            Long memorySizeMib) {
    }

    public VcenterVmClient(URI baseUri, Duration requestTimeout) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.requestTimeout = Objects.requireNonNull(requestTimeout, "requestTimeout");
        this.http = HttpClient.newBuilder()
                .connectTimeout(requestTimeout)
                .build();
    }

    /**
     * Lists VMs for each requested datacenter. If the vCenter session expires,
     * replace it and resume at the interrupted datacenter.
     */
    public List<VmSummary> collectByDatacenters(
            String username,
            String password,
            List<String> datacenterIds) throws IOException, InterruptedException {
        // TODO: implement the spec-derived session/list/resume workflow.
        throw new UnsupportedOperationException("Not implemented");
    }
}
