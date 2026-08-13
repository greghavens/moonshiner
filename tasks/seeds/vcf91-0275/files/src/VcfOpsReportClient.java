/**
 * Single-file client for the VMware Cloud Foundation Operations report-generation
 * operations named in docs/contract.json.
 *
 * The whole client lives in this file: HTTP, JSON encoding, JSON decoding and the
 * polling loop. Java SE standard library only - no third-party dependency, and no
 * additional source files.
 *
 * The public surface below is fixed by docs/contract.json and is compiled against by
 * harness/TestMain.java. Do not rename or re-type anything that is already declared;
 * add whatever private members the implementation needs.
 */
public final class VcfOpsReportClient {

    /** Mirrors the `traversal-spec` schema. Only {@link #name} is required. */
    public static final class TraversalSpec {
        public String name;
        public String description;
        public String rootAdapterKindKey;
        public String rootResourceKindKey;
        public Boolean adapterInstanceAssociation;
    }

    /** Everything one report-generation run needs. Null means "the caller did not supply it". */
    public static final class Request {
        /** Appliance origin with no trailing slash, e.g. {@code http://127.0.0.1:38311}. */
        public String baseUrl;
        public String username;
        public String password;
        /** Optional. */
        public String authSource;
        public String resourceId;
        public String reportDefinitionId;
        /** Optional. */
        public TraversalSpec traversalSpec;
        /** Optional {@code format} query parameter for the download. */
        public String downloadFormat;
        public int maxPolls;
        public long pollIntervalMillis;
    }

    /** Outcome of one report-generation run. */
    public static final class Result {
        /** The id the server assigned to the report. */
        public String reportId;
        /** The terminal status, verbatim as the server sent it. */
        public String finalStatus;
        /** How many getReport calls were actually made. */
        public int pollCount;
        /** The downloaded report body, or null when the terminal status was not COMPLETED. */
        public String downloadBody;
    }

    /**
     * Acquires a token, starts a report, polls it to a terminal status, and downloads it
     * when - and only when - that terminal status is COMPLETED.
     *
     * @throws IllegalStateException if the poll budget is exhausted while the status is
     *                               still non-terminal; the message must contain the report id
     * @throws java.io.IOException   if any response status is outside 200-299; the message
     *                               must contain the numeric status code
     */
    public static Result generateReport(Request request) throws Exception {
        throw new UnsupportedOperationException("VcfOpsReportClient.generateReport is not implemented yet");
    }

    private VcfOpsReportClient() {
    }
}
