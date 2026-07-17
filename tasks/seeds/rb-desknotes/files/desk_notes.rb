# Shared notebook for the support desk: short notes agents tag and pull up
# mid-ticket ("VPN reset steps", "billing escalation path"). Backing store
# is in-memory; the sync layer lives elsewhere.
class NoteStore
  Note = Struct.new(:id, :title, :body, :tags)

  def initialize
    @notes = []
    @next_id = 1
  end

  # Adds a note and returns it. Tags are normalized to unique, sorted
  # strings so queries never care how an agent typed them.
  def add(title, body = "", tags: [])
    note = Note.new(@next_id, title, body, normalize(tags))
    @next_id += 1
    @notes << note
    note
  end

  def find(id)
    @notes.find { |note| note.id == id }
  end

  # Every note, oldest first.
  def notes
    @notes.dup
  end

  # Notes carrying the tag, oldest first.
  def tagged(tag)
    @notes.select { |note| note.tags.include?(tag.to_s) }
  end

  # Every tag in use, sorted, no duplicates.
  def tags
    @notes.flat_map(&:tags).uniq.sort
  end

  private

  def normalize(tags)
    tags.map(&:to_s).uniq.sort
  end
end
