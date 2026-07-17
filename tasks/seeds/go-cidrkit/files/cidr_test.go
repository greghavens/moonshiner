package cidrkit

import "testing"

// Pins the existing parse/format/membership behavior. Must keep passing.

func TestParseIPRoundTrip(t *testing.T) {
	for _, s := range []string{"0.0.0.0", "10.0.0.1", "192.168.100.7", "255.255.255.255"} {
		ip, err := ParseIP(s)
		if err != nil {
			t.Fatalf("ParseIP(%q): %v", s, err)
		}
		if got := FormatIP(ip); got != s {
			t.Fatalf("FormatIP(ParseIP(%q)) = %q", s, got)
		}
	}
	if ip, _ := ParseIP("1.2.3.4"); ip != 0x01020304 {
		t.Fatalf("ParseIP(1.2.3.4) = %#x, want 0x01020304", ip)
	}
}

func TestParseIPRejectsGarbage(t *testing.T) {
	for _, s := range []string{"", "1.2.3", "1.2.3.4.5", "256.0.0.1", "1.2.3.-4", "a.b.c.d", "1..2.3", "1.2.3.4 "} {
		if _, err := ParseIP(s); err == nil {
			t.Fatalf("ParseIP(%q) succeeded, want error", s)
		}
	}
}

func TestParseCIDRAcceptsProperNetworks(t *testing.T) {
	n, err := ParseCIDR("10.42.0.0/16")
	if err != nil {
		t.Fatalf("ParseCIDR: %v", err)
	}
	if got := n.String(); got != "10.42.0.0/16" {
		t.Fatalf("String() = %q, want %q", got, "10.42.0.0/16")
	}
	if _, err := ParseCIDR("0.0.0.0/0"); err != nil {
		t.Fatalf("ParseCIDR(0.0.0.0/0): %v", err)
	}
	if _, err := ParseCIDR("10.0.0.7/32"); err != nil {
		t.Fatalf("ParseCIDR(10.0.0.7/32): %v", err)
	}
}

func TestParseCIDRRejectsHostBitsAndBadMasks(t *testing.T) {
	for _, s := range []string{"10.0.0.1/8", "10.42.0.1/16", "10.0.0.0/33", "10.0.0.0/-1", "10.0.0.0", "10.0.0.0/x"} {
		if _, err := ParseCIDR(s); err == nil {
			t.Fatalf("ParseCIDR(%q) succeeded, want error", s)
		}
	}
}

func TestContainsBoundaries(t *testing.T) {
	n, err := ParseCIDR("10.1.2.0/23")
	if err != nil {
		t.Fatalf("ParseCIDR: %v", err)
	}
	in := []string{"10.1.2.0", "10.1.2.255", "10.1.3.0", "10.1.3.255"}
	out := []string{"10.1.1.255", "10.1.4.0", "11.1.2.0"}
	for _, s := range in {
		ip, _ := ParseIP(s)
		if !n.Contains(ip) {
			t.Fatalf("%s should contain %s", n, s)
		}
	}
	for _, s := range out {
		ip, _ := ParseIP(s)
		if n.Contains(ip) {
			t.Fatalf("%s should not contain %s", n, s)
		}
	}
}
