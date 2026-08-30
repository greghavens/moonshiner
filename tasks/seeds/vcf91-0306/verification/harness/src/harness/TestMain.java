package harness;

import com.broadcom.vcfops.networks.VcfOpsNetworksClient;
import com.broadcom.vcfops.networks.VcfOpsNetworksClient.Credentials;
import com.broadcom.vcfops.networks.VcfOpsNetworksClient.Domain;
import com.broadcom.vcfops.networks.VcfOpsNetworksClient.OnboardOutcome;
import com.broadcom.vcfops.networks.VcfOpsNetworksClient.VcenterOnboardRequest;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Drives {@link VcfOpsNetworksClient} against a fresh loopback mock per scenario and records what
 * happened into {@code build/report.json} plus one request log per scenario.
 *
 * <p>This harness makes no assertions of its own; {@link Verifier} does all the checking so that
 * verification stays in a single place. A scenario that blows up is recorded, not fatal.
 *
 * <p>Harness file. Do not modify.
 */
public final class TestMain {

    /** vCenter password deliberately containing a quote and a backslash. */
    static final String VC_PASSWORD = "p@ss\"w\\ord1";
    static final String VC_USERNAME = "svc-vcfon@vsphere.local";

    static final String COLLECTOR_DC1 = "Collector_10.220.232.214";
    static final String COLLECTOR_DC2 = "Collector_10.220.232.219 (\"DC2\", rack \\ 4)";
    static final String PLATFORM_NODE = "Platform_10.220.232.210";

    public static void main(String[] args) throws Exception {
        Path root = Path.of(args.length > 0 ? args[0] : ".").toAbsolutePath().normalize();
        Path build = root.resolve("build");
        Map<String, Object> contract =
                Json.parseObject(Files.readString(root.resolve("docs/contract.json"), StandardCharsets.UTF_8));

        List<Object> scenarios = new ArrayList<>();
        for (Scenario s : Scenario.all()) {
            scenarios.add(run(s, contract, build));
        }

        Files.createDirectories(build);
        Files.writeString(build.resolve("report.json"),
                Json.write(Json.map("scenarios", scenarios)), StandardCharsets.UTF_8);
        System.out.println("TestMain: ran " + scenarios.size() + " scenarios -> "
                + build.resolve("report.json"));
    }

    private static Object run(Scenario s, Map<String, Object> contract, Path build) throws Exception {
        Path logPath = build.resolve("requests-" + s.name + ".jsonl");
        MockVcfOnServer mock = new MockVcfOnServer(contract, logPath);
        String baseUrl = mock.start();
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("name", s.name);
        record.put("log_file", "build/requests-" + s.name + ".jsonl");
        try {
            VcfOpsNetworksClient client = new VcfOpsNetworksClient(baseUrl);
            OnboardOutcome outcome = client.onboardVcenter(
                    new Credentials(MockVcfOnServer.API_USERNAME, MockVcfOnServer.API_PASSWORD),
                    s.apiDomain, s.request);
            record.put("threw", Json.NULL);
            record.put("outcome", outcome == null ? Json.NULL : describe(outcome));
        } catch (Throwable t) {
            record.put("threw", Json.map("type", t.getClass().getName(),
                    "message", String.valueOf(t.getMessage())));
            record.put("outcome", Json.NULL);
        } finally {
            mock.stop();
        }
        record.put("created", mock.createdDataSources());
        record.put("request_count", (long) mock.requestLog().size());
        return record;
    }

