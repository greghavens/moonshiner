import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Drives {@code VcenterRightSizer} against the loopback mock and captures the
 * report it returns.
 *
 * This harness is the only caller of the client. It fixes the client's public
 * shape:
 *
 * <pre>
 *   public VcenterRightSizer(String baseUrl, String username, String password)
 *   public String rightSize(String vmId, long cpuCount, long memorySizeMib,
 *                           long diskCapacityBytes, long diskScsiBus)
 * </pre>
 *
 * {@code rightSize} returns the run report as a JSON document. It is expected
 * to return normally when the vCenter endpoint rejects a step -- a rejected
 * step is a result to report, not an error to propagate.
 *
 * Usage:
 *   java TestMain --base-url http://127.0.0.1:PORT --config config/lab-vcenter.json --out build/report.json
 */
public final class TestMain {

    public static void main(String[] args) {
        Map<String, String> opts = new LinkedHashMap<>();
        for (int i = 0; i < args.length; i++) {
            if (!args[i].startsWith("--") || i + 1 >= args.length) {
                System.err.println("TestMain: expected --name value pairs, got " + args[i]);
                System.exit(2);
            }
            opts.put(args[i], args[++i]);
        }

        String baseUrl = require(opts, "--base-url");
        Path configPath = Path.of(require(opts, "--config"));
        Path outPath = Path.of(require(opts, "--out"));

        String report;
        try {
            Map<String, Object> config = MiniJson.asObject(MiniJson.parse(Files.readString(configPath)));
            Map<String, Object> plan = MiniJson.asObject(config.get("plan"));

            String vm = MiniJson.asString(config.get("vm"));
            String username = MiniJson.asString(config.get("username"));
            String password = MiniJson.asString(config.get("password"));
            long cpuCount = MiniJson.asLong(plan.get("cpu_count"));
            long memorySizeMib = MiniJson.asLong(plan.get("memory_size_mib"));
            long diskCapacityBytes = MiniJson.asLong(plan.get("disk_capacity_bytes"));
            long diskScsiBus = MiniJson.asLong(plan.get("disk_scsi_bus"));

            System.out.println("TestMain: right-sizing " + vm + " via " + baseUrl);
            VcenterRightSizer client = new VcenterRightSizer(baseUrl, username, password);
            report = client.rightSize(vm, cpuCount, memorySizeMib, diskCapacityBytes, diskScsiBus);
        } catch (Throwable t) {
            System.err.println("TestMain: the client did not return a report; it threw instead.");
            t.printStackTrace();
            System.exit(1);
            return;
        }

        if (report == null) {
            System.err.println("TestMain: the client returned null instead of a report.");
            System.exit(1);
            return;
        }

        try {
            if (outPath.getParent() != null) {
                Files.createDirectories(outPath.getParent());
            }
            Files.writeString(outPath, report, StandardCharsets.UTF_8);
        } catch (Exception e) {
            System.err.println("TestMain: could not write " + outPath + ": " + e);
            System.exit(1);
            return;
        }

        System.out.println("TestMain: wrote " + outPath);
        System.out.println(report);
    }

    private static String require(Map<String, String> opts, String name) {
        String value = opts.get(name);
        if (value == null) {
            System.err.println("TestMain: missing required option " + name);
            System.exit(2);
        }
        return value;
    }
}
