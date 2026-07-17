import java.util.Objects;

/** One programmed track in a broadcast setlist. */
public final class Track {
    private final String title;
    private final String artist;
    private final int seconds;
    private final boolean explicitLyrics;

    public Track(String title, String artist, int seconds, boolean explicitLyrics) {
        this.title = Objects.requireNonNull(title, "title");
        this.artist = Objects.requireNonNull(artist, "artist");
        if (seconds <= 0) {
            throw new IllegalArgumentException("seconds must be positive, got " + seconds);
        }
        this.seconds = seconds;
        this.explicitLyrics = explicitLyrics;
    }

    public String title() { return title; }
    public String artist() { return artist; }
    public int seconds() { return seconds; }
    public boolean explicitLyrics() { return explicitLyrics; }

    @Override
    public String toString() {
        return "\"" + title + "\" by " + artist + " (" + seconds + "s"
                + (explicitLyrics ? ", explicit" : "") + ")";
    }
}
