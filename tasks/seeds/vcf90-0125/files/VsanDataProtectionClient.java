import java.net.URI;
import java.util.List;
import java.util.function.Supplier;

/** Minimal vSAN Data Protection client used by TestMain. */
public final class VsanDataProtectionClient {
    public record ProtectionGroup(String id, String name, String status) {}

    public VsanDataProtectionClient(
            URI baseUri, String accessToken, Supplier<String> refreshAccessToken) {
        // TODO: implement
    }

    public ProtectionGroup createProtectionGroupAndReadBack(
            String clusterId, String name, List<String> virtualMachineIds) throws Exception {
        throw new UnsupportedOperationException("Not implemented");
    }
}
