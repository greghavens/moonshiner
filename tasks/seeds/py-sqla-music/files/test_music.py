"""Contract tests for the music-library ORM layer — protected file."""
import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from music import (
    Album,
    Artist,
    Playlist,
    PlaylistEntry,
    Track,
    add_album,
    album_runtimes,
    init_db,
    make_engine,
    playlist_titles,
    tracks_longer_than,
)


@pytest.fixture()
def engine():
    eng = make_engine()
    init_db(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s


def seed_library(session):
    add_album(session, "Ada Circuit", "First Light", 2019,
              [("Sunrise", 210), ("Coffee", 185), ("Compile", 240)])
    add_album(session, "Ada Circuit", "Night Build", 2022,
              [("Late Bus", 300), ("Neon", 195)])
    add_album(session, "The Loop Section", "Warm Start", 2021,
              [("Boot", 150), ("Kernel", 265), ("Cache Line", 205)])
    session.commit()


def add_playlist(session, name, ordered_titles):
    """Insert entries in reverse so ordering must come from position."""
    playlist = Playlist(name=name)
    session.add(playlist)
    numbered = list(enumerate(ordered_titles, start=1))
    for position, title in reversed(numbered):
        track = session.scalars(select(Track).where(Track.title == title)).one()
        playlist.entries.append(PlaylistEntry(track=track, position=position))
    session.commit()


def test_init_db_creates_expected_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert {"artists", "albums", "tracks",
            "playlists", "playlist_entries"} <= tables


def test_add_album_builds_tracks_in_order(session):
    album = add_album(session, "Ada Circuit", "First Light", 2019,
                      [("Sunrise", 210), ("Coffee", 185), ("Compile", 240)])
    session.commit()
    assert album.artist.name == "Ada Circuit"
    assert [(t.title, t.seconds, t.position) for t in album.tracks] == [
        ("Sunrise", 210, 1), ("Coffee", 185, 2), ("Compile", 240, 3)]


def test_add_album_reuses_existing_artist(session):
    seed_library(session)
    assert session.scalar(select(func.count()).select_from(Artist)) == 2


def test_add_album_does_not_commit(engine):
    with Session(engine) as s:
        add_album(s, "Ada Circuit", "First Light", 2019, [("Sunrise", 210)])
        s.rollback()
    with Session(engine) as s2:
        assert s2.scalar(select(func.count()).select_from(Album)) == 0
        assert s2.scalar(select(func.count()).select_from(Artist)) == 0


def test_album_runtimes_pinned_values(session):
    seed_library(session)
    assert album_runtimes(session, "Ada Circuit") == [
        ("First Light", 635), ("Night Build", 495)]


def test_album_runtimes_other_artist(session):
    seed_library(session)
    assert album_runtimes(session, "The Loop Section") == [("Warm Start", 620)]


def test_album_runtimes_unknown_artist_is_empty(session):
    seed_library(session)
    assert album_runtimes(session, "Nobody Here") == []


def test_tracks_longer_than_pinned_order(session):
    seed_library(session)
    assert tracks_longer_than(session, 200) == [
        ("Ada Circuit", "Night Build", "Late Bus"),
        ("The Loop Section", "Warm Start", "Kernel"),
        ("Ada Circuit", "First Light", "Compile"),
        ("Ada Circuit", "First Light", "Sunrise"),
        ("The Loop Section", "Warm Start", "Cache Line"),
    ]


def test_tracks_longer_than_is_strictly_greater(session):
    seed_library(session)
    result = tracks_longer_than(session, 205)
    titles = [row[2] for row in result]
    assert "Cache Line" not in titles          # exactly 205 is excluded
    assert len(result) == 4


def test_playlist_titles_ordered_by_position(session):
    seed_library(session)
    add_playlist(session, "Focus", ["Kernel", "Sunrise", "Late Bus"])
    assert playlist_titles(session, "Focus") == [
        "Kernel", "Sunrise", "Late Bus"]


def test_playlist_titles_unknown_playlist_is_empty(session):
    seed_library(session)
    assert playlist_titles(session, "Gym") == []


def test_deleting_album_cascades_to_tracks(session):
    seed_library(session)
    album = session.scalars(
        select(Album).where(Album.title == "Night Build")).one()
    session.delete(album)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Track)) == 6
    assert session.scalars(
        select(Track).where(Track.title == "Neon")).first() is None


def test_artist_names_are_unique(session):
    seed_library(session)
    session.add(Artist(name="Ada Circuit"))
    with pytest.raises(IntegrityError):
        session.commit()
