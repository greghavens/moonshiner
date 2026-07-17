require "minitest/autorun"
require_relative "desk_notes"

# Contract tests for the support-desk notebook. The first block pins the
# behavior agents already rely on; the rest covers the sprint's three
# features: archiving, tag rename with merge, and fuzzy title search.
class DeskNotesTest < Minitest::Test
  # -- existing behavior (must keep working) -----------------------------

  def test_add_assigns_sequential_ids
    store = NoteStore.new
    first = store.add("VPN reset steps", "kb-101")
    second = store.add("Printer jam checklist", "kb-104")
    assert_equal 1, first.id
    assert_equal 2, second.id
    assert_equal "VPN reset steps", first.title
    assert_equal "kb-101", first.body
  end

  def test_tags_are_normalized_on_add
    store = NoteStore.new
    note = store.add("VPN reset steps", tags: [:vpn, "billing", "vpn"])
    assert_equal %w[billing vpn], note.tags
  end

  def test_notes_lists_in_insertion_order
    store = NoteStore.new
    store.add("A")
    store.add("B")
    store.add("C")
    assert_equal [1, 2, 3], store.notes.map(&:id)
  end

  def test_find_returns_the_note_or_nil
    store = NoteStore.new
    store.add("VPN reset steps")
    assert_equal "VPN reset steps", store.find(1).title
    assert_nil store.find(99)
  end

  def test_tagged_filters_by_tag_in_insertion_order
    store = NoteStore.new
    store.add("A", tags: ["vpn"])
    store.add("B", tags: ["billing"])
    store.add("C", tags: ["vpn", "urgent"])
    assert_equal [1, 3], store.tagged(:vpn).map(&:id)
    assert_equal [], store.tagged("nope")
  end

  def test_all_tags_are_sorted_and_unique
    store = NoteStore.new
    store.add("A", tags: %w[vpn urgent])
    store.add("B", tags: %w[billing vpn])
    assert_equal %w[billing urgent vpn], store.tags
  end

  # -- feature: archiving -------------------------------------------------

  def test_archive_hides_a_note_from_the_default_listing
    store = NoteStore.new
    store.add("Old macro")
    store.add("Current macro")
    store.archive(1)
    assert_equal [2], store.notes.map(&:id)
    assert store.archived?(1)
    refute store.archived?(2)
  end

  def test_archived_notes_are_listed_when_asked
    store = NoteStore.new
    store.add("Old macro")
    store.add("Current macro")
    store.archive(1)
    assert_equal [1, 2], store.notes(include_archived: true).map(&:id)
  end

  def test_archived_notes_drop_out_of_tag_queries
    store = NoteStore.new
    store.add("Old macro", tags: ["vpn"])
    store.add("Current macro", tags: ["vpn"])
    store.archive(1)
    assert_equal [2], store.tagged("vpn").map(&:id)
    assert_equal [1, 2], store.tagged("vpn", include_archived: true).map(&:id)
  end

  def test_find_still_returns_an_archived_note
    store = NoteStore.new
    store.add("Old macro")
    store.archive(1)
    assert_equal "Old macro", store.find(1).title
  end

  def test_unarchive_restores_a_note
    store = NoteStore.new
    store.add("Old macro")
    store.archive(1)
    store.unarchive(1)
    assert_equal [1], store.notes.map(&:id)
    refute store.archived?(1)
  end

  def test_tags_reflect_only_visible_notes_by_default
    store = NoteStore.new
    store.add("Old macro", tags: ["legacy"])
    store.add("Current macro", tags: ["billing"])
    store.archive(1)
    assert_equal %w[billing], store.tags
    assert_equal %w[billing legacy], store.tags(include_archived: true)
  end

  def test_archiving_an_unknown_note_is_an_error
    store = NoteStore.new
    store.add("Only note")
    err = assert_raises(ArgumentError) { store.archive(99) }
    assert_equal "no such note: 99", err.message
    err = assert_raises(ArgumentError) { store.unarchive(99) }
    assert_equal "no such note: 99", err.message
  end

  # -- feature: tag rename with merge --------------------------------------

  def test_renaming_a_tag_retags_every_note
    store = NoteStore.new
    store.add("A", tags: %w[urgent vpn])
    store.add("B", tags: %w[urgent])
    store.add("C", tags: %w[billing])
    assert_equal 2, store.rename_tag("urgent", "priority")
    assert_equal [1, 2], store.tagged("priority").map(&:id)
    assert_equal [], store.tagged("urgent")
    assert_equal %w[billing priority vpn], store.tags
  end

  def test_renaming_onto_an_existing_tag_merges_without_duplicates
    store = NoteStore.new
    store.add("A", tags: %w[billing escalated])
    store.add("B", tags: %w[billing])
    store.add("C", tags: %w[escalated])
    assert_equal 2, store.rename_tag("billing", "escalated")
    assert_equal %w[escalated], store.find(1).tags
    assert_equal %w[escalated], store.find(2).tags
    assert_equal [1, 2, 3], store.tagged("escalated").map(&:id)
    assert_equal %w[escalated], store.tags
  end

  def test_rename_keeps_tag_lists_sorted
    store = NoteStore.new
    store.add("A", tags: %w[billing urgent])
    store.rename_tag("urgent", "aaa-hotlist")
    assert_equal %w[aaa-hotlist billing], store.find(1).tags
  end

  def test_rename_reaches_archived_notes
    store = NoteStore.new
    store.add("Old macro", tags: %w[urgent])
    store.add("Current macro", tags: %w[urgent])
    store.archive(1)
    assert_equal 2, store.rename_tag("urgent", "priority")
    assert_equal [1, 2], store.tagged("priority", include_archived: true).map(&:id)
  end

  def test_renaming_an_unknown_tag_is_an_error
    store = NoteStore.new
    store.add("A", tags: %w[billing])
    err = assert_raises(ArgumentError) { store.rename_tag("zzz", "anything") }
    assert_equal 'unknown tag "zzz"', err.message
  end

  def test_renaming_a_tag_onto_itself_changes_nothing
    store = NoteStore.new
    store.add("A", tags: %w[urgent])
    assert_equal 0, store.rename_tag("urgent", "urgent")
    assert_equal %w[urgent], store.find(1).tags
  end

  # -- feature: fuzzy title search ------------------------------------------

  def search_store
    store = NoteStore.new
    store.add("VPN reset steps", "kb-101", tags: ["network"])
    store.add("Vendor payment escalation", "kb-102", tags: ["billing"])
    store.add("The VPN appliance runbook", "kb-103", tags: ["network"])
    store.add("Printer jam checklist", "kb-104", tags: ["hardware"])
    store.add("Printer jam checklist", "kb-105", tags: %w[hardware legacy])
    store
  end

  def test_search_matches_subsequences_and_ranks_tight_matches_first
    assert_equal [1, 3, 2], search_store.search("vpn").map(&:id)
  end

  def test_search_is_case_insensitive
    assert_equal [1, 3, 2], search_store.search("VPN").map(&:id)
  end

  def test_search_breaks_span_ties_by_start_then_id
    assert_equal [4, 5], search_store.search("prin").map(&:id)
  end

  def test_search_with_empty_pattern_lists_visible_notes
    store = search_store
    assert_equal [1, 2, 3, 4, 5], store.search("").map(&:id)
    store.archive(4)
    assert_equal [1, 2, 3, 5], store.search("").map(&:id)
  end

  def test_search_skips_archived_notes_by_default
    store = search_store
    store.archive(1)
    assert_equal [3, 2], store.search("vpn").map(&:id)
    assert_equal [1, 3, 2], store.search("vpn", include_archived: true).map(&:id)
  end

  def test_search_returns_nothing_when_no_title_matches
    assert_equal [], search_store.search("qzx")
  end
end
