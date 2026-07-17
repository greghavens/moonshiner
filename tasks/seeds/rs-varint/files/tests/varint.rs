// Acceptance tests for the telemetry varint codec (see the ticket).
// These pin the wire format byte-for-byte: LEB128 base-128 varints with a
// 10-byte ceiling for u64, zigzag mapping for i64, canonical-encoding
// enforcement on decode, and error positions that point at the START of the
// offending varint.

use rs_varint::{
    decode_all_i64, decode_all_u64, encode_i64, encode_u64, zigzag_decode, zigzag_encode,
    DecodeError, Decoder,
};

fn enc_u(values: &[u64]) -> Vec<u8> {
    let mut out = Vec::new();
    for &v in values {
        encode_u64(v, &mut out);
    }
    out
}

fn enc_i(values: &[i64]) -> Vec<u8> {
    let mut out = Vec::new();
    for &v in values {
        encode_i64(v, &mut out);
    }
    out
}

#[test]
fn single_byte_encodings_are_exact() {
    assert_eq!(enc_u(&[0]), vec![0x00]);
    assert_eq!(enc_u(&[1]), vec![0x01]);
    assert_eq!(enc_u(&[127]), vec![0x7F]);
}

#[test]
fn multibyte_encodings_are_exact() {
    assert_eq!(enc_u(&[128]), vec![0x80, 0x01]);
    assert_eq!(enc_u(&[300]), vec![0xAC, 0x02]);
    assert_eq!(enc_u(&[16_383]), vec![0xFF, 0x7F]);
    assert_eq!(enc_u(&[16_384]), vec![0x80, 0x80, 0x01]);
    let mut max = vec![0xFF; 9];
    max.push(0x01);
    assert_eq!(enc_u(&[u64::MAX]), max);
}

#[test]
fn u64_round_trips_across_group_boundaries() {
    let cases: &[u64] = &[
        0,
        1,
        127,
        128,
        129,
        16_383,
        16_384,
        2_097_151,
        2_097_152,
        u32::MAX as u64,
        u64::MAX - 1,
        u64::MAX,
    ];
    for &v in cases {
        let buf = enc_u(&[v]);
        assert_eq!(decode_all_u64(&buf), Ok(vec![v]), "value {v}");
    }
    // encoded widths at the 7-bit group boundaries
    assert_eq!(enc_u(&[0]).len(), 1);
    assert_eq!(enc_u(&[127]).len(), 1);
    assert_eq!(enc_u(&[128]).len(), 2);
    assert_eq!(enc_u(&[16_384]).len(), 3);
    assert_eq!(enc_u(&[u64::MAX]).len(), 10);
}

#[test]
fn zigzag_mapping_is_pinned() {
    let pairs: &[(i64, u64)] = &[
        (0, 0),
        (-1, 1),
        (1, 2),
        (-2, 3),
        (2, 4),
        (i64::MAX, u64::MAX - 1),
        (i64::MIN, u64::MAX),
    ];
    for &(signed, unsigned) in pairs {
        assert_eq!(zigzag_encode(signed), unsigned, "zigzag_encode({signed})");
        assert_eq!(zigzag_decode(unsigned), signed, "zigzag_decode({unsigned})");
    }
}

#[test]
fn i64_round_trips_via_zigzag() {
    let cases: &[i64] = &[0, -1, 1, -64, 63, -65, 64, -300, 300, i64::MIN, i64::MAX];
    for &v in cases {
        let buf = enc_i(&[v]);
        assert_eq!(decode_all_i64(&buf), Ok(vec![v]), "value {v}");
    }
    // small negatives must stay small on the wire — that is the point of zigzag
    assert_eq!(enc_i(&[-1]).len(), 1);
    assert_eq!(enc_i(&[-64]).len(), 1);
    assert_eq!(enc_i(&[-65]).len(), 2);
    assert_eq!(enc_i(&[i64::MIN]).len(), 10);
}

#[test]
fn multi_value_streams_decode_in_order() {
    let values = [3u64, 270, 86_942, 0, u64::MAX];
    let buf = enc_u(&values);
    assert_eq!(decode_all_u64(&buf), Ok(values.to_vec()));

    let signed = [-1i64, 0, i64::MIN, 42, -8_192];
    let sbuf = enc_i(&signed);
    assert_eq!(decode_all_i64(&sbuf), Ok(signed.to_vec()));
}

#[test]
fn decoder_reports_positions_as_it_advances() {
    // widths: 3 -> 1 byte, 270 -> 2, 86942 -> 3, 0 -> 1, u64::MAX -> 10
    let buf = enc_u(&[3, 270, 86_942, 0, u64::MAX]);
    let mut d = Decoder::new(&buf);
    assert_eq!(d.position(), 0);
    assert_eq!(d.remaining(), 17);
    assert_eq!(d.decode_u64(), Ok(Some(3)));
    assert_eq!(d.position(), 1);
    assert_eq!(d.decode_u64(), Ok(Some(270)));
    assert_eq!(d.position(), 3);
    assert_eq!(d.decode_u64(), Ok(Some(86_942)));
    assert_eq!(d.position(), 6);
    assert_eq!(d.decode_u64(), Ok(Some(0)));
    assert_eq!(d.position(), 7);
    assert_eq!(d.decode_u64(), Ok(Some(u64::MAX)));
    assert_eq!(d.position(), 17);
    assert_eq!(d.remaining(), 0);
    // clean end: None, and asking again is still None
    assert_eq!(d.decode_u64(), Ok(None));
    assert_eq!(d.decode_u64(), Ok(None));
    assert_eq!(d.position(), 17);
}

