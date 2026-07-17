# Acceptance harness for battleduel.rb: spawns the server in its own process
# group, parses the PORT line, drives it with scripted loopback TCP clients.
# Every read is bounded by a timeout; the whole group is killed in teardown.
# Run: ruby test_battleduel.rb
require "minitest/autorun"
require "socket"
require "rbconfig"

SERVER = File.expand_path("battleduel.rb", __dir__)
FIXTURES = File.expand_path("fixtures", __dir__)
IO_TIMEOUT = 5

class DuelClient
  attr_reader :sock

  def initialize(port)
    @sock = Socket.tcp("127.0.0.1", port, connect_timeout: IO_TIMEOUT)
    @buf = +""
  end

  def send_line(line)
    @sock.write("#{line}\n")
  end

  def recv_line
    loop do
      if (i = @buf.index("\n"))
        return @buf.slice!(0..i).chomp
      end
      unless @sock.wait_readable(IO_TIMEOUT)
        raise "timed out waiting for a line (buffered: #{@buf.inspect})"
      end
      chunk = @sock.read_nonblock(65_536, exception: false)
      raise EOFError, "connection closed early (buffered: #{@buf.inspect})" if chunk.nil?
      next if chunk == :wait_readable
      @buf << chunk
    end
  end

  def eof?
    return false unless @buf.empty?
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + IO_TIMEOUT
    loop do
      return false unless @buf.empty?
      remaining = deadline - Process.clock_gettime(Process::CLOCK_MONOTONIC)
      return false if remaining <= 0 || !@sock.wait_readable(remaining)
      chunk = @sock.read_nonblock(65_536, exception: false)
      return true if chunk.nil?
      @buf << chunk unless chunk == :wait_readable
      return false unless @buf.empty?
    end
  end

  def close
    @sock.close unless @sock.closed?
  end
end

