// Package preview renders the short message previews shown in the push
// notification tray: the first line of the body, capped to a display
// budget, plus the sender initial used for the avatar bubble.
package preview

import "strings"

// maxRunesDefault is the tray's display budget when the caller passes 0.
const maxRunesDefault = 60

// Snippet returns the first line of body capped to max characters,
// appending an ellipsis when it had to cut anything off. A max of 0 uses
// the tray default.
func Snippet(body string, max int) string {
	if max <= 0 {
		max = maxRunesDefault
	}
	body = strings.TrimSpace(body)
	if i := strings.IndexAny(body, "\r\n"); i >= 0 {
		body = strings.TrimRight(body[:i], " \t")
	}
	if len(body) <= max {
		return body
	}
	return body[:max-1] + "…"
}

// Initial returns the single uppercase character shown in the sender's
// avatar bubble, or "?" when the name is empty.
func Initial(name string) string {
	name = strings.TrimSpace(name)
	if name == "" {
		return "?"
	}
	return strings.ToUpper(string(name[0]))
}
