use rs_seatmap::{Hall, Seat};

#[test]
fn standard_layout_shape() {
    let hall = Hall::standard();
    assert_eq!(hall.rows(), 4);
    assert_eq!(hall.open_in_row(0), 10);
    assert_eq!(hall.open_in_row(1), 12);
    assert_eq!(hall.open_in_row(2), 12);
    assert_eq!(hall.open_in_row(3), 14);
    assert_eq!(hall.seat(0, 0), Some(Seat::Open));
    assert_eq!(hall.seat(3, 13), Some(Seat::Open));
    assert_eq!(hall.seat(3, 14), None);
    assert_eq!(hall.seat(4, 0), None);
}

#[test]
fn custom_layout_shape() {
    let hall = Hall::with_layout(&[3, 5]);
    assert_eq!(hall.rows(), 2);
    assert_eq!(hall.open_in_row(0), 3);
    assert_eq!(hall.open_in_row(1), 5);
    assert_eq!(hall.open_in_row(2), 0);
}

#[test]
fn holds_only_land_on_open_seats() {
    let mut hall = Hall::with_layout(&[2, 2]);
    assert!(hall.hold(0, 1));
    assert_eq!(hall.seat(0, 1), Some(Seat::Held));
    assert!(!hall.hold(0, 1), "re-holding a held seat");
    assert!(!hall.hold(5, 0), "holding a seat that does not exist");
    assert_eq!(hall.open_in_row(0), 1);
}

#[test]
fn selling_covers_open_and_held_but_never_twice() {
    let mut hall = Hall::with_layout(&[3]);
    assert!(hall.hold(0, 0));
    assert!(hall.sell(0, 0), "selling a held seat");
    assert!(hall.sell(0, 1), "selling an open seat directly");
    assert!(!hall.sell(0, 0), "selling a sold seat again");
    assert_eq!(hall.seat(0, 0), Some(Seat::Sold));
    assert_eq!(hall.seat(0, 1), Some(Seat::Sold));
    assert_eq!(hall.open_in_row(0), 1);
}

#[test]
fn best_row_is_front_most_with_space() {
    let mut hall = Hall::standard();
    assert_eq!(hall.best_row(4), Some(0));
    for seat in 0..8 {
        assert!(hall.sell(0, seat));
    }
    assert_eq!(hall.best_row(4), Some(1), "front row too full for four");
    assert_eq!(hall.best_row(2), Some(0), "front row still fits a pair");
    assert_eq!(hall.best_row(15), None, "no row seats a party of fifteen");
}
