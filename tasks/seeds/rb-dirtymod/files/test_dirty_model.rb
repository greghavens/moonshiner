require "minitest/autorun"
require_relative "dirty_model"

# Acceptance tests for DirtyModel — dirty-tracked records for the museum
# collection system. Run: ruby test_dirty_model.rb

class ArtifactRecord < DirtyModel
  attributes :title, :era, :insured_value, :on_display
end

class LoanRecord < DirtyModel
  attributes :borrower, :due_on
end

class DirtyModelTest < Minitest::Test
  def bell
    ArtifactRecord.new(title: "Bronze Bell", era: "Ming", insured_value: 12_000, on_display: true)
  end

  # -- generated accessors ---------------------------------------------------

  def test_readers_return_construction_values
    rec = bell
    assert_equal "Bronze Bell", rec.title
    assert_equal "Ming", rec.era
    assert_equal 12_000, rec.insured_value
    assert_equal true, rec.on_display
  end

  def test_writers_update_the_reader
    rec = bell
    rec.title = "Great Bronze Bell"
    assert_equal "Great Bronze Bell", rec.title
  end

  def test_attribute_not_given_at_construction_reads_as_nil
    rec = ArtifactRecord.new(title: "Jade Comb")
    assert_nil rec.era
    assert_nil rec.insured_value
  end

  def test_construction_rejects_undeclared_attributes
    err = assert_raises(ArgumentError) { ArtifactRecord.new(title: "Vase", weight_kg: 3) }
    assert_equal "unknown attribute :weight_kg", err.message
  end

  # -- respond_to? / method() must tell the truth ----------------------------

  def test_respond_to_is_true_for_declared_readers_and_writers
    rec = bell
    assert_respond_to rec, :title
    assert_respond_to rec, :title=
    assert_respond_to rec, :insured_value
    assert_respond_to rec, :insured_value=
  end

  def test_respond_to_is_false_for_undeclared_names
    rec = bell
    refute_respond_to rec, :weight_kg
    refute_respond_to rec, :weight_kg=
    refute_respond_to rec, :borrower, "another model's attributes must not bleed in"
  end

  def test_method_objects_are_retrievable_and_callable
    rec = bell
    reader = rec.method(:era)
    assert_equal "Ming", reader.call
    writer = rec.method(:era=)
    writer.call("Qing")
    assert_equal "Qing", rec.era
  end

  def test_undeclared_reader_and_writer_raise_no_method_error
    rec = bell
    err = assert_raises(NoMethodError) { rec.weight_kg }
    assert_includes err.message, "weight_kg"
    assert_raises(NoMethodError) { rec.weight_kg = 3 }
  end

  # -- dirty tracking --------------------------------------------------------

  def test_fresh_record_is_clean
    rec = bell
    refute_predicate rec, :changed?
    assert_equal({}, rec.changes)
  end

  def test_assignment_marks_the_record_changed_with_old_and_new
    rec = bell
    rec.era = "Qing"
    assert_predicate rec, :changed?
    assert_equal({ era: ["Ming", "Qing"] }, rec.changes)
  end

  def test_changes_track_first_old_value_and_latest_new_value
    rec = bell
    rec.insured_value = 15_000
    rec.insured_value = 18_000
    assert_equal({ insured_value: [12_000, 18_000] }, rec.changes)
  end

  def test_assigning_the_original_value_back_clears_that_change
    rec = bell
    rec.title = "Iron Bell"
    rec.era = "Qing"
    rec.title = "Bronze Bell"
    assert_equal({ era: ["Ming", "Qing"] }, rec.changes)
    rec.era = "Ming"
    refute_predicate rec, :changed?
    assert_equal({}, rec.changes)
  end

  def test_assigning_the_current_value_is_not_a_change
    rec = bell
    rec.on_display = true
    refute_predicate rec, :changed?
    assert_equal({}, rec.changes)
  end

  def test_changes_from_nil_are_tracked
    rec = ArtifactRecord.new(title: "Jade Comb")
    rec.era = "Han"
    assert_equal({ era: [nil, "Han"] }, rec.changes)
  end

  def test_changes_lists_attributes_in_declaration_order
    rec = bell
    rec.on_display = false
    rec.era = "Qing"
    rec.title = "Great Bell"
    assert_equal %i[title era on_display], rec.changes.keys
  end

  def test_changes_returns_a_defensive_copy
    rec = bell
    rec.era = "Qing"
    snapshot = rec.changes
    snapshot[:era][1] = "CORRUPTED"
    snapshot[:bogus] = [1, 2]
    assert_equal({ era: ["Ming", "Qing"] }, rec.changes)
  end

  # -- rollback! --------------------------------------------------------------

  def test_rollback_restores_every_original_value_and_returns_self
    rec = bell
    rec.title = "Great Bell"
    rec.insured_value = 99_000
    assert_same rec, rec.rollback!
    assert_equal "Bronze Bell", rec.title
    assert_equal 12_000, rec.insured_value
    refute_predicate rec, :changed?
    assert_equal({}, rec.changes)
  end

  def test_tracking_still_works_after_rollback
    rec = bell
    rec.era = "Qing"
    rec.rollback!
    rec.era = "Song"
    assert_equal({ era: ["Ming", "Song"] }, rec.changes)
  end

  def test_rollback_on_a_clean_record_is_a_quiet_no_op
    rec = bell
    assert_same rec, rec.rollback!
    assert_equal "Bronze Bell", rec.title
    refute_predicate rec, :changed?
  end

  # -- models and instances stay independent ---------------------------------

  def test_each_model_class_has_its_own_attribute_list
    loan = LoanRecord.new(borrower: "City Library", due_on: "2026-09-01")
    assert_equal "City Library", loan.borrower
    assert_respond_to loan, :due_on=
    refute_respond_to loan, :title
    assert_raises(NoMethodError) { loan.era }
  end

  def test_instances_do_not_share_dirty_state
    a = bell
    b = bell
    a.era = "Qing"
    refute_predicate b, :changed?
    assert_equal({}, b.changes)
    assert_equal "Ming", b.era
  end
end
