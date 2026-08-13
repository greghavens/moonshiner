import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Dependency-free client for the two vSphere Automation API operations that provision a virtual
 * machine on a VMware Cloud Foundation 9.0 vCenter: {@code Vcenter.VM_list} and
 * {@code Vcenter.VM_create}.
 *
 * <p>The wire contract this client implements is recorded in {@code docs/contract.json}; its
 * provenance is recorded in {@code docs/official_sources.json}.
 */
public final class VcenterVmProvisioner {

    /** Path of both contract operations, relative to the {@code /api} server base path. */
    private static final String VM_PATH = "/vcenter/vm";

    /** Header carrying the vCenter session identifier ({@code api_key_auth} in the contract). */
    private static final String SESSION_HEADER = "vmware-api-session-id";

    private final String baseUrl;
    private final String sessionId;
    private final HttpClient http;

    /**
     * @param baseUrl vCenter API base URL including the {@code /api} server base path, for example
     *                {@code https://vcenter.example.test/api}
     * @param sessionId value sent in the {@code vmware-api-session-id} header
     */
    public VcenterVmProvisioner(String baseUrl, String sessionId) {
        this.baseUrl = trimTrailingSlash(Objects.requireNonNull(baseUrl, "baseUrl"));
        this.sessionId = Objects.requireNonNull(sessionId, "sessionId");
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    }

    /**
     * Provisions the requested virtual machine and reports whether this call is the one that
     * created it.
     *
     * <p>Repeating this call with the same request must leave vCenter holding exactly one virtual
     * machine of that name in that folder.
     */
    public Outcome ensureVirtualMachine(VmRequest request) throws IOException, InterruptedException {
        Objects.requireNonNull(request, "request");
        return new Outcome(createVirtualMachine(request), true);
    }

