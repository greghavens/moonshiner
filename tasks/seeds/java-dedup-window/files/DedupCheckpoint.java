import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Stable text checkpoint codec used by {@link PartitionedDeduplicator}. */
final class DedupCheckpoint {
    private static final String MAGIC = "partition-dedup-v1";

    private DedupCheckpoint() {
    }

    static String encode(int windowSize, Map<String, Long> highWatermarks,
                         Set<EventPosition> retained) {
        StringBuilder result = new StringBuilder();
        result.append(MAGIC).append('|').append(windowSize).append('\n');

        List<String> partitions = new ArrayList<>(highWatermarks.keySet());
        partitions.sort(String::compareTo);
        Base64.Encoder encoder = Base64.getUrlEncoder().withoutPadding();

        for (String partition : partitions) {
            String encodedPartition = encoder.encodeToString(
                    partition.getBytes(StandardCharsets.UTF_8));
            result.append(encodedPartition)
                    .append('|')
                    .append(highWatermarks.get(partition))
                    .append('|');

            retained.stream()
                    .filter(position -> position.partition().equals(partition))
                    .map(EventPosition::sequence)
                    .sorted()
                    .forEachOrdered(sequence -> result.append(sequence).append(','));
            result.append('\n');
        }
        return result.toString();
    }

    static State decode(String checkpoint) {
        if (checkpoint == null) {
            throw new IllegalArgumentException("checkpoint must not be null");
        }
        try {
            return decodeChecked(checkpoint);
        } catch (IllegalArgumentException failure) {
            if ("invalid checkpoint".equals(failure.getMessage())) {
                throw failure;
            }
            throw new IllegalArgumentException("invalid checkpoint", failure);
        }
    }

    private static State decodeChecked(String checkpoint) {
        String[] lines = checkpoint.split("\\n", -1);
        if (lines.length < 2 || !lines[lines.length - 1].isEmpty()) {
            throw invalid();
        }

        String[] header = lines[0].split("\\|", -1);
        if (header.length != 2 || !MAGIC.equals(header[0])) {
            throw invalid();
        }
        int windowSize = Integer.parseInt(header[1]);
        if (windowSize < 1) {
            throw invalid();
        }

        Map<String, Long> highWatermarks = new HashMap<>();
        Set<EventPosition> retained = new HashSet<>();
        Base64.Decoder decoder = Base64.getUrlDecoder();

        for (int lineNumber = 1; lineNumber < lines.length - 1; lineNumber++) {
            String[] fields = lines[lineNumber].split("\\|", -1);
            if (fields.length != 3) {
                throw invalid();
            }
            String partition = new String(decoder.decode(fields[0]), StandardCharsets.UTF_8);
            if (partition.isEmpty() || highWatermarks.containsKey(partition)) {
                throw invalid();
            }
            long highWatermark = Long.parseLong(fields[1]);
            if (highWatermark < 0) {
                throw invalid();
            }
            highWatermarks.put(partition, highWatermark);

            long minimum = minimumRetained(highWatermark, windowSize);
            if (!fields[2].isEmpty()) {
                String[] sequences = fields[2].split(",", -1);
                if (!sequences[sequences.length - 1].isEmpty()) {
                    throw invalid();
                }
                for (int index = 0; index < sequences.length - 1; index++) {
                    long sequence = Long.parseLong(sequences[index]);
                    EventPosition position = new EventPosition(partition, sequence);
                    if (sequence < minimum || sequence > highWatermark
                            || !retained.add(position)) {
                        throw invalid();
                    }
                }
            }
        }
        return new State(windowSize, highWatermarks, retained);
    }

    private static long minimumRetained(long highWatermark, int windowSize) {
        return Math.max(0L, highWatermark - (long) windowSize + 1L);
    }

    private static IllegalArgumentException invalid() {
        return new IllegalArgumentException("invalid checkpoint");
    }

    record State(int windowSize, Map<String, Long> highWatermarks,
                 Set<EventPosition> retained) {
    }
}
