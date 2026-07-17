require "minitest/autorun"
require "fileutils"
require "json"
require_relative "firing_config"

# Contract tests for the kiln controller's firing profile: panel edits,
# persistence across controller restarts (save -> load), effective settings.
class FiringConfigTest < Minitest::Test
  SCRATCH = File.join(Dir.pwd, "kiln_scratch")

  def setup
    FileUtils.mkdir_p(SCRATCH)
    @path = File.join(SCRATCH, "evening-glaze.json")
  end

  def teardown
    FileUtils.rm_rf(SCRATCH)
  end

  def test_factory_defaults_apply_out_of_the_box
    config = FiringConfig.new
    assert_equal "bisque-04", config.profile_name
    assert_equal 150, config.ramp_rate
    assert_equal 1945, config.target_temp
    assert_equal 10, config.hold_minutes
    assert_equal "22:00", config.quiet_start
    assert_equal "06:00", config.quiet_stop
  end

  def test_panel_tweaks_take_effect_immediately
    config = FiringConfig.new(ramp_rate: 120, target_temp: 2232)
    assert_equal 120, config.ramp_rate
    assert_equal 2232, config.target_temp
    config.set(:hold_minutes, 25)
    assert_equal 25, config.hold_minutes
    config.set(:quiet_hours, { start: "21:00", stop: "05:30" })
    assert_equal "21:00", config.quiet_start
  end

  def test_unknown_settings_are_rejected
    err = assert_raises(ArgumentError) { FiringConfig.new.set(:rampratee, 100) }
    assert_equal "unknown setting :rampratee", err.message
  end

  def test_save_writes_the_profile_as_json
    FiringConfig.new(ramp_rate: 120).save(@path)
    raw = JSON.parse(File.read(@path))
    assert_equal 120, raw["ramp_rate"]
  end

  def test_a_saved_profile_survives_a_controller_restart
    config = FiringConfig.new(profile_name: "evening-glaze", ramp_rate: 120, target_temp: 2232)
    config.save(@path)
    back = FiringConfig.load(@path)
    assert_equal 120, back.ramp_rate
    assert_equal 2232, back.target_temp
    assert_equal "evening-glaze", back.profile_name
    assert_equal 10, back.hold_minutes
  end

  def test_quiet_hours_survive_a_controller_restart
    config = FiringConfig.new(quiet_hours: { start: "21:00", stop: "05:30" })
    config.save(@path)
    back = FiringConfig.load(@path)
    assert_equal "21:00", back.quiet_start
    assert_equal "05:30", back.quiet_stop
  end

  def test_a_tweak_after_a_restart_keeps_the_earlier_tweaks
    FiringConfig.new(ramp_rate: 120, target_temp: 2232).save(@path)
    session_two = FiringConfig.load(@path)
    session_two.set(:hold_minutes, 25)
    session_two.save(@path)
    session_three = FiringConfig.load(@path)
    assert_equal 25, session_three.hold_minutes
    assert_equal 120, session_three.ramp_rate
    assert_equal 2232, session_three.target_temp
  end

  def test_effective_settings_list_each_setting_exactly_once_after_a_restart
    FiringConfig.new(ramp_rate: 120).save(@path)
    effective = FiringConfig.load(@path).to_h
    assert_equal 5, effective.size
    assert_equal FiringConfig::DEFAULTS.keys.sort, effective.keys.sort
    assert_equal 120, effective[:ramp_rate]
    assert_equal 1945, effective[:target_temp]
  end
end
