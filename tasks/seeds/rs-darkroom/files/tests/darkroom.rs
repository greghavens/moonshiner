use rs_darkroom::chem::{dilution, Batch, Developer};
use rs_darkroom::log::entry::Entry;
use rs_darkroom::log::{session_minutes, FilmStock};
use rs_darkroom::report::contact_sheet;

fn tri_x() -> FilmStock {
    FilmStock { name: "Tri-X".to_string(), iso: 400 }
}

fn fp4() -> FilmStock {
    FilmStock { name: "FP4+".to_string(), iso: 125 }
}

fn tonight() -> (Vec<Entry>, Batch) {
    let entries = vec![
        Entry::new(tri_x(), Developer::D76, 9, 40),
        Entry::new(fp4(), Developer::D76, 9, 24),
    ];
    let batch = Batch { developer: Developer::D76, stock_ml: 50, water_ml: 450 };
    (entries, batch)
}

#[test]
fn film_stock_is_one_type_under_both_module_paths() {
    // The chem module has always re-exported the stock type for the
    // shopping-list code; both spellings must name the same struct.
    let a: rs_darkroom::chem::FilmStock = tri_x();
    let b: rs_darkroom::log::FilmStock = a;
    assert_eq!(b.iso, 400);
}

#[test]
fn dilution_labels_reduce_like_the_bottles() {
    let (_, batch) = tonight();
    assert_eq!(dilution(&batch), "1+9");
    let strong = Batch { developer: Developer::Rodinal, stock_ml: 20, water_ml: 500 };
    assert_eq!(dilution(&strong), "1+25");
}

#[test]
fn sheet_frames_cap_at_the_grid() {
    let long_roll = Entry::new(tri_x(), Developer::Xtol, 7, 40);
    assert_eq!(long_roll.sheet_frames(), 36);
    let half_roll = Entry::new(fp4(), Developer::Xtol, 7, 24);
    assert_eq!(half_roll.sheet_frames(), 24);
}

#[test]
fn session_minutes_totals_the_night() {
    let (entries, _) = tonight();
    assert_eq!(session_minutes(&entries), 18);
}

#[test]
fn contact_sheet_reads_exactly_like_the_printout() {
    let (entries, batch) = tonight();
    assert_eq!(
        contact_sheet(&entries, &batch),
        "CONTACT SHEETS — 2 rolls\n\
         Tri-X (400) — D76 9min, 36 frames\n\
         FP4+ (125) — D76 9min, 24 frames\n\
         batch: D76 1+9 (500ml)"
    );
}

#[test]
fn contact_sheet_for_a_quiet_week() {
    let batch = Batch { developer: Developer::Xtol, stock_ml: 100, water_ml: 400 };
    assert_eq!(
        contact_sheet(&[], &batch),
        "CONTACT SHEETS — 0 rolls\nbatch: Xtol 1+4 (500ml)"
    );
}