    private static Object describe(OnboardOutcome o) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("succeeded", o.succeeded);
        m.put("stage", o.stage == null ? Json.NULL : o.stage);
        m.put("failureCode", o.failureCode == null ? Json.NULL : o.failureCode);
        m.put("failureMessage", o.failureMessage == null ? Json.NULL : o.failureMessage);
        m.put("proxyId", o.proxyId == null ? Json.NULL : o.proxyId);
        m.put("precheckCode", o.precheckCode == null ? Json.NULL : Long.valueOf(o.precheckCode));
        m.put("precheckMessage", o.precheckMessage == null ? Json.NULL : o.precheckMessage);
        m.put("entityId", o.entityId == null ? Json.NULL : o.entityId);
        return m;
    }

    // -------------------------------------------------------------- scenarios

    record Scenario(String name, Domain apiDomain, VcenterOnboardRequest request) {

        static List<Scenario> all() {
            List<Scenario> out = new ArrayList<>();

            VcenterOnboardRequest happyIp = base();
            happyIp.ip = "10.197.17.68";
            happyIp.collectorNodeName = COLLECTOR_DC1;
            happyIp.nickname = "vc-dc1";
            out.add(new Scenario("happy-ip-local-domain", new Domain("LOCAL", null), happyIp));

            VcenterOnboardRequest rejected = base();
            rejected.fqdn = "vc-bad.rainpole.local";
            rejected.collectorNodeName = COLLECTOR_DC1;
            rejected.nickname = "vc-bad";
            out.add(new Scenario("precheck-rejected", null, rejected));

            VcenterOnboardRequest options = base();
            options.fqdn = "vc02.rainpole.local";
            options.collectorNodeName = COLLECTOR_DC2;
            options.nickname = "vc-dc2";
            options.notes = "Located in DC2";
            options.enabled = Boolean.FALSE;
            options.ipfixEnabled = Boolean.TRUE;
            options.isVmc = Boolean.FALSE;
            out.add(new Scenario("happy-fqdn-all-options",
                    new Domain("LDAP", "rainpole.local"), options));

            VcenterOnboardRequest explicitFalse = base();
            explicitFalse.ip = "10.197.17.69";
            explicitFalse.collectorNodeName = COLLECTOR_DC1;
            explicitFalse.nickname = "vc-explicit-false";
            explicitFalse.vcenterCredentials = new Credentials(VC_USERNAME, null);
            explicitFalse.enabled = Boolean.TRUE;
            explicitFalse.ipfixEnabled = Boolean.FALSE;
            explicitFalse.isVmc = Boolean.FALSE;
            out.add(new Scenario("happy-ip-explicit-false",
                    new Domain("LOCAL", null), explicitFalse));

            VcenterOnboardRequest missing = base();
            missing.ip = "10.197.17.90";
            missing.collectorNodeName = "Collector_10.220.232.250";
            missing.nickname = "vc-dc3";
            out.add(new Scenario("collector-not-found", new Domain("LOCAL", null), missing));

            VcenterOnboardRequest platform = base();
            platform.ip = "10.197.17.91";
            platform.collectorNodeName = PLATFORM_NODE;
            platform.nickname = "vc-dc4";
            out.add(new Scenario("platform-node-is-not-a-collector", new Domain("LOCAL", null), platform));

            VcenterOnboardRequest ambiguous = base();
            ambiguous.ip = "10.197.17.92";
            ambiguous.fqdn = "vc05.rainpole.local";
            ambiguous.collectorNodeName = COLLECTOR_DC1;
            ambiguous.nickname = "vc-dc5";
            out.add(new Scenario("ambiguous-target", new Domain("LOCAL", null), ambiguous));

            VcenterOnboardRequest noTarget = base();
            noTarget.collectorNodeName = COLLECTOR_DC1;
            noTarget.nickname = "vc-no-target";
            out.add(new Scenario("missing-target", new Domain("LOCAL", null), noTarget));

            VcenterOnboardRequest noCollectorName = base();
            noCollectorName.ip = "10.197.17.93";
            noCollectorName.nickname = "vc-no-collector-name";
            out.add(new Scenario("missing-collector-name", new Domain("LOCAL", null),
                    noCollectorName));

            VcenterOnboardRequest noNickname = base();
            noNickname.ip = "10.197.17.94";
            noNickname.collectorNodeName = COLLECTOR_DC1;
            out.add(new Scenario("missing-nickname", new Domain("LOCAL", null), noNickname));

            VcenterOnboardRequest noCredentials = base();
            noCredentials.ip = "10.197.17.95";
            noCredentials.collectorNodeName = COLLECTOR_DC1;
            noCredentials.nickname = "vc-no-credentials";
            noCredentials.vcenterCredentials = null;
            out.add(new Scenario("missing-vcenter-credentials", new Domain("LOCAL", null),
                    noCredentials));

            VcenterOnboardRequest noCredentialUsername = base();
            noCredentialUsername.ip = "10.197.17.96";
            noCredentialUsername.collectorNodeName = COLLECTOR_DC1;
            noCredentialUsername.nickname = "vc-no-credential-username";
            noCredentialUsername.vcenterCredentials = new Credentials(null, VC_PASSWORD);
            out.add(new Scenario("missing-vcenter-username", new Domain("LOCAL", null),
                    noCredentialUsername));

            return out;
        }

        private static VcenterOnboardRequest base() {
            VcenterOnboardRequest r = new VcenterOnboardRequest();
            r.vcenterCredentials = new Credentials(VC_USERNAME, VC_PASSWORD);
            return r;
        }
    }
}
