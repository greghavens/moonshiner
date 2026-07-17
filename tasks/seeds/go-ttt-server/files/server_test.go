// Package harness drives the tttserver binary over loopback TCP with two
// scripted clients. It builds the server once, starts a fresh process per
// test in its own process group, parses the PORT line, keeps every read
// bounded by a deadline, and kills the whole process group in cleanup.
package harness

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

const ioTimeout = 5 * time.Second

var serverBin string

func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "tttserver-harness")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer os.RemoveAll(dir)
	serverBin = filepath.Join(dir, "tttserver")
	out, err := exec.Command("go", "build", "-race", "-o", serverBin, "./cmd/tttserver").CombinedOutput()
	if err != nil {
		fmt.Fprintf(os.Stderr, "building ./cmd/tttserver failed: %v\n%s", err, out)
		os.Exit(1)
	}
	os.Exit(m.Run())
}

type server struct {
	cmd    *exec.Cmd
	port   int
	waitCh chan error
}

// startServer launches a fresh server process in its own process group,
// reads the PORT line from its stdout, and registers a cleanup that kills
// the entire group.
func startServer(t *testing.T) *server {
	t.Helper()
	pr, pw, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	cmd := exec.Command(serverBin)
	cmd.Stdout = pw
	cmd.Stderr = os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		t.Fatalf("starting server: %v", err)
	}
	pw.Close()
	s := &server{cmd: cmd, waitCh: make(chan error, 1)}
	go func() { s.waitCh <- cmd.Wait() }()
	t.Cleanup(func() {
		syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		select {
		case <-s.waitCh:
		case <-time.After(ioTimeout):
			t.Error("server did not exit after SIGKILL to its process group")
		}
		pr.Close()
	})

	portCh := make(chan int, 1)
	go func() {
		sc := bufio.NewScanner(pr)
		if sc.Scan() {
			var p int
			if _, err := fmt.Sscanf(sc.Text(), "PORT %d", &p); err == nil {
				portCh <- p
			}
		}
		close(portCh)
		io.Copy(io.Discard, pr) // keep draining so the child never blocks
	}()
	select {
	case p, ok := <-portCh:
		if !ok || p <= 0 {
			t.Fatal("server did not print a valid PORT line")
		}
		s.port = p
	case <-time.After(10 * time.Second):
		t.Fatal("timed out waiting for the PORT line")
	}
	return s
}

// expectCleanExit asserts the server terminates on its own with status 0.
func (s *server) expectCleanExit(t *testing.T) {
	t.Helper()
	select {
	case err := <-s.waitCh:
		s.waitCh <- err // keep cleanup happy
		if err != nil {
			t.Fatalf("server exited with error: %v", err)
		}
	case <-time.After(ioTimeout):
		t.Fatal("server still running; expected it to exit after the game ended")
	}
}

type client struct {
	t    *testing.T
	conn net.Conn
	r    *bufio.Reader
}

func dial(t *testing.T, s *server) *client {
	t.Helper()
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", s.port), ioTimeout)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { conn.Close() })
	return &client{t: t, conn: conn, r: bufio.NewReader(conn)}
}

func (c *client) send(line string) {
	c.t.Helper()
	c.conn.SetWriteDeadline(time.Now().Add(ioTimeout))
	if _, err := fmt.Fprintf(c.conn, "%s\n", line); err != nil {
		c.t.Fatalf("send %q: %v", line, err)
	}
}

func (c *client) recv() string {
	c.t.Helper()
	c.conn.SetReadDeadline(time.Now().Add(ioTimeout))
	line, err := c.r.ReadString('\n')
	if err != nil {
		c.t.Fatalf("read: %v (partial %q)", err, line)
	}
	return strings.TrimRight(line, "\n")
}

func (c *client) expect(want string) {
	c.t.Helper()
	if got := c.recv(); got != want {
		c.t.Fatalf("got %q, want %q", got, want)
	}
}

func (c *client) expectEOF() {
	c.t.Helper()
	c.conn.SetReadDeadline(time.Now().Add(ioTimeout))
	line, err := c.r.ReadString('\n')
	if !errors.Is(err, io.EOF) {
		c.t.Fatalf("expected connection close, got line %q err %v", line, err)
	}
}

// joinBoth connects two clients, joins them, and consumes the game-start
// broadcast. Returns (X client, O client).
func joinBoth(t *testing.T, s *server) (*client, *client) {
	t.Helper()
	cx := dial(t, s)
	cx.expect("HELLO X")
	co := dial(t, s)
	co.expect("HELLO O")
	cx.send("JOIN alice")
	co.send("JOIN bob")
	for _, c := range []*client{cx, co} {
		c.expect("BOARD .........")
		c.expect("TURN X")
	}
	return cx, co
}

