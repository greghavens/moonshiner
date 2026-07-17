/** A non-2xx ONTAP REST response carrying the documented error object. */
public class OntapApiException extends Exception {
    public final int status;
    public final String code;
    public final String apiMessage;

    public OntapApiException(int status, String code, String apiMessage) {
        super("ONTAP API error " + status + " (code " + code + "): " + apiMessage);
        this.status = status;
        this.code = code;
        this.apiMessage = apiMessage;
    }
}
