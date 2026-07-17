// Tests for the imp/1 session machine. They drive full conversations
// through Conn (the per-connection wrapper) and poke edge cases through
// the pure Step function directly.
package session

import (
	"fmt"
	"strings"
	"testing"
)

// say feeds one line and fails the test on a protocol error.
func say(t *testing.T, c *Conn, line, wantReply string) {
	t.Helper()
	reply, err := c.Handle(line)
	if err != nil {
		t.Fatalf("Handle(%q): unexpected protocol error: %v", line, err)
	}
	if reply != wantReply {
		t.Fatalf("Handle(%q) = %q, want %q", line, reply, wantReply)
	}
}

// runImport drives one full import of n rows through the connection.
func runImport(t *testing.T, c *Conn, table string, n int) {
	t.Helper()
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
	say(t, c, "SCHEMA "+table+" id,qty,price", "OK 3 columns")
	for i := 0; i < n; i++ {
		say(t, c, fmt.Sprintf("ROW %d,4,995", i+1), "OK")
	}
	say(t, c, "COMMIT", fmt.Sprintf("OK imported %d rows into %s", n, table))
}

func TestSingleImportHappyPath(t *testing.T) {
	c := NewConn()
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
	say(t, c, "SCHEMA orders id,qty,price", "OK 3 columns")
	say(t, c, "ROW 1,4,995", "OK")
	say(t, c, "ROW 2,1,250", "OK")
	say(t, c, "COMMIT", "OK imported 2 rows into orders")
}

func TestConnectionReuseAcrossImports(t *testing.T) {
	c := NewConn()
	for i, table := range []string{"orders", "refunds", "stock"} {
		reply, err := c.Handle("HELLO imp/1")
		if err != nil {
			t.Fatalf("import #%d: HELLO on the kept-alive connection failed: %v", i+1, err)
		}
		if reply != "OK imp/1 ready" {
			t.Fatalf("import #%d: HELLO reply = %q, want %q", i+1, reply, "OK imp/1 ready")
		}
		say(t, c, "SCHEMA "+table+" id,qty,price", "OK 3 columns")
		say(t, c, "ROW 7,2,100", "OK")
		say(t, c, "ROW 8,1,300", "OK")
		say(t, c, "COMMIT", "OK imported 2 rows into "+table)
	}
}

func TestFreshConnectionsAreIndependent(t *testing.T) {
	a, b := NewConn(), NewConn()
	say(t, a, "HELLO imp/1", "OK imp/1 ready")
	say(t, b, "HELLO imp/1", "OK imp/1 ready")
	say(t, a, "SCHEMA orders id,qty,price", "OK 3 columns")
	say(t, b, "SCHEMA stock sku,count", "OK 2 columns")
	say(t, a, "ROW 1,4,995", "OK")
	say(t, b, "ROW WIDGET-9,40", "OK")
	say(t, a, "COMMIT", "OK imported 1 rows into orders")
	say(t, b, "COMMIT", "OK imported 1 rows into stock")
}

func TestAbortDiscardsTheImportAndTheSessionSurvives(t *testing.T) {
	c := NewConn()
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
	say(t, c, "SCHEMA orders id,qty,price", "OK 3 columns")
	say(t, c, "ROW 1,4,995", "OK")
	say(t, c, "ABORT", "OK aborted")
	// the same connection can start over
	runImport(t, c, "orders", 1)
}

func TestOutOfOrderMessagesAreRejectedWithoutStateDamage(t *testing.T) {
	start := NewState()

	for _, line := range []string{"SCHEMA orders id,qty", "ROW 1,2", "COMMIT", "ABORT"} {
		next, reply, err := Step(start, line)
		verb := strings.Fields(line)[0]
		if err == nil {
			t.Fatalf("Step(ready, %q) accepted, reply %q; want error", line, reply)
		}
		if want := "unexpected message: " + verb; err.Error() != want {
			t.Fatalf("Step(ready, %q) error = %q, want %q", line, err, want)
		}
		if next != start {
			t.Fatalf("state changed on a rejected message: %+v -> %+v", start, next)
		}
	}

	greeted, _, err := Step(start, "HELLO imp/1")
	if err != nil {
		t.Fatalf("HELLO: %v", err)
	}
	if _, _, err := Step(greeted, "COMMIT"); err == nil {
		t.Fatal("COMMIT before SCHEMA must be rejected")
	}
	if _, _, err := Step(greeted, "HELLO imp/1"); err == nil {
		t.Fatal("a second HELLO mid-conversation must be rejected")
	}
}

func TestRowWidthIsValidated(t *testing.T) {
	c := NewConn()
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
	say(t, c, "SCHEMA orders id,qty,price", "OK 3 columns")
	_, err := c.Handle("ROW 1,4")
	if err == nil || !strings.Contains(err.Error(), "want 3") {
		t.Fatalf("short row error = %v, want a count mismatch mentioning 'want 3'", err)
	}
	// the bad row was not counted and the session is still usable
	say(t, c, "ROW 1,4,995", "OK")
	say(t, c, "COMMIT", "OK imported 1 rows into orders")
}

func TestHelloRequiresTheProtocolVersion(t *testing.T) {
	c := NewConn()
	if _, err := c.Handle("HELLO imp/2"); err == nil ||
		!strings.Contains(err.Error(), "unsupported protocol") {
		t.Fatalf("HELLO imp/2 error = %v, want unsupported protocol", err)
	}
	// still in ready: the correct greeting works
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
}

func TestSchemaIsValidated(t *testing.T) {
	c := NewConn()
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
	if _, err := c.Handle("SCHEMA orders"); err == nil {
		t.Fatal("SCHEMA without columns must be rejected")
	}
	if _, err := c.Handle("SCHEMA orders id,,price"); err == nil {
		t.Fatal("SCHEMA with an empty column name must be rejected")
	}
	say(t, c, "SCHEMA orders id,qty", "OK 2 columns")
}

func TestQuitClosesTheConnection(t *testing.T) {
	c := NewConn()
	say(t, c, "HELLO imp/1", "OK imp/1 ready")
	say(t, c, "QUIT", "BYE")
	if _, err := c.Handle("HELLO imp/1"); err == nil ||
		!strings.Contains(err.Error(), "connection closed") {
		t.Fatalf("post-QUIT error = %v, want connection closed", err)
	}
}
