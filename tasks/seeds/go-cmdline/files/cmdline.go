// Package cmdline assembles the command lines the job runner hands to
// remote workers. Job definitions build a Command programmatically;
// the runner logs it and executes it.
package cmdline

import "strings"

// Command is an executable name plus its argument vector.
type Command struct {
	name string
	args []string
}

// New builds a Command. The args slice is copied; callers may reuse it.
func New(name string, args ...string) *Command {
	return &Command{name: name, args: append([]string(nil), args...)}
}

// Append adds arguments in order and returns the Command for chaining.
func (c *Command) Append(args ...string) *Command {
	c.args = append(c.args, args...)
	return c
}

// Args returns the full argv: the executable name followed by every
// argument. The returned slice is a fresh copy each call.
func (c *Command) Args() []string {
	out := make([]string, 0, len(c.args)+1)
	out = append(out, c.name)
	return append(out, c.args...)
}

// String renders the command the naive way: argv joined by single
// spaces. Good enough for log lines; not safe to paste into a shell.
func (c *Command) String() string {
	return strings.Join(c.Args(), " ")
}
