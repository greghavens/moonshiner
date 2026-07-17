use rs_cragstats::*;

fn ascent(route: &str, grade: &str, tries: u32) -> Ascent {
    Ascent { route: route.to_string(), grade: grade.to_string(), tries }
}

fn kiosk_log() -> Vec<Ascent> {
    vec![
        ascent("Slab of Dreams", "V2", 1),
        ascent("Roofline", "V5", 2),
        ascent("Crimp City", "V4", 1),
        ascent("Long Traverse", "V1", 3),
    ]
}

#[test]
fn ascent_scoring() {
    assert_eq!(ascent("Roofline", "V5", 2).score(), 49);
    assert_eq!(ascent("Slab of Dreams", "V2", 1).score(), 20);
    assert_eq!(ascent("Warmup", "V0", 4).score(), 0);
}

#[test]
fn meeting_summary_lists_strongest_half_best_first() {
    let got = meeting_summary(&kiosk_log());
    assert_eq!(
        got,
        "standouts 2/4\n* V5 Roofline (49)\n* V4 Crimp City (40)"
    );
}

#[test]
fn meeting_summary_stays_generic_for_the_league_importer() {
    #[derive(Debug, Clone)]
    struct LeagueRow {
        team: String,
        points: u32,
    }
    impl Scored for LeagueRow {
        fn score(&self) -> u32 {
            self.points
        }
        fn label(&self) -> String {
            self.team.clone()
        }
    }
    let rows = vec![
        LeagueRow { team: "Crimp Crew".to_string(), points: 12 },
        LeagueRow { team: "Dyno-mite".to_string(), points: 30 },
        LeagueRow { team: "Heel Hooks".to_string(), points: 21 },
    ];
    assert_eq!(
        meeting_summary(&rows),
        "standouts 2/3\n* Dyno-mite (30)\n* Heel Hooks (21)"
    );
}

#[test]
fn tag_lines_verbose_and_terse() {
    let log = kiosk_log();
    let verbose: Vec<String> = tag_lines(&log[..2], true).collect();
    assert_eq!(
        verbose,
        vec![
            "Slab of Dreams [V2] 1 tries".to_string(),
            "Roofline [V5] 2 tries".to_string(),
        ]
    );
    let terse: Vec<String> = tag_lines(&log[..2], false).collect();
    assert_eq!(
        terse,
        vec!["V2 Slab of Dreams".to_string(), "V5 Roofline".to_string()]
    );
}

#[test]
fn wall_card_matches_the_template() {
    let card = wall_card(vec![
        ascent("Roofline", "V5", 2),
        ascent("Crimp City", "V4", 1),
    ]);
    assert_eq!(
        card,
        "SET CARD (2 routes)\nRoofline — V5, x2\nCrimp City — V4, x1\n"
    );
}

#[test]
fn wall_card_for_an_empty_set() {
    assert_eq!(wall_card(Vec::new()), "SET CARD (0 routes)\n");
}
