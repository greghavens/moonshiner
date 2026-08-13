import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;
import java.util.Objects;

/** A small VCF Installer 9.1 client implemented with only the Java standard library. */
public final class VcfInstallerClient {
    public record NetworkSpec(String networkType, int vlanId) {
        public NetworkSpec {
            Objects.requireNonNull(networkType, "networkType");
            if (vlanId < 0 || vlanId > 4094) {
                throw new IllegalArgumentException("vlanId must be between 0 and 4094");
            }
        }
    }

    public record SddcSpec(
            String sddcId,
            String vcenterHostname,
            String rootVcenterPassword,
            List<NetworkSpec> networkSpecs,
            String dnsSubdomain,
            String workflowType,
            String version,
            String vcenterVmSize,
            String vcenterStorageSize,
            List<String> nameservers) {
        public SddcSpec {
            Objects.requireNonNull(sddcId, "sddcId");
            Objects.requireNonNull(vcenterHostname, "vcenterHostname");
            Objects.requireNonNull(rootVcenterPassword, "rootVcenterPassword");
            networkSpecs = List.copyOf(Objects.requireNonNull(networkSpecs, "networkSpecs"));
            Objects.requireNonNull(dnsSubdomain, "dnsSubdomain");
            nameservers = nameservers == null ? null : List.copyOf(nameservers);
        }
    }

    public record DeploymentOutcome(
            String validationId,
            String validationResultStatus,
            String taskId) {
        public boolean deployed() {
            return taskId != null;
        }
    }

    private final URI baseUri;
    private final String bearerToken;
    private final int maxPollAttempts;
    private final HttpClient httpClient;

    public VcfInstallerClient(URI baseUri, String bearerToken, int maxPollAttempts) {
        this.baseUri = Objects.requireNonNull(baseUri, "baseUri");
        this.bearerToken = Objects.requireNonNull(bearerToken, "bearerToken");
        if (maxPollAttempts < 1) {
            throw new IllegalArgumentException("maxPollAttempts must be positive");
        }
        this.maxPollAttempts = maxPollAttempts;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    /**
     * Validate {@code spec}, wait for a terminal validation, and deploy only on success.
     * A null {@code skipValidations} value means that the optional query parameter is absent.
     */
    public DeploymentOutcome precheckThenDeploy(SddcSpec spec, Boolean skipValidations)
            throws IOException, InterruptedException {
        throw new UnsupportedOperationException("TODO: implement the VCF Installer precheck gate");
    }
}
