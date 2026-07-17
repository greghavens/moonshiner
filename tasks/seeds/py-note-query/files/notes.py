"""In-memory note store with tag lookups, backing the desk-notes CLI."""


def normalize_tag(tag):
    """Tags are case-insensitive slugs: letters, digits, '-' and '_'."""
    t = tag.strip().lower()
    if not t or not all(c.isalnum() or c in "-_" for c in t):
        raise ValueError("bad tag: %r" % (tag,))
    return t


class Note:
    def __init__(self, note_id, title, tags):
        self.id = note_id
        self.title = title
        self.tags = frozenset(tags)

    def __repr__(self):
        return "Note(%d, %r)" % (self.id, self.title)


class NoteStore:
    def __init__(self):
        self._notes = {}
        self._next_id = 1

    def add(self, title, tags=()):
        """Store a note; returns its numeric id (ids increase, never reused)."""
        title = title.strip()
        if not title:
            raise ValueError("title required")
        note = Note(self._next_id, title, {normalize_tag(t) for t in tags})
        self._notes[note.id] = note
        self._next_id += 1
        return note.id

    def get(self, note_id):
        return self._notes[note_id]

    def remove(self, note_id):
        del self._notes[note_id]

    def __len__(self):
        return len(self._notes)

    def all_tags(self):
        """Every tag in use, sorted, without duplicates."""
        tags = set()
        for note in self._notes.values():
            tags |= note.tags
        return sorted(tags)

    def find_any(self, *tags):
        """Notes carrying at least one of the tags, ordered by id."""
        wanted = {normalize_tag(t) for t in tags}
        return [n for n in self._by_id() if n.tags & wanted]

    def find_all(self, *tags):
        """Notes carrying every one of the tags, ordered by id."""
        wanted = {normalize_tag(t) for t in tags}
        return [n for n in self._by_id() if wanted <= n.tags]

    def _by_id(self):
        return [self._notes[k] for k in sorted(self._notes)]
