import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Protected harness for the retry-safe vCenter VM provisioning client.
 *
 * <p>Drives {@link VcenterVmProvisioner} against {@link MockVcenterServer} on the loopback
 * interface and asserts the exact wire shape of every request the client emitted, including that
 * optional properties which were never set are absent from the request document rather than
 * transmitted as null, an empty object or an empty array.
 *
 * <p>Usage: {@code java TestMain <path to docs/contract.json>}
 */
public final class TestMain {

    private static final String SESSION_ID = "b8f5c2e1-7a41-4d0e-9c33-6f2a1d54e7b0";
    private static final String FOLDER = "group-v42";

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("usage: TestMain <contract.json>");
        }
        MockVcenterServer vcenter = new MockVcenterServer(Path.of(args[0]), SESSION_ID);
        vcenter.start();
        try {
            VcenterVmProvisioner client =
                    new VcenterVmProvisioner(vcenter.baseUrl(), SESSION_ID);
            vcenter.seedVirtualMachine("vcf-app-01", "group-v-other", "vm-8001");
            createsWhenAbsent(vcenter, client);
            repeatsWithoutDuplicating(vcenter, client);
            sendsEveryConfiguredProperty(vcenter, client);
            omitsPropertiesThatWereNeverSet(vcenter, client);
            serialisesOneQueryParameterPerFilterValue(vcenter, client);
            adoptsTheWinnerOfARace(vcenter, client);
            staysInsideTheContract(vcenter);
        } finally {
            vcenter.stop();
        }
        System.out.println("PASS: retry-safe VM provisioning wire contract verified");
    }

    /** A first call ignores an equal name in another folder and creates in the target folder. */
    private static void createsWhenAbsent(MockVcenterServer vcenter, VcenterVmProvisioner client)
            throws Exception {
        int mark = vcenter.mark();
        VcenterVmProvisioner.Outcome outcome = client.ensureVirtualMachine(minimalRequest());

        check(outcome.created(), "the first call must report that it created the virtual machine");
        check("vm-1001".equals(outcome.vmId()),
                "expected the created identifier vm-1001, got " + outcome.vmId());

        List<MockVcenterServer.Recorded> requests = vcenter.requestsSince(mark);
        check(requests.size() >= 2,
                "expected a lookup before the create, got " + requests);

        MockVcenterServer.Recorded lookup = requests.get(0);
        check("Vcenter.VM_list".equals(lookup.operationId()) && "GET".equals(lookup.method()),
                "the lookup must be GET Vcenter.VM_list, got " + lookup);
        check("/api/vcenter/vm".equals(lookup.path()), "unexpected lookup path: " + lookup.path());
        check(SESSION_ID.equals(lookup.header("vmware-api-session-id")),
                "the lookup must authenticate with the vmware-api-session-id header");
        check(lookup.header("authorization") == null,
                "the contract's security scheme is vmware-api-session-id, not Authorization");
        check(lookup.status() == 200, "the lookup was rejected with HTTP " + lookup.status());

        Map<String, List<String>> query = lookup.query();
        check(query.keySet().equals(Set.of("names", "folders")),
                "the lookup must filter on names and folders only, got " + query.keySet());
        check(List.of("vcf-app-01").equals(query.get("names")),
                "expected one repeated names parameter, got " + query.get("names"));
        check(List.of(FOLDER).equals(query.get("folders")),
                "expected one repeated folders parameter, got " + query.get("folders"));
        for (List<String> values : query.values()) {
            for (String value : values) {
                check(value.indexOf(',') < 0,
                        "form/explode serialisation sends one parameter per value, "
                                + "not a delimiter-joined list: " + value);
            }
        }

        MockVcenterServer.Recorded create = single(creates(requests));
        check(requests.indexOf(create) > requests.indexOf(lookup),
                "the lookup must happen before the create, got " + requests);
        check("Vcenter.VM_create".equals(create.operationId()) && "POST".equals(create.method()),
                "the create must be POST Vcenter.VM_create, got " + create);
        check("/api/vcenter/vm".equals(create.path()), "unexpected create path: " + create.path());
        check(create.rawQuery() == null || create.rawQuery().isEmpty(),
                "Vcenter.VM_create takes no query parameters, got ?" + create.rawQuery());
        check(SESSION_ID.equals(create.header("vmware-api-session-id")),
                "the create must authenticate with the vmware-api-session-id header");
        String contentType = create.header("content-type");
        check(contentType != null && contentType.startsWith("application/json"),
                "the create must be sent as application/json, got " + contentType);
        check(create.status() == 201, "the create was rejected with HTTP " + create.status());

        Map<String, Object> spec = object(create.body());
        assertNothingUnsetWasTransmitted(spec, "");
        check(keys(spec).equals(Set.of("guest_os", "name", "placement")),
                "an unconfigured request must carry only guest_os, name and placement, got "
                        + keys(spec));
        check("RHEL_9_64".equals(spec.get("guest_os")),
                "guest_os is the 9.0 property name and must carry the requested guest OS");
        check("vcf-app-01".equals(spec.get("name")), "the create must name the virtual machine");

        Map<String, Object> placement = nested(spec, "placement");
        check(keys(placement).equals(Set.of("folder", "resource_pool", "datastore")),
                "placement must carry only the properties that were set, got " + keys(placement));
        check(FOLDER.equals(placement.get("folder")), "placement.folder must be the target folder");
        check("resgroup-77".equals(placement.get("resource_pool")),
                "placement.resource_pool must be the requested resource pool");
        check("datastore-31".equals(placement.get("datastore")),
                "placement.datastore must be the requested datastore");
    }

    /** Repeating the same request must not create a second virtual machine. */
    private static void repeatsWithoutDuplicating(
            MockVcenterServer vcenter, VcenterVmProvisioner client) throws Exception {
        int mark = vcenter.mark();
        for (int attempt = 1; attempt <= 3; attempt++) {
            int attemptMark = vcenter.mark();
            VcenterVmProvisioner.Outcome outcome = client.ensureVirtualMachine(minimalRequest());
            check(!outcome.created(),
                    "retry " + attempt + " must report that the virtual machine already existed");
            check("vm-1001".equals(outcome.vmId()),
                    "retry " + attempt + " must adopt vm-1001, got " + outcome.vmId());
            List<MockVcenterServer.Recorded> attemptRequests = vcenter.requestsSince(attemptMark);
            check(!attemptRequests.isEmpty(),
                    "retry " + attempt + " must look up the virtual machine");
            check(creates(attemptRequests).isEmpty(),
                    "retry " + attempt + " must not issue Vcenter.VM_create, got "
                            + attemptRequests);
        }

        List<MockVcenterServer.Recorded> requests = vcenter.requestsSince(mark);
        List<MockVcenterServer.Recorded> creates = creates(requests);
        check(creates.isEmpty(),
                "a retry must not issue Vcenter.VM_create again, got " + creates);
    }

    /** Every property the caller did configure has to reach the wire under its spec name. */
    private static void sendsEveryConfiguredProperty(
            MockVcenterServer vcenter, VcenterVmProvisioner client) throws Exception {
        int mark = vcenter.mark();
        VcenterVmProvisioner.VmRequest request =
                new VcenterVmProvisioner.VmRequest("vcf-app-02", FOLDER, "WINDOWS_SERVER_2025")
                        .resourcePool("resgroup-77")
                        .host("host-19")
                        .cluster("domain-c7")
                        .datastore("datastore-31")
                        .hardwareVersion("VMX_21")
                        .cpu(4, 2)
                        .memoryMib(8192)
                        .newVmdk("system", 42949672960L)
                        .nic("STANDARD_PORTGROUP", "network-8");

        VcenterVmProvisioner.Outcome outcome = client.ensureVirtualMachine(request);
        check(outcome.created() && "vm-1002".equals(outcome.vmId()),
                "expected a fresh machine vm-1002, got " + outcome);

        Map<String, Object> spec = object(single(creates(vcenter.requestsSince(mark))).body());
        assertNothingUnsetWasTransmitted(spec, "");
        check(keys(spec).equals(Set.of(
                        "guest_os", "name", "placement", "hardware_version", "cpu", "memory",
                        "disks", "nics")),
                "a fully configured request must carry exactly its configured properties, got "
                        + keys(spec));
        check("VMX_21".equals(spec.get("hardware_version")), "hardware_version must be transmitted");

        Map<String, Object> placement = nested(spec, "placement");
        check(keys(placement).equals(Set.of("folder", "resource_pool", "host", "cluster", "datastore")),
                "placement must carry all five configured properties, got " + keys(placement));
        check("host-19".equals(placement.get("host")) && "domain-c7".equals(placement.get("cluster")),
                "placement.host and placement.cluster must be transmitted");

        Map<String, Object> cpu = nested(spec, "cpu");
        check(keys(cpu).equals(Set.of("count", "cores_per_socket")),
                "cpu must carry count and cores_per_socket, got " + keys(cpu));
        checkInteger(cpu.get("count"), "4", "cpu.count");
        checkInteger(cpu.get("cores_per_socket"), "2", "cpu.cores_per_socket");

        Map<String, Object> memory = nested(spec, "memory");
        check(keys(memory).equals(Set.of("size_mib")),
                "memory must carry size_mib only, got " + keys(memory));
        checkInteger(memory.get("size_mib"), "8192", "memory.size_mib");

        Map<String, Object> vmdk = nested(only(spec, "disks"), "new_vmdk");
        check(keys(vmdk).equals(Set.of("name", "capacity")),
                "disks[0].new_vmdk must carry name and capacity, got " + keys(vmdk));
        check("system".equals(vmdk.get("name")), "disks[0].new_vmdk.name must be transmitted");
        checkInteger(vmdk.get("capacity"), "42949672960", "disks[0].new_vmdk.capacity");

        Map<String, Object> disk = only(spec, "disks");
        check(keys(disk).equals(Set.of("new_vmdk")),
                "disks[0] must carry new_vmdk only, got " + keys(disk));

        Map<String, Object> nic = only(spec, "nics");
        check(keys(nic).equals(Set.of("backing")), "nics[0] must carry backing only, got " + keys(nic));
        Map<String, Object> backing = nested(nic, "backing");
        check(keys(backing).equals(Set.of("type", "network")),
                "nics[0].backing must carry type and network, got " + keys(backing));
        check("STANDARD_PORTGROUP".equals(backing.get("type")) && "network-8".equals(backing.get("network")),
                "nics[0].backing must carry the requested backing type and network");
    }

    /** Optional properties left unset must be absent, not null and not an empty container. */
    private static void omitsPropertiesThatWereNeverSet(
            MockVcenterServer vcenter, VcenterVmProvisioner client) throws Exception {
        int mark = vcenter.mark();
        VcenterVmProvisioner.VmRequest request =
                new VcenterVmProvisioner.VmRequest("vcf-app-03", FOLDER, "UBUNTU_64")
                        .resourcePool("resgroup-77")
                        .cpu(2, null)
                        .newVmdk(null, 21474836480L)
                        .nic("STANDARD_PORTGROUP", null);

        VcenterVmProvisioner.Outcome outcome = client.ensureVirtualMachine(request);
        check(outcome.created() && "vm-1003".equals(outcome.vmId()),
                "expected a fresh machine vm-1003, got " + outcome);

        String body = single(creates(vcenter.requestsSince(mark))).body();
        Map<String, Object> spec = object(body);
        assertNothingUnsetWasTransmitted(spec, "");
        check(keys(spec).equals(Set.of("guest_os", "name", "placement", "cpu", "disks", "nics")),
                "hardware_version and memory were never set and must be absent, got " + keys(spec));

        check(keys(nested(spec, "placement")).equals(Set.of("folder", "resource_pool")),
                "placement.host, placement.cluster and placement.datastore were never set");
        check(keys(nested(spec, "cpu")).equals(Set.of("count")),
                "cpu.cores_per_socket was never set and must be absent");
        check(keys(nested(only(spec, "disks"), "new_vmdk")).equals(Set.of("capacity")),
                "disks[0].new_vmdk.name was never set and must be absent");
        check(keys(nested(only(spec, "nics"), "backing")).equals(Set.of("type")),
                "nics[0].backing.network was never set and must be absent");
    }

    /**
     * The {@code names} and {@code folders} filters are {@code form}/{@code explode: true} arrays:
     * each value is its own repeated parameter, and a filter that is not applied is left out.
     */
    private static void serialisesOneQueryParameterPerFilterValue(
            MockVcenterServer vcenter, VcenterVmProvisioner client) throws Exception {
        int mark = vcenter.mark();
        List<VcenterVmProvisioner.VmSummary> matches = client.listVirtualMachines(
                List.of("vcf-app-01", "vcf-app-02"), List.of(FOLDER, "group-v99"));

        MockVcenterServer.Recorded lookup = firstRequest(vcenter.requestsSince(mark));
        check(lookup.status() == 200, "the multi-value lookup was rejected: " + lookup);
        check(List.of("vcf-app-01", "vcf-app-02").equals(lookup.query().get("names")),
                "each names value needs its own repeated parameter, got " + lookup.query().get("names")
                        + " from ?" + lookup.rawQuery());
        check(List.of(FOLDER, "group-v99").equals(lookup.query().get("folders")),
                "each folders value needs its own repeated parameter, got "
                        + lookup.query().get("folders") + " from ?" + lookup.rawQuery());
        check(names(matches).equals(Set.of("vcf-app-01", "vcf-app-02")),
                "the lookup must report both matching machines, got " + names(matches));

        mark = vcenter.mark();
        List<VcenterVmProvisioner.VmSummary> everything =
                client.listVirtualMachines(List.of(), List.of());
        MockVcenterServer.Recorded unfiltered = firstRequest(vcenter.requestsSince(mark));
        check(unfiltered.query().isEmpty(),
                "a filter that is not applied is left out of the query string, got ?"
                        + unfiltered.rawQuery());
        check(names(everything).equals(Set.of("vcf-app-01", "vcf-app-02", "vcf-app-03")),
                "an unfiltered lookup must report every machine, got " + names(everything));

        mark = vcenter.mark();
        client.listVirtualMachines(List.of("vcf app&folders=group-v42"), List.of());
        MockVcenterServer.Recorded encoded = firstRequest(vcenter.requestsSince(mark));
        check(encoded.query().keySet().equals(Set.of("names")),
                "filter values must be percent-encoded so they cannot forge another parameter, got "
                        + encoded.query().keySet() + " from ?" + encoded.rawQuery());
        check(List.of("vcf app&folders=group-v42").equals(encoded.query().get("names")),
                "the encoded filter value must arrive intact, got " + encoded.query().get("names"));
    }

    /**
     * When another caller wins the race, the ALREADY_EXISTS answer must be resolved by adopting the
     * existing machine rather than by creating again or by failing.
     */
    private static void adoptsTheWinnerOfARace(
            MockVcenterServer vcenter, VcenterVmProvisioner client) throws Exception {
        vcenter.seedHiddenVirtualMachine("vcf-app-race", FOLDER, "vm-9001");

        int mark = vcenter.mark();
        VcenterVmProvisioner.Outcome outcome = client.ensureVirtualMachine(
                new VcenterVmProvisioner.VmRequest("vcf-app-race", FOLDER, "RHEL_9_64")
                        .resourcePool("resgroup-77"));

        check(!outcome.created(),
                "losing the race is not a creation: the effect had already been applied");
        check("vm-9001".equals(outcome.vmId()),
                "the client must adopt the racing caller's vm-9001, got " + outcome.vmId());

        List<MockVcenterServer.Recorded> requests = vcenter.requestsSince(mark);
        List<MockVcenterServer.Recorded> createRequests = creates(requests);
        check(createRequests.size() == 1,
                "the client must issue Vcenter.VM_create at most once, got " + requests);
        MockVcenterServer.Recorded create = createRequests.get(0);
        int createIndex = requests.indexOf(create);
        check(createIndex > 0,
                "the client must look up the machine before creating it, got " + requests);
        check(create.status() == 400,
                "the mock vCenter was expected to answer ALREADY_EXISTS, got " + create);
        boolean adoptedAfterCreate = false;
        for (int index = createIndex + 1; index < requests.size(); index++) {
            MockVcenterServer.Recorded request = requests.get(index);
            if ("Vcenter.VM_list".equals(request.operationId()) && request.status() == 200) {
                adoptedAfterCreate = true;
            }
        }
        check(adoptedAfterCreate,
                "the client must re-run Vcenter.VM_list after ALREADY_EXISTS, got " + requests);

        int settled = vcenter.mark();
        VcenterVmProvisioner.Outcome again = client.ensureVirtualMachine(
                new VcenterVmProvisioner.VmRequest("vcf-app-race", FOLDER, "RHEL_9_64")
                        .resourcePool("resgroup-77"));
        check(!again.created() && "vm-9001".equals(again.vmId()),
                "a further attempt must settle on vm-9001, got " + again);
        check(creates(vcenter.requestsSince(settled)).isEmpty(),
                "once the machine is visible no further Vcenter.VM_create may be issued");
    }

    /** The client may only touch the two operations the contract names. */
    private static void staysInsideTheContract(MockVcenterServer vcenter) {
        List<MockVcenterServer.Recorded> stray = new ArrayList<>();
        for (MockVcenterServer.Recorded request : vcenter.requests()) {
            if (request.operationId() == null || !"/api/vcenter/vm".equals(request.path())) {
                stray.add(request);
            }
        }
        check(stray.isEmpty(), "the client reached outside the contract: " + stray);
        check(vcenter.requests().size() > 0, "the client never contacted the mock vCenter");
    }

    private static VcenterVmProvisioner.VmRequest minimalRequest() {
        return new VcenterVmProvisioner.VmRequest("vcf-app-01", FOLDER, "RHEL_9_64")
                .resourcePool("resgroup-77")
                .datastore("datastore-31");
    }

    /**
     * Fails on any JSON null, empty object or empty array anywhere in a request document: each of
     * those encodes "unset" differently from the omission the contract requires.
     */
    private static void assertNothingUnsetWasTransmitted(Object node, String pointer) {
        if (node == MockVcenterServer.Json.NULL) {
            check(false, "an unset optional property must be omitted, not sent as null: "
                    + (pointer.isEmpty() ? "<root>" : pointer));
        } else if (node instanceof Map) {
            Map<?, ?> object = (Map<?, ?>) node;
            check(!object.isEmpty(),
                    "an unset optional object must be omitted, not sent empty: " + pointer);
            for (Map.Entry<?, ?> entry : object.entrySet()) {
                assertNothingUnsetWasTransmitted(entry.getValue(), pointer + "/" + entry.getKey());
            }
        } else if (node instanceof List) {
            List<?> array = (List<?>) node;
            check(!array.isEmpty(),
                    "an unset optional array must be omitted, not sent empty: " + pointer);
            for (int index = 0; index < array.size(); index++) {
                assertNothingUnsetWasTransmitted(array.get(index), pointer + "/" + index);
            }
        }
    }

    private static void checkInteger(Object value, String expected, String pointer) {
        check(value instanceof MockVcenterServer.Num,
                pointer + " must be a JSON number, got " + value);
        MockVcenterServer.Num number = (MockVcenterServer.Num) value;
        check(number.isIntegerLiteral(),
                pointer + " must be an integer literal, got " + number.literal());
        check(expected.equals(number.literal()),
                pointer + " must be " + expected + ", got " + number.literal());
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(String body) {
        Object document;
        try {
            document = MockVcenterServer.Json.read(body);
        } catch (RuntimeException malformed) {
            throw new AssertionError("request body is not valid JSON: " + body, malformed);
        }
        check(document instanceof Map, "request body is not a JSON object: " + body);
        return (Map<String, Object>) document;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> nested(Map<String, Object> parent, String property) {
        Object value = parent.get(property);
        check(value instanceof Map, property + " must be a JSON object, got " + value);
        return (Map<String, Object>) value;
    }

    /** The single element of an array-valued property, asserted to be an object. */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> only(Map<String, Object> parent, String property) {
        Object value = parent.get(property);
        check(value instanceof List, property + " must be a JSON array, got " + value);
        List<Object> array = (List<Object>) value;
        check(array.size() == 1, property + " must carry exactly one entry, got " + array);
        check(array.get(0) instanceof Map, property + "[0] must be a JSON object");
        return (Map<String, Object>) array.get(0);
    }

    private static Set<String> keys(Map<String, Object> object) {
        return new LinkedHashSet<>(object.keySet());
    }

    private static List<MockVcenterServer.Recorded> creates(List<MockVcenterServer.Recorded> requests) {
        List<MockVcenterServer.Recorded> creates = new ArrayList<>();
        for (MockVcenterServer.Recorded request : requests) {
            if ("Vcenter.VM_create".equals(request.operationId())) {
                creates.add(request);
            }
        }
        return creates;
    }

    private static MockVcenterServer.Recorded single(List<MockVcenterServer.Recorded> requests) {
        check(requests.size() == 1, "expected exactly one Vcenter.VM_create, got " + requests);
        return requests.get(0);
    }

    private static MockVcenterServer.Recorded firstRequest(List<MockVcenterServer.Recorded> requests) {
        check(!requests.isEmpty(), "expected at least one request");
        return requests.get(0);
    }

    private static Set<String> names(List<VcenterVmProvisioner.VmSummary> summaries) {
        Set<String> names = new LinkedHashSet<>();
        for (VcenterVmProvisioner.VmSummary summary : summaries) {
            names.add(summary.name());
        }
        return names;
    }

    private static void check(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
