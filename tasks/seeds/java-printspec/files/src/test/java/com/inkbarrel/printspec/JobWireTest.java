package com.inkbarrel.printspec;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.exc.InvalidTypeIdException;
import com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException;
import java.util.List;
import org.junit.jupiter.api.Test;

class JobWireTest {

    private static final String CARDS_WIRE =
            "{\"kind\":\"cards\",\"orderRef\":\"ORD-2101\",\"stockName\":\"pearl 350gsm\",\"quantity\":500,\"cornersRounded\":false}";
    private static final String BANNER_WIRE =
            "{\"kind\":\"banner\",\"orderRef\":\"ORD-2107\",\"widthCm\":180,\"heightCm\":60,\"grommets\":true}";
    private static final String BOOKLET_WIRE =
            "{\"kind\":\"booklet\",\"orderRef\":\"ORD-2112\",\"pages\":24,\"staplePattern\":\"saddle\"}";

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void cardsRoundTripsToThePinnedWireForm() throws Exception {
        JobSpec job = new CardsJob("ORD-2101", "pearl 350gsm", 500, false);
        assertEquals(CARDS_WIRE, mapper.writeValueAsString(job));
        assertEquals(job, mapper.readValue(CARDS_WIRE, JobSpec.class));
    }

    @Test
    void bannerRoundTripsToThePinnedWireForm() throws Exception {
        JobSpec job = new BannerJob("ORD-2107", 180, 60, true);
        assertEquals(BANNER_WIRE, mapper.writeValueAsString(job));
        assertEquals(job, mapper.readValue(BANNER_WIRE, JobSpec.class));
    }

    @Test
    void bookletRoundTripsToThePinnedWireForm() throws Exception {
        JobSpec job = new BookletJob("ORD-2112", 24, "saddle");
        assertEquals(BOOKLET_WIRE, mapper.writeValueAsString(job));
        assertEquals(job, mapper.readValue(BOOKLET_WIRE, JobSpec.class));
    }

    @Test
    void kindMayArriveInAnyPosition() throws Exception {
        String shuffled =
                "{\"orderRef\":\"ORD-2107\",\"grommets\":true,\"widthCm\":180,\"heightCm\":60,\"kind\":\"banner\"}";
        assertEquals(new BannerJob("ORD-2107", 180, 60, true), mapper.readValue(shuffled, JobSpec.class));
    }

    @Test
    void mixedListRoundTripsByteForByte() throws Exception {
        String wire = "[" + CARDS_WIRE + "," + BANNER_WIRE + "," + BOOKLET_WIRE + "]";
        TypeReference<List<JobSpec>> listOfJobs = new TypeReference<List<JobSpec>>() {};
        List<JobSpec> jobs = mapper.readValue(wire, listOfJobs);
        assertEquals(
                List.of(
                        new CardsJob("ORD-2101", "pearl 350gsm", 500, false),
                        new BannerJob("ORD-2107", 180, 60, true),
                        new BookletJob("ORD-2112", 24, "saddle")),
                jobs);
        // Type erasure: a writer for the declared list type keeps the kind
        // discriminator on every element, exactly as the intake service does.
        assertEquals(wire, mapper.writerFor(listOfJobs).writeValueAsString(jobs));
    }

    @Test
    void unknownKindIsRejected() {
        String wire = "{\"kind\":\"poster\",\"orderRef\":\"ORD-2150\"}";
        InvalidTypeIdException e =
                assertThrows(InvalidTypeIdException.class, () -> mapper.readValue(wire, JobSpec.class));
        assertEquals("poster", e.getTypeId());
    }

    @Test
    void missingKindIsRejected() {
        String wire = "{\"orderRef\":\"ORD-2199\",\"widthCm\":90,\"heightCm\":30,\"grommets\":false}";
        InvalidTypeIdException e =
                assertThrows(InvalidTypeIdException.class, () -> mapper.readValue(wire, JobSpec.class));
        assertNull(e.getTypeId());
    }

    @Test
    void unknownFieldsAreRejected() {
        String wire =
                "{\"kind\":\"banner\",\"orderRef\":\"ORD-2107\",\"widthCm\":180,\"heightCm\":60,\"grommets\":true,\"laminate\":\"gloss\"}";
        UnrecognizedPropertyException e =
                assertThrows(UnrecognizedPropertyException.class, () -> mapper.readValue(wire, JobSpec.class));
        assertEquals("laminate", e.getPropertyName());
    }
}
