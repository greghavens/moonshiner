"""Behavior checks for the memory-card import helper.

Run: python3 test_shoot_import.py
"""
from shoot_import import missing_frames, next_name, sequences, shoot_order, split_name


def test_split_name():
    assert split_name("IMG_4021.jpg") == ("IMG", "4021", "jpg")
    assert split_name("DSC_0098.NEF") == ("DSC", "0098", "nef")
    assert split_name("clip_7.mov") == ("clip", "7", "mov")
    try:
        split_name("notes.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("non-sequence names must be rejected")


def test_contact_sheet_order():
    card = ["IMG_998.jpg", "IMG_1000.jpg", "IMG_7.jpg", "IMG_999.jpg"]
    assert shoot_order(card) == [
        "IMG_7.jpg", "IMG_998.jpg", "IMG_999.jpg", "IMG_1000.jpg",
    ], shoot_order(card)


def test_sequences_grouped_in_frame_order():
    card = ["clip_10.mov", "IMG_9.jpg", "clip_9.mov", "IMG_10.jpg", "IMG_11.jpg"]
    groups = sequences(card)
    assert groups == {
        "IMG": ["IMG_9.jpg", "IMG_10.jpg", "IMG_11.jpg"],
        "clip": ["clip_9.mov", "clip_10.mov"],
    }, groups


def test_missing_frames_report():
    card = ["IMG_7.jpg", "IMG_8.jpg", "IMG_10.jpg", "IMG_11.jpg", "IMG_13.jpg"]
    assert missing_frames(card, "IMG") == [9, 12], missing_frames(card, "IMG")

    padded = ["DSC_0098.nef", "DSC_0099.nef", "DSC_0101.nef"]
    assert missing_frames(padded, "DSC") == [100], missing_frames(padded, "DSC")

    assert missing_frames(card, "DSC") == []


def test_next_name_continues_the_sequence():
    card = ["IMG_998.jpg", "IMG_999.jpg", "IMG_1000.jpg"]
    suggestion = next_name(card, "IMG", "jpg")
    assert suggestion not in card, f"suggested a name already on the card: {suggestion}"
    assert suggestion == "IMG_1001.jpg", suggestion


def test_next_name_padding():
    assert next_name(["DSC_0011.nef", "DSC_0012.nef"], "DSC", "nef") == "DSC_0013.nef"
    assert next_name(["clip_0999.mov"], "clip", "mov") == "clip_1000.mov"
    assert next_name([], "IMG", "jpg") == "IMG_0001.jpg"
    assert next_name(["DSC_0100.nef", "IMG_2.jpg"], "IMG", "jpg") == "IMG_3.jpg"


def main():
    test_split_name()
    test_contact_sheet_order()
    test_sequences_grouped_in_frame_order()
    test_missing_frames_report()
    test_next_name_continues_the_sequence()
    test_next_name_padding()
    print("all checks passed")


if __name__ == "__main__":
    main()
