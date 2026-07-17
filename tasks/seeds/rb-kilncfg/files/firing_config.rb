require "json"

# Firing profile for the studio's kiln controller. The panel edits one
# profile in memory; between sessions it lives as a JSON file next to the
# controller state. All programmatic access uses symbol keys.
class FiringConfig
  DEFAULTS = {
    profile_name: "bisque-04",
    ramp_rate: 150,      # degrees F per hour
    target_temp: 1945,   # cone 04
    hold_minutes: 10,
    quiet_hours: { start: "22:00", stop: "06:00" } # no alarm horn in this window
  }.freeze

  def initialize(overrides = {})
    @settings = {}
    overrides.each { |key, value| set(key, value) }
  end

  # Panel dial writes land here.
  def set(key, value)
    raise ArgumentError, "unknown setting #{key.inspect}" unless DEFAULTS.key?(key)

    @settings[key] = value
    self
  end

  def profile_name = lookup(:profile_name)
  def ramp_rate    = lookup(:ramp_rate)
  def target_temp  = lookup(:target_temp)
  def hold_minutes = lookup(:hold_minutes)

  def quiet_start = lookup(:quiet_hours)[:start]
  def quiet_stop  = lookup(:quiet_hours)[:stop]

  # Effective settings: factory values overlaid with this profile's tweaks.
  def to_h
    DEFAULTS.merge(@settings)
  end

  def save(path)
    File.write(path, JSON.generate(@settings))
    path
  end

  def self.load(path)
    config = new
    config.restore(JSON.parse(File.read(path)))
    config
  end

  # Used by load and by the controller's crash-recovery path.
  def restore(data)
    @settings = data
    self
  end

  private

  def lookup(key)
    @settings.key?(key) ? @settings[key] : DEFAULTS[key]
  end
end
