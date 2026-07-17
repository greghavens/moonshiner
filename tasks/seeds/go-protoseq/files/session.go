// Package session implements the server-side state machine for imp/1, the
// line-oriented protocol our bulk loader speaks to the import service.
//
// One import runs HELLO -> SCHEMA -> ROW... -> COMMIT (or ABORT). QUIT ends
// the connection. Stepping is pure: state in, line in, state and reply out.
// Protocol violations return an error and leave the state as it was, so the
// client can correct itself and continue.
package session

import (
	"errors"
	"fmt"
	"strings"
)

// Proto is the protocol version accepted in HELLO.
const Proto = "imp/1"

// Phase is where a session is in the import conversation.
type Phase string

const (
	PhaseReady  Phase = "ready"  // waiting for HELLO
	PhaseSchema Phase = "schema" // greeted, waiting for SCHEMA
	PhaseRows   Phase = "rows"   // schema set, accepting ROW/COMMIT/ABORT
	PhaseClosed Phase = "closed" // QUIT received, connection is done
)

// State is the whole session state; it is a value, so stepping never
// aliases and a caller can keep any snapshot it likes.
type State struct {
	Phase Phase
	Table string
	Cols  int
	Rows  int
}

// NewState returns the state a connection starts in.
func NewState() State {
	return State{Phase: PhaseReady}
}

// Step consumes one inbound line and returns the next state and the reply
// to write back. On a protocol error the returned state is the input state.
func Step(s State, line string) (State, string, error) {
	if s.Phase == PhaseClosed {
		return s, "", errors.New("connection closed")
	}
	verb, rest, _ := strings.Cut(strings.TrimRight(line, "\r\n"), " ")
	if verb == "" {
		return s, "", errors.New("empty line")
	}

	switch verb {
	case "HELLO":
		if s.Phase != PhaseReady {
			return s, "", fmt.Errorf("unexpected message: %s", verb)
		}
		if rest != Proto {
			return s, "", fmt.Errorf("unsupported protocol %q", rest)
		}
		s.Phase = PhaseSchema
		return s, "OK " + Proto + " ready", nil

	case "SCHEMA":
		if s.Phase != PhaseSchema {
			return s, "", fmt.Errorf("unexpected message: %s", verb)
		}
		table, colSpec, ok := strings.Cut(rest, " ")
		if !ok || table == "" || colSpec == "" {
			return s, "", errors.New("schema needs a table name and a column list")
		}
		cols := strings.Split(colSpec, ",")
		for _, col := range cols {
			if col == "" {
				return s, "", errors.New("schema has an empty column name")
			}
		}
		s.Phase, s.Table, s.Cols, s.Rows = PhaseRows, table, len(cols), 0
		return s, fmt.Sprintf("OK %d columns", len(cols)), nil

	case "ROW":
		if s.Phase != PhaseRows {
			return s, "", fmt.Errorf("unexpected message: %s", verb)
		}
		if rest == "" {
			return s, "", errors.New("empty row")
		}
		if got := len(strings.Split(rest, ",")); got != s.Cols {
			return s, "", fmt.Errorf("row has %d fields, want %d", got, s.Cols)
		}
		s.Rows++
		return s, "OK", nil

	case "COMMIT":
		if s.Phase != PhaseRows {
			return s, "", fmt.Errorf("unexpected message: %s", verb)
		}
		reply := fmt.Sprintf("OK imported %d rows into %s", s.Rows, s.Table)
		s.Table, s.Cols, s.Rows = "", 0, 0
		return s, reply, nil

	case "ABORT":
		if s.Phase != PhaseSchema && s.Phase != PhaseRows {
			return s, "", fmt.Errorf("unexpected message: %s", verb)
		}
		return State{Phase: PhaseReady}, "OK aborted", nil

	case "QUIT":
		return State{Phase: PhaseClosed}, "BYE", nil

	default:
		return s, "", fmt.Errorf("unexpected message: %s", verb)
	}
}

// Conn tracks one client connection's session across messages. The
// listener keeps a Conn per accepted connection and feeds it lines.
type Conn struct {
	state State
}

// NewConn returns a connection session in its starting state.
func NewConn() *Conn {
	return &Conn{state: NewState()}
}

// State reports the current session state (used by the metrics endpoint).
func (c *Conn) State() State {
	return c.state
}

// Handle feeds one line through the machine. On a protocol error the
// session stays where it was and the caller writes an ERR line.
func (c *Conn) Handle(line string) (string, error) {
	next, reply, err := Step(c.state, line)
	if err != nil {
		return "", err
	}
	c.state = next
	return reply, nil
}
