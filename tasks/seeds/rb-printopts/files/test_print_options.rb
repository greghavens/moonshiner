require "minitest/autorun"
require_relative "print_options"

# Acceptance tests for PrintOptions — job-option validation for the print
# farm's slicer submit endpoint. Run: ruby test_print_options.rb

class PrintOptionsTest < Minitest::Test
  def schema
    PrintOptions::Schema.new do
      required :filament, :string
      required :nozzle_temp, :integer
      optional :bed_temp, :integer, default: 60
      optional :supports, :boolean, default: false
      optional :infill_pct, :integer, default: 20
      optional :note, :string
    end
  end

  # -- happy path --------------------------------------------------------------

  def test_native_types_pass_through
    got = schema.validate(filament: "PLA", nozzle_temp: 215, bed_temp: 65,
                          supports: true, infill_pct: 35, note: "lattice bracket")
    assert_equal({ filament: "PLA", nozzle_temp: 215, bed_temp: 65,
                   supports: true, infill_pct: 35, note: "lattice bracket" }, got)
  end

  def test_missing_optionals_get_defaults_in_declaration_order
    got = schema.validate(filament: "PETG", nozzle_temp: 235)
    assert_equal({ filament: "PETG", nozzle_temp: 235, bed_temp: 60,
                   supports: false, infill_pct: 20 }, got)
    assert_equal %i[filament nozzle_temp bed_temp supports infill_pct], got.keys
  end

  def test_optional_without_default_is_absent_not_nil
    got = schema.validate(filament: "PLA", nozzle_temp: 215)
    refute got.key?(:note)
  end

  def test_each_validate_call_returns_a_fresh_hash
    s = schema
    a = s.validate(filament: "PLA", nozzle_temp: 215)
    b = s.validate(filament: "PLA", nozzle_temp: 215)
    assert_equal a, b
    refute_same a, b
  end

  def test_the_callers_hash_is_not_mutated
    opts = { filament: "PLA", nozzle_temp: "215" }
    schema.validate(**opts)
    assert_equal({ filament: "PLA", nozzle_temp: "215" }, opts)
  end

  # -- coercions ----------------------------------------------------------------

  def test_integer_strings_coerce
    got = schema.validate(filament: "PLA", nozzle_temp: "215", bed_temp: "-5", infill_pct: "007")
    assert_equal 215, got[:nozzle_temp]
    assert_equal(-5, got[:bed_temp])
    assert_equal 7, got[:infill_pct]
  end

  def test_boolean_strings_coerce
    got = schema.validate(filament: "PLA", nozzle_temp: 215, supports: "true")
    assert_equal true, got[:supports]
    got = schema.validate(filament: "PLA", nozzle_temp: 215, supports: "false")
    assert_equal false, got[:supports]
  end

  def test_explicit_false_survives_a_true_default
    s = PrintOptions::Schema.new { optional :heated_chamber, :boolean, default: true }
    assert_equal({ heated_chamber: false }, s.validate(heated_chamber: false))
    assert_equal({ heated_chamber: true }, s.validate)
  end

  def test_bad_values_report_pinned_messages
    err = assert_raises(PrintOptions::Error) { schema.validate(filament: "PLA", nozzle_temp: "21.5") }
    assert_equal ["nozzle_temp must be an integer, got \"21.5\""], err.problems

    err = assert_raises(PrintOptions::Error) { schema.validate(filament: "PLA", nozzle_temp: true) }
    assert_equal ["nozzle_temp must be an integer, got true"], err.problems

    err = assert_raises(PrintOptions::Error) { schema.validate(filament: "PLA", nozzle_temp: "") }
    assert_equal ["nozzle_temp must be an integer, got \"\""], err.problems

    err = assert_raises(PrintOptions::Error) { schema.validate(filament: 42, nozzle_temp: 215) }
    assert_equal ["filament must be a string, got 42"], err.problems

    err = assert_raises(PrintOptions::Error) { schema.validate(filament: "PLA", nozzle_temp: 215, supports: "yes") }
    assert_equal ["supports must be a boolean, got \"yes\""], err.problems
  end

  # -- unknown keys and did-you-mean ---------------------------------------------

  def test_unknown_key_suggests_the_nearest_declared_key
    err = assert_raises(PrintOptions::Error) do
      schema.validate(filament: "PLA", nozzle_temp: 215, nozle_temp: 210)
    end
    assert_equal ["unknown option :nozle_temp (did you mean :nozzle_temp?)"], err.problems
  end

  def test_unknown_key_with_nothing_close_gets_no_suggestion
    err = assert_raises(PrintOptions::Error) do
      schema.validate(filament: "PLA", nozzle_temp: 215, qqqqqq: 1)
    end
    assert_equal ["unknown option :qqqqqq"], err.problems
  end

  def test_suggestion_ties_break_toward_the_earlier_declaration
    raft_first = PrintOptions::Schema.new do
      optional :raft, :boolean, default: false
      optional :draft, :boolean, default: false
    end
    err = assert_raises(PrintOptions::Error) { raft_first.validate(craft: true) }
    assert_equal ["unknown option :craft (did you mean :raft?)"], err.problems

    draft_first = PrintOptions::Schema.new do
      optional :draft, :boolean, default: false
      optional :raft, :boolean, default: false
    end
    err = assert_raises(PrintOptions::Error) { draft_first.validate(craft: true) }
    assert_equal ["unknown option :craft (did you mean :draft?)"], err.problems
  end

  # -- errors accumulate: one raise carries everything ----------------------------

  def test_all_problems_come_back_in_one_raise_in_pinned_order
    err = assert_raises(PrintOptions::Error) do
      schema.validate(nozle_temp: "215", noote: "brim on", filament: 42, bed_temp: "warm")
    end
    assert_equal [
      "unknown option :nozle_temp (did you mean :nozzle_temp?)",
      "unknown option :noote (did you mean :note?)",
      "filament must be a string, got 42",
      "nozzle_temp is required",
      "bed_temp must be an integer, got \"warm\"",
    ], err.problems
    assert_equal "invalid print options:\n" \
                 "  - unknown option :nozle_temp (did you mean :nozzle_temp?)\n" \
                 "  - unknown option :noote (did you mean :note?)\n" \
                 "  - filament must be a string, got 42\n" \
                 "  - nozzle_temp is required\n" \
                 "  - bed_temp must be an integer, got \"warm\"", err.message
  end

  def test_missing_requireds_report_in_declaration_order
    err = assert_raises(PrintOptions::Error) { schema.validate }
    assert_equal ["filament is required", "nozzle_temp is required"], err.problems
  end

  def test_error_is_an_argument_error
    err = assert_raises(PrintOptions::Error) { schema.validate }
    assert_kind_of ArgumentError, err
  end

  # -- schema declaration mistakes fail fast at build time -------------------------

  def test_duplicate_declaration_raises
    err = assert_raises(ArgumentError) do
      PrintOptions::Schema.new do
        required :filament, :string
        optional :filament, :string
      end
    end
    assert_equal "duplicate option :filament", err.message
  end

  def test_unknown_type_raises
    err = assert_raises(ArgumentError) do
      PrintOptions::Schema.new { required :speed, :float }
    end
    assert_equal "unknown type :float for :speed", err.message
  end

  def test_required_with_a_default_raises
    err = assert_raises(ArgumentError) do
      PrintOptions::Schema.new { required :filament, :string, default: "PLA" }
    end
    assert_equal "required option :filament cannot have a default", err.message
  end
end