// play sends MOVE cell from the mover and checks both clients see the same
// board broadcast followed by followup (a TURN/WIN/DRAW line).
func play(t *testing.T, mover *client, cell int, both []*client, board, followup string) {
	t.Helper()
	mover.send(fmt.Sprintf("MOVE %d", cell))
	for _, c := range both {
		c.expect("BOARD " + board)
		c.expect(followup)
	}
}

func TestFullGameXWins(t *testing.T) {
	s := startServer(t)
	cx, co := joinBoth(t, s)
	both := []*client{cx, co}

	play(t, cx, 0, both, "X........", "TURN O")
	play(t, co, 3, both, "X..O.....", "TURN X")
	play(t, cx, 1, both, "XX.O.....", "TURN O")
	play(t, co, 4, both, "XX.OO....", "TURN X")
	play(t, cx, 2, both, "XXXOO....", "WIN X")

	cx.expectEOF()
	co.expectEOF()
	s.expectCleanExit(t)
}

func TestFullBoardIsADraw(t *testing.T) {
	s := startServer(t)
	cx, co := joinBoth(t, s)
	both := []*client{cx, co}

	play(t, cx, 0, both, "X........", "TURN O")
	play(t, co, 1, both, "XO.......", "TURN X")
	play(t, cx, 2, both, "XOX......", "TURN O")
	play(t, co, 4, both, "XOX.O....", "TURN X")
	play(t, cx, 3, both, "XOXXO....", "TURN O")
	play(t, co, 5, both, "XOXXOO...", "TURN X")
	play(t, cx, 7, both, "XOXXOO.X.", "TURN O")
	play(t, co, 6, both, "XOXXOOOX.", "TURN X")
	play(t, cx, 8, both, "XOXXOOOXX", "DRAW")

	cx.expectEOF()
	co.expectEOF()
	s.expectCleanExit(t)
}

func TestIllegalMovesAreRejectedWithoutStateChange(t *testing.T) {
	s := startServer(t)
	cx, co := joinBoth(t, s)
	both := []*client{cx, co}

	play(t, cx, 0, both, "X........", "TURN O")

	// Out of turn: only the offender hears about it.
	cx.send("MOVE 1")
	cx.expect("ERR not-your-turn")

	// Occupied cell and malformed cells, all rejected for O.
	co.send("MOVE 0")
	co.expect("ERR occupied")
	co.send("MOVE 9")
	co.expect("ERR bad-cell")
	co.send("MOVE -1")
	co.expect("ERR bad-cell")
	co.send("MOVE four")
	co.expect("ERR bad-cell")
	co.send("MOVE")
	co.expect("ERR bad-cell")

	// State is untouched: both clients read the same unchanged board on
	// demand, with no stray broadcasts queued in front of it.
	cx.send("BOARD")
	cx.expect("BOARD X........")
	co.send("BOARD")
	co.expect("BOARD X........")

	// The game continues normally after all that abuse.
	play(t, co, 4, both, "X...O....", "TURN X")
}

func TestJoinHandshakeAndLobbyErrors(t *testing.T) {
	s := startServer(t)
	cx := dial(t, s)
	cx.expect("HELLO X")

	cx.send("MOVE 0")
	cx.expect("ERR not-started")
	cx.send("JOIN bad name")
	cx.expect("ERR bad-name")
	cx.send("JOIN this-name-is-way-too-long")
	cx.expect("ERR bad-name")
	cx.send("FROBNICATE 7")
	cx.expect("ERR bad-command")
	cx.send("JOIN alice")
	cx.send("JOIN alice")
	cx.expect("ERR already-joined")

	co := dial(t, s)
	co.expect("HELLO O")
	co.send("MOVE 0")
	co.expect("ERR not-started")
	co.send("JOIN bob")
	for _, c := range []*client{cx, co} {
		c.expect("BOARD .........")
		c.expect("TURN X")
	}

	// A third connection is turned away.
	extra := dial(t, s)
	extra.expect("ERR full")
	extra.expectEOF()

	// The two seated players are unaffected.
	cx.send("BOARD")
	cx.expect("BOARD .........")
}

func TestDisconnectForfeitsTheGame(t *testing.T) {
	s := startServer(t)
	cx, co := joinBoth(t, s)
	both := []*client{cx, co}

	play(t, cx, 4, both, "....X....", "TURN O")

	// X walks away mid-game: O wins by forfeit and the server shuts down.
	cx.conn.Close()
	co.expect("WIN O forfeit")
	co.expectEOF()
	s.expectCleanExit(t)
}

func TestDisconnectBeforeStartAlsoForfeits(t *testing.T) {
	s := startServer(t)
	cx := dial(t, s)
	cx.expect("HELLO X")
	cx.send("JOIN alice")
	co := dial(t, s)
	co.expect("HELLO O")

	// O never joins — it just leaves. X wins by forfeit.
	co.conn.Close()
	cx.expect("WIN X forfeit")
	cx.expectEOF()
	s.expectCleanExit(t)
}
