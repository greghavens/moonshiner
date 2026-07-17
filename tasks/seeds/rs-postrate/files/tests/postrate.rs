use rs_postrate::{base_rate, quote, roll_line, zone_fee, Class, Zone};

#[test]
fn base_rates_match_the_card() {
    assert_eq!(base_rate(Class::Letter), 120);
    assert_eq!(base_rate(Class::Flat), 260);
    assert_eq!(base_rate(Class::Parcel), 540);
}

#[test]
fn local_fee_bands_per_full_500g() {
    assert_eq!(zone_fee(Zone::Local, 0), 30);
    assert_eq!(zone_fee(Zone::Local, 499), 30);
    assert_eq!(zone_fee(Zone::Local, 500), 35);
    assert_eq!(zone_fee(Zone::Local, 1999), 45);
    assert_eq!(zone_fee(Zone::Local, 2000), 50);
}

#[test]
fn regional_and_national_band_per_full_250g() {
    assert_eq!(zone_fee(Zone::Regional, 0), 80);
    assert_eq!(zone_fee(Zone::Regional, 249), 80);
    assert_eq!(zone_fee(Zone::Regional, 250), 90);
    assert_eq!(zone_fee(Zone::Regional, 1000), 120);
    assert_eq!(zone_fee(Zone::National, 240), 140);
    assert_eq!(zone_fee(Zone::National, 750), 185);
}

#[test]
fn distance_fees_cap_at_the_card_max() {
    assert_eq!(zone_fee(Zone::National, 13000), 900);
    assert_eq!(zone_fee(Zone::Regional, 25000), 900);
}

#[test]
fn quotes_stack_base_and_zone() {
    assert_eq!(quote(Class::Letter, Zone::Local, 120), 150);
    assert_eq!(quote(Class::Flat, Zone::Regional, 510), 360);
    assert_eq!(quote(Class::Parcel, Zone::National, 1300), 755);
}

#[test]
fn roll_lines_print_the_meter_total() {
    assert_eq!(roll_line(Class::Letter, Zone::Local, 120), "Letter/Local 120g = 150c");
    assert_eq!(
        roll_line(Class::Parcel, Zone::National, 1300),
        "Parcel/National 1300g = 755c"
    );
}
