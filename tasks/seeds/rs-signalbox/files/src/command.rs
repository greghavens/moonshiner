/// What the box operator asked for, parsed from a console keyword.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    SetRoute,
    ClearRoute,
    ReleaseLock,
    Unknown,
}

/// Console keywords are entered lowercase by convention; anything the
/// console does not recognise is echoed back for the operator to retype.
pub fn classify(keyword: &str) -> Action {
    match keyword {
        "set" => Action::SetRoute,
        "clear" => Action::ClearRoute,
        _ => Action::Unknown,
        "release" => Action::ReleaseLock,
    }
}
