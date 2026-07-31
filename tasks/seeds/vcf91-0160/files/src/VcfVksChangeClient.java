import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.List;

/**
 * Coordinates one vSphere Supervisor namespace update with two VKS Cluster
 * merge patches. Implement this file using only the Java 17 standard library.
 */
public final class VcfVksChangeClient {
    public static final String NAMESPACE_UPDATE_OPERATION =
            "Vcenter.Namespaces.Instances_update";
    public static final String CLUSTER_PATCH_OPERATION =
            "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch";

    public enum OverallStatus {
        SUCCEEDED,
        FAILED,
        UNKNOWN
    }

    public enum StepStatus {
        SUCCEEDED,
        FAILED,
        UNKNOWN,
        SKIPPED
    }

    public record Change(
            String namespace,
            String clusterName,
            String namespaceDescription,
            String labelKey,
            String labelValue,
            String targetVersion) {
    }

    public record StepResult(
            String name,
            String operation,
            StepStatus status,
            Integer httpStatus,
            boolean changed) {
    }

    public record ChangeReport(
            OverallStatus overallStatus,
            List<StepResult> steps) {
        public ChangeReport {
            steps = List.copyOf(steps);
        }
    }

    public static final class ChangeTransportException extends IOException {
        private final ChangeReport report;

        public ChangeTransportException(ChangeReport report) {
            super("coordinated change transport failed");
            this.report = report;
        }

        public ChangeReport report() {
            return report;
        }
    }

    public static final class ChangeInterruptedException
            extends InterruptedException {
        private final ChangeReport report;

        public ChangeInterruptedException(ChangeReport report) {
            super("coordinated change interrupted");
            this.report = report;
        }

        public ChangeReport report() {
            return report;
        }
    }

    public VcfVksChangeClient(
            HttpClient httpClient,
            URI vcenterApiBase,
            URI kubernetesOrigin,
            String vcenterSessionId,
            String kubernetesBearerToken,
            Duration timeout) {
        throw new UnsupportedOperationException("TODO");
    }

    public ChangeReport apply(Change change)
            throws ChangeTransportException, ChangeInterruptedException {
        throw new UnsupportedOperationException("TODO");
    }
}
