use rs_cuesheet::{Cue, CueSheet, Rgb};

fn sample_sheet() -> CueSheet {
    CueSheet {
        show_name: "Night Garden".to_string(),
        cues: vec![
            Cue {
                label: "house down".to_string(),
                fade_ms: 4000,
                color: Rgb { r: 0x1a, g: 0x1a, b: 0x2e },
                notes: String::new(),
            },
            Cue {
                label: "moonrise".to_string(),
                fade_ms: 12500,
                color: Rgb { r: 0x40, g: 0x66, b: 0xaa },
                notes: "follow spot standby".to_string(),
            },
        ],
    }
}

const WIRE: &str = r##"{"showName":"Night Garden","cues":[{"label":"house down","fadeMs":4000,"color":"#1a1a2e"},{"label":"moonrise","fadeMs":12500,"color":"#4066aa","notes":"follow spot standby"}]}"##;

#[test]
fn serializes_to_pinned_wire_format() {
    assert_eq!(serde_json::to_string(&sample_sheet()).unwrap(), WIRE);
}

#[test]
fn deserializes_pinned_wire_format() {
    let sheet: CueSheet = serde_json::from_str(WIRE).unwrap();
    assert_eq!(sheet, sample_sheet());
}

#[test]
fn accepts_reordered_keys() {
    let json = r##"{"cues":[{"color":"#000000","fadeMs":1,"label":"blackout"}],"showName":"Test"}"##;
    let sheet: CueSheet = serde_json::from_str(json).unwrap();
    assert_eq!(sheet.show_name, "Test");
    assert_eq!(sheet.cues.len(), 1);
    assert_eq!(sheet.cues[0].color, Rgb { r: 0, g: 0, b: 0 });
}

#[test]
fn missing_notes_defaults_empty_and_stays_off_the_wire() {
    let json = r##"{"showName":"S","cues":[{"label":"a","fadeMs":10,"color":"#ffffff"}]}"##;
    let sheet: CueSheet = serde_json::from_str(json).unwrap();
    assert_eq!(sheet.cues[0].notes, "");
    assert_eq!(serde_json::to_string(&sheet).unwrap(), json);
}

#[test]
fn rejects_unknown_field_on_sheet() {
    let json = r#"{"showName":"S","cues":[],"venue":"main hall"}"#;
    let err = serde_json::from_str::<CueSheet>(json).unwrap_err();
    assert!(
        err.to_string().contains("unknown field `venue`"),
        "unexpected error: {err}"
    );
}

#[test]
fn rejects_unknown_field_on_cue() {
    let json = r##"{"showName":"S","cues":[{"label":"a","fadeMs":10,"color":"#ffffff","gel":"R80"}]}"##;
    let err = serde_json::from_str::<CueSheet>(json).unwrap_err();
    assert!(
        err.to_string().contains("unknown field `gel`"),
        "unexpected error: {err}"
    );
}

#[test]
fn reports_missing_field_by_wire_name() {
    let json = r##"{"showName":"S","cues":[{"label":"a","color":"#ffffff"}]}"##;
    let err = serde_json::from_str::<CueSheet>(json).unwrap_err();
    assert!(
        err.to_string().contains("missing field `fadeMs`"),
        "unexpected error: {err}"
    );
}

#[test]
fn rejects_malformed_colors_with_pinned_message() {
    for bad in ["1a1a2e", "#1a1a2", "#1a1a2ef", "#12g4ff", "##11223"] {
        let json = format!(
            r#"{{"showName":"S","cues":[{{"label":"a","fadeMs":1,"color":"{bad}"}}]}}"#
        );
        let err = serde_json::from_str::<CueSheet>(&json).unwrap_err();
        let want = format!("invalid color \"{bad}\": expected \"#rrggbb\"");
        assert!(err.to_string().contains(&want), "for {bad}: got {err}");
    }
}

#[test]
fn color_serializes_as_lowercase_hex() {
    let cue = Cue {
        label: "warm wash".to_string(),
        fade_ms: 1,
        color: Rgb { r: 255, g: 10, b: 0 },
        notes: String::new(),
    };
    let json = serde_json::to_string(&cue).unwrap();
    assert!(json.contains(r##""color":"#ff0a00""##), "got: {json}");
}

#[test]
fn color_must_be_a_string() {
    let json = r#"{"showName":"S","cues":[{"label":"a","fadeMs":1,"color":1710638}]}"#;
    assert!(serde_json::from_str::<CueSheet>(json).is_err());
}
