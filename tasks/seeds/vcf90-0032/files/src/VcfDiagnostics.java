import java.nio.file.Path;

/**
 * Diagnostic client for SDDC-51782.
 *
 * See docs/incident.md for what this has to do and docs/contract.json for the
 * seven SDDC Manager operations that are available, their query parameters and
 * their request-body properties.
 *
 * Keep everything in this one file and use only the JDK standard library
 * (java.net.http.HttpClient is available).
 */
public final class VcfDiagnostics {

    /**
     * Investigates the failed credential rotation and writes {@code outDir/diagnosis.json}.
     *
     * @param baseUrl  base URL of SDDC Manager, e.g. {@code http://127.0.0.1:8080}
     * @param username API username
     * @param password API password
     * @param outDir   directory the diagnosis document is written to
     * @return path of the diagnosis document that was written
     */
    public static Path diagnose(String baseUrl, String username, String password, Path outDir)
            throws Exception {
        throw new UnsupportedOperationException(
                "VcfDiagnostics.diagnose is not implemented yet - see docs/incident.md");
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("usage: VcfDiagnostics <baseUrl> [username] [password] [outDir]");
            System.exit(2);
        }
        String user = args.length > 1 ? args[1] : "administrator@vsphere.local";
        String pass = args.length > 2 ? args[2] : "VMw@re1!VMw@re1!";
        Path out = Path.of(args.length > 3 ? args[3] : "out");
        System.out.println(diagnose(args[0], user, pass, out));
    }

    private VcfDiagnostics() {
    }
}
