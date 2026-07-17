require "minitest/autorun"
require_relative "export_job"

# Render-option handling for the statement-export worker. The worker is a
# long-lived singleton process, so these scenarios run in a fixed order to
# keep the log fixtures reproducible in CI.
class ExportJobTest < Minitest::Test
  def self.run_order
    :alpha
  end

  def test_configure_rejects_unknown_options
    err = assert_raises(ArgumentError) { ExportJob.new("S-1003", papersize: "A4") }
    assert_equal "unknown option :papersize", err.message
  end

  def test_constructor_overrides_apply_to_that_job
    job = ExportJob.new("S-1002", page_size: "A4", copies: 2)
    assert_equal "A4", job.options[:page_size]
    assert_equal 2, job.options[:copies]
    assert_equal "portrait", job.options[:orientation]
  end

  def test_copies_and_page_size_overrides_do_not_touch_later_jobs
    ExportJob.new("S-1004", copies: 5, orientation: "landscape")
    fresh = ExportJob.new("S-1005")
    assert_equal 1, fresh.options[:copies]
    assert_equal "portrait", fresh.options[:orientation]
  end

  def test_defaults_are_in_effect_on_a_fresh_job
    job = ExportJob.new("S-1001")
    assert_equal "Letter", job.options[:page_size]
    assert_equal "portrait", job.options[:orientation]
    assert_equal 1, job.options[:copies]
    assert_equal [], job.options[:stamp_lines]
    assert_equal({ emails: [], attach_csv: false }, job.options[:delivery])
  end

  def test_delivery_ccs_do_not_carry_over_to_later_jobs
    ExportJob.new("S-2003").deliver_to("ops@ledgerline.test")
    fresh = ExportJob.new("S-2004")
    assert_equal [], fresh.options[:delivery][:emails]
  end

  def test_factory_constants_survive_a_burst_of_jobs
    ExportJob.new("S-4001", copies: 3).add_stamp("void").deliver_to("audit@ledgerline.test")
    ExportJob.new("S-4002").attach_csv!
    assert_equal [], ExportJob::DEFAULTS[:stamp_lines]
    assert_equal({ emails: [], attach_csv: false }, ExportJob::DEFAULTS[:delivery])
    assert_equal 1, ExportJob::DEFAULTS[:copies]
  end

  def test_manifest_reflects_only_its_own_job
    ExportJob.new("S-5001").add_stamp("draft").deliver_to("qa@ledgerline.test")
    job = ExportJob.new("S-5002", page_size: "A4").attach_csv!
    assert_equal "S-5002 A4/portrait x1 stamps=- cc=- csv", job.manifest
  end

  def test_stamps_do_not_carry_over_to_later_jobs
    ExportJob.new("S-2001").add_stamp("draft")
    fresh = ExportJob.new("S-2002")
    assert_equal [], fresh.options[:stamp_lines]
  end

  def test_the_csv_attachment_flag_does_not_carry_over
    ExportJob.new("S-2005").attach_csv!
    fresh = ExportJob.new("S-2006")
    refute fresh.options[:delivery][:attach_csv], "a fresh job must not bundle a CSV"
  end

  def test_two_live_jobs_keep_their_stamps_apart
    draft = ExportJob.new("S-3001").add_stamp("draft")
    final = ExportJob.new("S-3002").add_stamp("paid")
    assert_equal ["DRAFT"], draft.options[:stamp_lines]
    assert_equal ["PAID"], final.options[:stamp_lines]
  end
end