class BattleduelTest < Minitest::Test
  def setup
    @pid = nil
    @reaped = false
    @clients = []
  end

  def teardown
    @clients.each { |c| c.close rescue nil }
    if @pid && !@reaped
      begin
        Process.kill("KILL", -@pid)
      rescue Errno::ESRCH, Errno::EPERM
        nil
      end
      begin
        Process.waitpid(@pid)
      rescue Errno::ECHILD
        nil
      end
    end
    @port_r&.close
  end

  def start_server
    @port_r, port_w = IO.pipe
    @pid = Process.spawn(RbConfig.ruby, SERVER, out: port_w, pgroup: true)
    port_w.close
    assert @port_r.wait_readable(10), "server never printed its PORT line"
    line = @port_r.gets
    m = /\APORT (\d+)\n\z/.match(line)
    refute_nil m, "first stdout line must be 'PORT <n>', got #{line.inspect}"
    Integer(m[1])
  end

  def connect(port)
    c = DuelClient.new(port)
    @clients << c
    c
  end

  def expect(client, want, ctx = nil)
    got = client.recv_line
    assert_equal want, got, ctx
  end

  def assert_clean_exit
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + IO_TIMEOUT
    loop do
      done, status = Process.waitpid2(@pid, Process::WNOHANG)
      if done
        @reaped = true
        assert status.exited?, "server was killed rather than exiting: #{status.inspect}"
        assert_equal 0, status.exitstatus, "server must exit 0 once the game is decided"
        return
      end
      flunk "server still running; expected it to exit after the game ended" \
        if Process.clock_gettime(Process::CLOCK_MONOTONIC) > deadline
      sleep 0.05
    end
  end

  def place_fleet(client, fixture)
    File.readlines(File.join(FIXTURES, fixture), chomp: true).each do |spec|
      next if spec.empty?
      client.send_line("PLACE #{spec}")
      expect(client, "OK PLACE #{spec.split.first}", "placing #{spec}")
    end
  end

  # Seats both players, places both fixture fleets (P2 first, to prove the
  # placement phase has no turn order), readies both, consumes START/TURN P1.
  def start_game(port)
    p1 = connect(port)
    expect(p1, "HELLO P1")
    p2 = connect(port)
    expect(p2, "HELLO P2")
    place_fleet(p2, "p2_fleet.txt")
    place_fleet(p1, "p1_fleet.txt")
    p2.send_line("READY")
    expect(p2, "OK READY")
    p1.send_line("READY")
    expect(p1, "OK READY")
    [p1, p2].each do |c|
      expect(c, "START")
      expect(c, "TURN P1")
    end
    [p1, p2]
  end

  def fire(shooter, both, coord, result)
    shooter.send_line("FIRE #{coord}")
    both.each do |c|
      expect(c, "SHOT #{result}", "firing at #{coord}")
    end
  end

  def test_full_game_follows_the_fixture_script
    port = start_server
    p1, p2 = start_game(port)
    both = [p1, p2]

    shots1 = File.readlines(File.join(FIXTURES, "p1_shots.txt"), chomp: true)
    shots2 = File.readlines(File.join(FIXTURES, "p2_shots.txt"), chomp: true)

    # (shooter, outcome, followup) — the pinned game story for the fixture
    # fleets and shot lists; coords are consumed from the shot-list files.
    script = [
      [:p1, "HIT",              "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "SUNK BATTLESHIP",  "TURN P1"],
      [:p1, "MISS",             "TURN P2"],
      [:p2, "HIT",              "TURN P2"],
      [:p2, "HIT",              "TURN P2"],
      [:p2, "HIT",              "TURN P2"],
      [:p2, "MISS",             "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "SUNK CRUISER",     "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "SUNK SUBMARINE",   "TURN P1"],
      [:p1, "HIT",              "TURN P1"],
      [:p1, "SUNK DESTROYER",   "WIN P1"],
    ]
    queues = { p1: shots1.dup, p2: shots2.dup }
    script.each do |who, outcome, followup|
      shooter = who == :p1 ? p1 : p2
      coord = queues[who].shift
      fire(shooter, both, coord, "#{who.to_s.upcase} #{coord} #{outcome}")
      both.each { |c| expect(c, followup, "after #{who} fired at #{coord}") }
    end
    assert_empty queues[:p1], "every P1 fixture shot must be used"
    assert_empty queues[:p2], "every P2 fixture shot must be used"

    assert p1.eof?, "server must close P1's connection after WIN"
    assert p2.eof?, "server must close P2's connection after WIN"
    assert_clean_exit
  end

  def test_placement_rules_are_enforced
    port = start_server
    p1 = connect(port)
    expect(p1, "HELLO P1")

    p1.send_line("FIRE A1")
    expect(p1, "ERR not-started")
    p1.send_line("PLACE A1")
    expect(p1, "ERR bad-command")
    p1.send_line("SCUTTLE NOW PLEASE")
    expect(p1, "ERR bad-command")
    p1.send_line("PLACE FRIGATE A1 H")
    expect(p1, "ERR bad-ship")
    p1.send_line("PLACE DESTROYER Z9 H")
    expect(p1, "ERR bad-coord")
    p1.send_line("PLACE DESTROYER A0 H")
    expect(p1, "ERR bad-coord")
    p1.send_line("PLACE DESTROYER I1 H")
    expect(p1, "ERR bad-coord")
    p1.send_line("PLACE DESTROYER A1 X")
    expect(p1, "ERR bad-orient")
    p1.send_line("PLACE BATTLESHIP A6 H")
    expect(p1, "ERR off-board")
    p1.send_line("PLACE BATTLESHIP F1 V")
    expect(p1, "ERR off-board")
    p1.send_line("READY")
    expect(p1, "ERR fleet-incomplete")

    p1.send_line("PLACE CRUISER B1 H\r")
    expect(p1, "OK PLACE CRUISER")
    p1.send_line("PLACE SUBMARINE B3 V")
    expect(p1, "ERR overlap")
    p1.send_line("PLACE CRUISER E5 H")
    expect(p1, "ERR already-placed")
    p1.send_line("PLACE SUBMARINE C1 H")
    expect(p1, "OK PLACE SUBMARINE")
    p1.send_line("PLACE BATTLESHIP D1 H")
    expect(p1, "OK PLACE BATTLESHIP")
    p1.send_line("READY")
    expect(p1, "ERR fleet-incomplete")
    p1.send_line("PLACE DESTROYER E1 V")
    expect(p1, "OK PLACE DESTROYER")
    p1.send_line("READY")
    expect(p1, "OK READY")
    p1.send_line("READY")
    expect(p1, "ERR already-ready")
    p1.send_line("PLACE DESTROYER G7 H")
    expect(p1, "ERR already-ready")
  end

  def test_turn_order_and_illegal_shots_change_nothing
    port = start_server
    p1, p2 = start_game(port)
    both = [p1, p2]

    p2.send_line("FIRE A1")
    expect(p2, "ERR not-your-turn")
    p1.send_line("FIRE Z9")
    expect(p1, "ERR bad-coord")
    p1.send_line("FIRE")
    expect(p1, "ERR bad-command")
    p1.send_line("FIRE H5 H6")
    expect(p1, "ERR bad-command")

    fire(p1, both, "H5", "P1 H5 HIT")
    both.each { |c| expect(c, "TURN P1") }

    p1.send_line("FIRE H5")
    expect(p1, "ERR repeat-shot")
    p2.send_line("FIRE A1")
    expect(p2, "ERR not-your-turn")

    fire(p1, both, "A8", "P1 A8 MISS")
    both.each { |c| expect(c, "TURN P2") }

    # P2 may fire at a cell P1 already used — shot history is per shooter.
    fire(p2, both, "H5", "P2 H5 MISS")
    both.each { |c| expect(c, "TURN P1") }
  end

  def test_a_third_connection_is_turned_away
    port = start_server
    p1 = connect(port)
    expect(p1, "HELLO P1")
    p2 = connect(port)
    expect(p2, "HELLO P2")
    extra = connect(port)
    expect(extra, "ERR full")
    assert extra.eof?, "the extra connection must be closed"
    # The seated players are unaffected.
    p1.send_line("PLACE DESTROYER A1 H")
    expect(p1, "OK PLACE DESTROYER")
  end

  def test_disconnect_mid_game_forfeits
    port = start_server
    p1, p2 = start_game(port)
    both = [p1, p2]
    fire(p1, both, "H5", "P1 H5 HIT")
    both.each { |c| expect(c, "TURN P1") }

    p2.close
    expect(p1, "WIN P1 forfeit")
    assert p1.eof?, "server must close the survivor's connection after a forfeit"
    assert_clean_exit
  end

  def test_disconnect_during_placement_also_forfeits
    port = start_server
    p1 = connect(port)
    expect(p1, "HELLO P1")
    p2 = connect(port)
    expect(p2, "HELLO P2")
    place_fleet(p1, "p1_fleet.txt")

    p1.close
    expect(p2, "WIN P2 forfeit")
    assert p2.eof?
    assert_clean_exit
  end
end
