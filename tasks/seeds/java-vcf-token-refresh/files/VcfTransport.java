/** Anything that can perform an authenticated GET against SDDC Manager. */
public interface VcfTransport {

    /** GET {@code path} and return the parsed JSON body. */
    Object get(String path);
}