    /** Invokes {@code Vcenter.VM_create}. Returns the identifier of the new virtual machine. */
    public String createVirtualMachine(VmRequest request) throws IOException, InterruptedException {
        Objects.requireNonNull(request, "request");
        String body = Json.write(request.toCreateSpec());
        HttpRequest post = HttpRequest.newBuilder(URI.create(baseUrl + VM_PATH))
                .header(SESSION_HEADER, sessionId)
                .header("Content-Type", "application/json")
                .header("Accept", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> response = http.send(post, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 201) {
            throw apiFailure("Vcenter.VM_create", response);
        }
        Object identifier = Json.read(response.body());
        if (!(identifier instanceof String)) {
            throw new IOException("Vcenter.VM_create did not answer with a virtual machine identifier");
        }
        return (String) identifier;
    }

    /**
     * Invokes {@code Vcenter.VM_list}, applying the {@code names} and {@code folders} filters.
     *
     * <p>A filter list that is empty is not applied at all, matching the specification's "if
     * missing or null or empty, virtual machines with any name match the filter".
     */
    public List<VmSummary> listVirtualMachines(List<String> names, List<String> folders)
            throws IOException, InterruptedException {
        StringBuilder query = new StringBuilder();
        appendFilter(query, "names", names);
        appendFilter(query, "folders", folders);
        String uri = baseUrl + VM_PATH + (query.length() == 0 ? "" : "?" + query);

        HttpRequest get = HttpRequest.newBuilder(URI.create(uri))
                .header(SESSION_HEADER, sessionId)
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<String> response = http.send(get, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) {
            throw apiFailure("Vcenter.VM_list", response);
        }
        Object document = Json.read(response.body());
        if (!(document instanceof List)) {
            throw new IOException("Vcenter.VM_list did not answer with an array of summaries");
        }
        List<VmSummary> summaries = new ArrayList<>();
        for (Object element : (List<?>) document) {
            if (!(element instanceof Map)) {
                throw new IOException("Vcenter.VM_list returned a malformed summary");
            }
            Map<?, ?> summary = (Map<?, ?>) element;
            summaries.add(new VmSummary(
                    string(summary.get("vm")), string(summary.get("name")), string(summary.get("power_state"))));
        }
        return Collections.unmodifiableList(summaries);
    }

    private static void appendFilter(StringBuilder query, String parameter, List<String> values) {
        if (values == null || values.isEmpty()) {
            return;
        }
        StringBuilder joined = new StringBuilder();
        for (String value : values) {
            if (joined.length() > 0) {
                joined.append(',');
            }
            joined.append(value);
        }
        if (query.length() > 0) {
            query.append('&');
        }
        query.append(parameter).append('=').append(encode(joined.toString()));
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private VcenterApiException apiFailure(String operationId, HttpResponse<String> response) {
        String errorType = null;
        String detail = response.body();
        Object document = null;
        try {
            document = Json.read(response.body());
        } catch (RuntimeException ignored) {
            // A non-JSON error body leaves errorType unset; the status code still classifies it.
        }
        if (document instanceof Map) {
            Map<?, ?> error = (Map<?, ?>) document;
            errorType = string(error.get("error_type"));
            Object messages = error.get("messages");
            if (messages instanceof List && !((List<?>) messages).isEmpty()) {
                Object first = ((List<?>) messages).get(0);
                if (first instanceof Map) {
                    String message = string(((Map<?, ?>) first).get("default_message"));
                    if (message != null) {
                        detail = message;
                    }
                }
            }
        }
        return new VcenterApiException(operationId, response.statusCode(), errorType, detail);
    }

    private static String string(Object value) {
        return value instanceof String ? (String) value : null;
    }

    private static String trimTrailingSlash(String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    /** Result of {@link #ensureVirtualMachine(VmRequest)}. */
    public static final class Outcome {
        private final String vmId;
        private final boolean created;

        public Outcome(String vmId, boolean created) {
            this.vmId = vmId;
            this.created = created;
        }

        /** Identifier of the virtual machine that now exists. */
        public String vmId() {
            return vmId;
        }

        /** True when this call issued the {@code Vcenter.VM_create} that produced the machine. */
        public boolean created() {
            return created;
        }

        @Override
        public String toString() {
            return "Outcome[vmId=" + vmId + ", created=" + created + "]";
        }
    }

    /** A {@code Vcenter.VM.Summary} entry returned by {@code Vcenter.VM_list}. */
    public static final class VmSummary {
        private final String vm;
        private final String name;
        private final String powerState;

        public VmSummary(String vm, String name, String powerState) {
            this.vm = vm;
            this.name = name;
            this.powerState = powerState;
        }

        public String vm() {
            return vm;
        }

        public String name() {
            return name;
        }

        public String powerState() {
            return powerState;
        }
    }

    /** Raised when a contract operation answers with a status the contract classifies as an error. */
    public static final class VcenterApiException extends IOException {
        private static final long serialVersionUID = 1L;

        private final String operationId;
        private final int status;
        private final String errorType;

        public VcenterApiException(String operationId, int status, String errorType, String detail) {
            super(operationId + " failed with HTTP " + status
                    + (errorType == null ? "" : " (" + errorType + ")") + ": " + detail);
            this.operationId = operationId;
            this.status = status;
            this.errorType = errorType;
        }

        public String operationId() {
            return operationId;
        }

        public int status() {
            return status;
        }

        /** The {@code error_type} discriminator of {@code Vapi.Std.Errors.Error}, or null. */
        public String errorType() {
            return errorType;
        }
    }

    /**
     * Desired state of one virtual machine.
     *
     * <p>{@code name}, {@code folder} and {@code guestOs} are required. Every other property is
     * optional in {@code Vcenter.VM.CreateSpec}; a property that is never set on this request stays
     * unset.
     */
    public static final class VmRequest {
        private final String name;
        private final String folder;
        private final String guestOs;

        private String resourcePool;
        private String host;
        private String cluster;
        private String datastore;
        private String hardwareVersion;
        private Integer cpuCount;
        private Integer coresPerSocket;
        private Integer memorySizeMib;
        private boolean diskRequested;
        private String diskName;
        private Long diskCapacityBytes;
        private boolean nicRequested;
        private String nicBackingType;
        private String nicNetwork;

        public VmRequest(String name, String folder, String guestOs) {
            this.name = Objects.requireNonNull(name, "name");
            this.folder = Objects.requireNonNull(folder, "folder");
            this.guestOs = Objects.requireNonNull(guestOs, "guestOs");
        }

        public String name() {
            return name;
        }

        public String folder() {
            return folder;
        }

        public VmRequest resourcePool(String value) {
            this.resourcePool = value;
            return this;
        }

        public VmRequest host(String value) {
            this.host = value;
            return this;
        }

        public VmRequest cluster(String value) {
            this.cluster = value;
            return this;
        }

        public VmRequest datastore(String value) {
            this.datastore = value;
            return this;
        }

        public VmRequest hardwareVersion(String value) {
            this.hardwareVersion = value;
            return this;
        }

        public VmRequest cpu(Integer count, Integer coresPerSocket) {
            this.cpuCount = count;
            this.coresPerSocket = coresPerSocket;
            return this;
        }

        public VmRequest memoryMib(Integer sizeMib) {
            this.memorySizeMib = sizeMib;
            return this;
        }

        /** Requests one new VMDK-backed disk. Either argument may be null to leave it unset. */
        public VmRequest newVmdk(String diskName, Long capacityBytes) {
            this.diskRequested = true;
            this.diskName = diskName;
            this.diskCapacityBytes = capacityBytes;
            return this;
        }

        /** Requests one Ethernet adapter with the given backing. {@code network} may be null. */
        public VmRequest nic(String backingType, String network) {
            this.nicRequested = true;
            this.nicBackingType = Objects.requireNonNull(backingType, "backingType");
            this.nicNetwork = network;
            return this;
        }

        /** Builds the {@code Vcenter.VM.CreateSpec} document for this request. */
        Map<String, Object> toCreateSpec() {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("guest_os", guestOs);
            spec.put("name", name);

            Map<String, Object> placement = new LinkedHashMap<>();
            placement.put("folder", folder);
            placement.put("resource_pool", resourcePool);
            placement.put("host", host);
            placement.put("cluster", cluster);
            placement.put("datastore", datastore);
            spec.put("placement", placement);

            spec.put("hardware_version", hardwareVersion);

            Map<String, Object> cpu = new LinkedHashMap<>();
            cpu.put("count", cpuCount);
            cpu.put("cores_per_socket", coresPerSocket);
            spec.put("cpu", cpu);

            Map<String, Object> memory = new LinkedHashMap<>();
            memory.put("size_mib", memorySizeMib);
            spec.put("memory", memory);

            List<Object> disks = new ArrayList<>();
            if (diskRequested) {
                Map<String, Object> vmdk = new LinkedHashMap<>();
                vmdk.put("name", diskName);
                vmdk.put("capacity", diskCapacityBytes);
                Map<String, Object> disk = new LinkedHashMap<>();
                disk.put("new_vmdk", vmdk);
                disks.add(disk);
            }
            spec.put("disks", disks);

            List<Object> nics = new ArrayList<>();
            if (nicRequested) {
                Map<String, Object> backing = new LinkedHashMap<>();
                backing.put("type", nicBackingType);
                backing.put("network", nicNetwork);
                Map<String, Object> nic = new LinkedHashMap<>();
                nic.put("backing", backing);
                nics.add(nic);
            }
            spec.put("nics", nics);

            return spec;
        }
    }

    /** Minimal JSON reader and writer, sufficient for the documents these two operations exchange. */
    static final class Json {

        private Json() {
        }

        static String write(Object value) {
            StringBuilder out = new StringBuilder();
            writeValue(value, out);
            return out.toString();
        }

        private static void writeValue(Object value, StringBuilder out) {
            if (value == null) {
                out.append("null");
            } else if (value instanceof Map) {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    writeString(String.valueOf(entry.getKey()), out);
                    out.append(':');
                    writeValue(entry.getValue(), out);
                }
                out.append('}');
            } else if (value instanceof List) {
                out.append('[');
                boolean first = true;
                for (Object element : (List<?>) value) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    writeValue(element, out);
                }
                out.append(']');
            } else if (value instanceof String) {
                writeString((String) value, out);
            } else if (value instanceof Boolean || value instanceof Integer || value instanceof Long) {
                out.append(value);
            } else {
                throw new IllegalArgumentException("unsupported JSON value: " + value.getClass());
            }
        }

        private static void writeString(String value, StringBuilder out) {
            out.append('"');
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                switch (character) {
                    case '"':
                        out.append("\\\"");
                        break;
                    case '\\':
                        out.append("\\\\");
                        break;
                    case '\n':
                        out.append("\\n");
                        break;
                    case '\r':
                        out.append("\\r");
                        break;
                    case '\t':
                        out.append("\\t");
                        break;
                    default:
                        if (character < 0x20) {
                            out.append(String.format("\\u%04x", (int) character));
                        } else {
                            out.append(character);
                        }
                }
            }
            out.append('"');
        }

        static Object read(String text) {
            Parser parser = new Parser(text);
            parser.skipWhitespace();
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (!parser.atEnd()) {
                throw new IllegalArgumentException("trailing content in JSON document");
            }
            return value;
        }

        private static final class Parser {
            private final String text;
            private int at;

            Parser(String text) {
                this.text = text;
            }

            boolean atEnd() {
                return at >= text.length();
            }

            void skipWhitespace() {
                while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                    at++;
                }
            }

            Object readValue() {
                skipWhitespace();
                if (atEnd()) {
                    throw new IllegalArgumentException("empty JSON document");
                }
                char character = text.charAt(at);
                switch (character) {
                    case '{':
                        return readObject();
                    case '[':
                        return readArray();
                    case '"':
                        return readString();
                    case 't':
                        expect("true");
                        return Boolean.TRUE;
                    case 'f':
                        expect("false");
                        return Boolean.FALSE;
                    case 'n':
                        expect("null");
                        return null;
                    default:
                        return readNumber();
                }
            }

            private Map<String, Object> readObject() {
                Map<String, Object> object = new LinkedHashMap<>();
                at++;
                skipWhitespace();
                if (!atEnd() && text.charAt(at) == '}') {
                    at++;
                    return object;
                }
                while (true) {
                    skipWhitespace();
                    String key = readString();
                    skipWhitespace();
                    require(':');
                    object.put(key, readValue());
                    skipWhitespace();
                    if (atEnd()) {
                        throw new IllegalArgumentException("unterminated JSON object");
                    }
                    char character = text.charAt(at++);
                    if (character == '}') {
                        return object;
                    }
                    if (character != ',') {
                        throw new IllegalArgumentException("malformed JSON object");
                    }
                }
            }

            private List<Object> readArray() {
                List<Object> array = new ArrayList<>();
                at++;
                skipWhitespace();
                if (!atEnd() && text.charAt(at) == ']') {
                    at++;
                    return array;
                }
                while (true) {
                    array.add(readValue());
                    skipWhitespace();
                    if (atEnd()) {
                        throw new IllegalArgumentException("unterminated JSON array");
                    }
                    char character = text.charAt(at++);
                    if (character == ']') {
                        return array;
                    }
                    if (character != ',') {
                        throw new IllegalArgumentException("malformed JSON array");
                    }
                }
            }

            private String readString() {
                require('"');
                StringBuilder value = new StringBuilder();
                while (true) {
                    if (atEnd()) {
                        throw new IllegalArgumentException("unterminated JSON string");
                    }
                    char character = text.charAt(at++);
                    if (character == '"') {
                        return value.toString();
                    }
                    if (character != '\\') {
                        value.append(character);
                        continue;
                    }
                    char escape = text.charAt(at++);
                    switch (escape) {
                        case '"':
                        case '\\':
                        case '/':
                            value.append(escape);
                            break;
                        case 'b':
                            value.append('\b');
                            break;
                        case 'f':
                            value.append('\f');
                            break;
                        case 'n':
                            value.append('\n');
                            break;
                        case 'r':
                            value.append('\r');
                            break;
                        case 't':
                            value.append('\t');
                            break;
                        case 'u':
                            value.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                            at += 4;
                            break;
                        default:
                            throw new IllegalArgumentException("unsupported JSON escape: \\" + escape);
                    }
                }
            }

            private Object readNumber() {
                int start = at;
                while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                    at++;
                }
                String literal = text.substring(start, at);
                if (literal.isEmpty()) {
                    throw new IllegalArgumentException("malformed JSON value at offset " + start);
                }
                if (literal.indexOf('.') < 0 && literal.indexOf('e') < 0 && literal.indexOf('E') < 0) {
                    return Long.parseLong(literal);
                }
                return Double.parseDouble(literal);
            }

            private void require(char expected) {
                if (atEnd() || text.charAt(at) != expected) {
                    throw new IllegalArgumentException("expected '" + expected + "' at offset " + at);
                }
                at++;
            }

            private void expect(String literal) {
                if (!text.startsWith(literal, at)) {
                    throw new IllegalArgumentException("expected '" + literal + "' at offset " + at);
                }
                at += literal.length();
            }
        }
    }
}