#[test]
fn empty_input_is_a_clean_end() {
    assert_eq!(decode_all_u64(&[]), Ok(vec![]));
    assert_eq!(decode_all_i64(&[]), Ok(vec![]));
    let mut d = Decoder::new(&[]);
    assert_eq!(d.decode_u64(), Ok(None));
    assert_eq!(d.position(), 0);
}

#[test]
fn truncated_streams_report_the_varint_start() {
    assert_eq!(
        decode_all_u64(&[0x80]),
        Err(DecodeError::Truncated { start: 0 })
    );
    assert_eq!(
        decode_all_u64(&[0xFF, 0xFF]),
        Err(DecodeError::Truncated { start: 0 })
    );
    // two good values first: 1 -> [0x01], 300 -> [0xAC, 0x02], then a dangling
    // continuation byte at offset 3
    assert_eq!(
        decode_all_u64(&[0x01, 0xAC, 0x02, 0x80]),
        Err(DecodeError::Truncated { start: 3 })
    );
}

#[test]
fn a_failed_decode_leaves_the_decoder_at_the_varint_start() {
    let buf = [0x01, 0xAC, 0x02, 0x80];
    let mut d = Decoder::new(&buf);
    assert_eq!(d.decode_u64(), Ok(Some(1)));
    assert_eq!(d.decode_u64(), Ok(Some(300)));
    assert_eq!(d.position(), 3);
    assert_eq!(d.decode_u64(), Err(DecodeError::Truncated { start: 3 }));
    assert_eq!(d.position(), 3, "error must not consume input");
    // deterministic retry: same error again, position still parked
    assert_eq!(d.decode_u64(), Err(DecodeError::Truncated { start: 3 }));
    assert_eq!(d.position(), 3);
}

#[test]
fn overlong_encodings_are_rejected() {
    // value 1 padded to two bytes
    assert_eq!(
        decode_all_u64(&[0x81, 0x00]),
        Err(DecodeError::Overlong { start: 0 })
    );
    // value 0 padded to two bytes
    assert_eq!(
        decode_all_u64(&[0x80, 0x00]),
        Err(DecodeError::Overlong { start: 0 })
    );
    // value 127 padded to two bytes
    assert_eq!(
        decode_all_u64(&[0xFF, 0x00]),
        Err(DecodeError::Overlong { start: 0 })
    );
    // three-byte zero
    assert_eq!(
        decode_all_u64(&[0x80, 0x80, 0x00]),
        Err(DecodeError::Overlong { start: 0 })
    );
    // ... but a lone zero byte is the canonical zero
    assert_eq!(decode_all_u64(&[0x00]), Ok(vec![0]));
    // position is the start of the overlong varint, not of the stream
    assert_eq!(
        decode_all_u64(&[0x05, 0x81, 0x00]),
        Err(DecodeError::Overlong { start: 1 })
    );
}

#[test]
fn ten_byte_ceiling_is_enforced() {
    // exactly 1 << 63: nine continuation bytes then 0x01 — legal
    let mut buf = vec![0x80; 9];
    buf.push(0x01);
    assert_eq!(decode_all_u64(&buf), Ok(vec![1u64 << 63]));

    // tenth byte carries more than the single permitted bit
    let mut over = vec![0x80; 9];
    over.push(0x02);
    assert_eq!(
        decode_all_u64(&over),
        Err(DecodeError::Overflow { start: 0 })
    );

    // tenth byte still has the continuation bit set
    assert_eq!(
        decode_all_u64(&[0xFF; 10]),
        Err(DecodeError::Overflow { start: 0 })
    );

    // overflow after a good value reports the second varint's offset
    let mut tail = vec![0x07];
    tail.extend_from_slice(&[0xFF; 10]);
    assert_eq!(
        decode_all_u64(&tail),
        Err(DecodeError::Overflow { start: 1 })
    );
}

#[test]
fn ten_byte_zero_padding_is_overlong_not_overflow() {
    // ten bytes whose payload is all zeros: fits in u64 (it is 0), so the
    // canonicality rule is what rejects it
    let mut buf = vec![0x80; 9];
    buf.push(0x00);
    assert_eq!(
        decode_all_u64(&buf),
        Err(DecodeError::Overlong { start: 0 })
    );
}

#[test]
fn errors_propagate_through_the_signed_api() {
    assert_eq!(
        decode_all_i64(&[0x80]),
        Err(DecodeError::Truncated { start: 0 })
    );
    let mut d = Decoder::new(&[0x02, 0x81, 0x00]);
    assert_eq!(d.decode_i64(), Ok(Some(1)));
    assert_eq!(d.decode_i64(), Err(DecodeError::Overlong { start: 1 }));
    assert_eq!(d.position(), 1);
}

#[test]
fn error_display_messages_are_stable() {
    assert_eq!(
        DecodeError::Truncated { start: 3 }.to_string(),
        "truncated varint starting at byte 3"
    );
    assert_eq!(
        DecodeError::Overlong { start: 0 }.to_string(),
        "overlong varint encoding starting at byte 0"
    );
    assert_eq!(
        DecodeError::Overflow { start: 7 }.to_string(),
        "varint overflows u64 starting at byte 7"
    );
    fn assert_error<E: std::error::Error>(_: &E) {}
    assert_error(&DecodeError::Truncated { start: 0 });
}
