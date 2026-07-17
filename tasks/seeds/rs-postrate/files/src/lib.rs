//! Counter rates for the mail-room postage meter. The numbers mirror
//! the laminated rate card next to the scale: a base rate per mail
//! class, plus a distance fee banded by what the scale reads.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Class {
    Letter,
    Flat,
    Parcel,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Zone {
    Local,
    Regional,
    National,
}

/// Base counter rate in cents by mail class, straight off the card.
pub fn base_rate(class: Class) -> u32 {
    match class {
        Class::Letter => 120
        Class::Flat => 260,
        Class::Parcel => 540,
    }
}

/// Distance fee in cents. Local runs add 5 per full 500 g on the
/// scale; regional and national add 10 and 15 per full 250 g. The
/// card caps any distance fee at 900 cents.
pub fn zone_fee(zone: Zone, weight_g: u32) -> u32 {
    let fee = match zone {
        Zone::Local => {
            let bands = weight_g / 500;
            30 + bands * 5;
        }
        Zone::Regional => 80 + (weight_g / 250) * 10,
        Zone::National => 140 + (weight_g / 250) * 15,
    };
    fee.min(900)
}

/// Meter total in cents for one piece.
pub fn quote(class: Class, zone: Zone, weight_g: u32) -> u32 {
    base_rate(class) + zone_fee(zone, weight_g)
}

/// The line the meter prints on the day roll.
pub fn roll_line(class: Class, zone: Zone, weight_g: u32) -> String {
    format!(
        "{:?}/{:?} {}g = {}c",
        class,
        zone,
        weight_g,
        quote(class, zone, weight_g)
    )
}
