import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Drives VcenterUpdateClient against the loopback MockVcenter for one scenario/mode pair
 * and writes the observed outcome to a JSON result file.
 *
 *   java TestMain <scenarioDir> <mode> <logFile> <resultFile>
 *
 * mode = "minimal"  -> every optional input is null (they must not appear on the wire)
 * mode = "full"     -> every optional input is supplied
 *
 * PROTECTED HARNESS FILE -- do not modify.
 */
public final class TestMain {

    static final String SESSION_ID = "vcf90-test-session-0001";

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            System.err.println("usage: TestMain <scenarioDir> <mode> <logFile> <resultFile>");
            System.exit(2);
        }
        Path scenarioDir = Path.of(args[0]);
        String mode = args[1];
        Path logFile = Path.of(args[2]);
        Path resultFile = Path.of(args[3]);

        MockVcenter mock = new MockVcenter(scenarioDir, logFile);
        mock.start();

        Map<String, String> userData = new LinkedHashMap<>();
        userData.put("vcsa.root.password", "VMw@re-9.0-\"Test\"\\line\nnext");
        userData.put("backup.confirmed", "true");

        String sourceType;
        String url;
        Boolean listMajorUpgrades;
        String component;
        if (mode.equals("full")) {
            sourceType = "LOCAL";
            url = "https://vcsa-repo.example.com/vc/9.0.1.0100/?channel=ga&arch=x86_64";
            listMajorUpgrades = Boolean.FALSE;
            component = "VMware-vCenter-Server-Appliance";
        } else {
            sourceType = "LOCAL_AND_ONLINE";
            url = null;
            listMajorUpgrades = null;
            component = null;
        }

        StringBuilder out = new StringBuilder();
        out.append("{\"mode\":").append(MockVcenter.quote(mode));
        out.append(",\"scenario\":").append(MockVcenter.quote(scenarioDir.getFileName().toString()));

        try {
            VcenterUpdateClient client = new VcenterUpdateClient(mock.baseUrl(), SESSION_ID);
            VcenterUpdateClient.Result result = client.applyFirstPendingUpdate(
                    sourceType, url, listMajorUpgrades, component, userData);

            out.append(",\"error\":null");
            out.append(",\"installed\":").append(result.installed);
            out.append(",\"version\":")
                    .append(result.version == null ? "null" : MockVcenter.quote(result.version));
            out.append(",\"blocking_issues\":[");
            if (result.blockingIssues != null) {
                for (int i = 0; i < result.blockingIssues.size(); i++) {
                    if (i > 0) {
                        out.append(',');
                    }
                    out.append(MockVcenter.quote(result.blockingIssues.get(i)));
                }
            }
            out.append(']');
        } catch (Throwable t) {
            out.append(",\"error\":").append(MockVcenter.quote(t.getClass().getName() + ": " + t.getMessage()));
            out.append(",\"installed\":false,\"version\":null,\"blocking_issues\":[]");
        } finally {
            out.append(",\"mock\":{\"install_count\":").append(mock.installCount())
                    .append(",\"installed_version\":")
                    .append(mock.installedVersion() == null
                            ? "null" : MockVcenter.quote(mock.installedVersion()))
                    .append("}}");
            mock.stop();
        }

        Files.createDirectories(resultFile.toAbsolutePath().getParent());
        Files.writeString(resultFile, out.toString(), StandardCharsets.UTF_8);
        System.out.println(out);
    }
}
