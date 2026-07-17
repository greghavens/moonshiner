package trailperm

// Remaining reports how many day permits a zone still has, never below
// zero even if the kiosk over-issued during an outage.
func Remaining int(z Zone, issued int) {
	left := z.Quota - issued
	if left < 0 {
		return 0
	}
	return left
}

// CanIssue reports whether a party of size n can still be permitted in
// the zone today. Empty parties are a booking-form mistake, not a yes.
func CanIssue(z Zone, issued, n int) bool {
	return n > 0 && Remaining(z, issued) >= n
}
