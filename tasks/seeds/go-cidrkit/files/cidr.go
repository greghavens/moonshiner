// Package cidrkit is the address math behind the firewall-rule
// compiler. Rules reference IPv4 CIDR blocks; the compiler needs to
// parse them strictly and answer membership questions.
package cidrkit

import (
	"fmt"
	"strconv"
	"strings"
)

// Net is an IPv4 CIDR block.
type Net struct {
	base uint32 // network address, host bits all zero
	bits int    // prefix length, 0..32
}

// ParseIP parses a dotted-quad IPv4 address into its 32-bit value.
func ParseIP(s string) (uint32, error) {
	parts := strings.Split(s, ".")
	if len(parts) != 4 {
		return 0, fmt.Errorf("cidrkit: %q is not a dotted-quad IPv4 address", s)
	}
	var ip uint32
	for _, p := range parts {
		if p == "" || len(p) > 3 {
			return 0, fmt.Errorf("cidrkit: bad octet %q in %q", p, s)
		}
		for i := 0; i < len(p); i++ {
			if p[i] < '0' || p[i] > '9' {
				return 0, fmt.Errorf("cidrkit: bad octet %q in %q", p, s)
			}
		}
		n, err := strconv.Atoi(p)
		if err != nil || n > 255 {
			return 0, fmt.Errorf("cidrkit: bad octet %q in %q", p, s)
		}
		ip = ip<<8 | uint32(n)
	}
	return ip, nil
}

// FormatIP renders a 32-bit value as a dotted quad.
func FormatIP(ip uint32) string {
	return fmt.Sprintf("%d.%d.%d.%d", ip>>24, ip>>16&0xff, ip>>8&0xff, ip&0xff)
}

// ParseCIDR parses "a.b.c.d/len". The address must be the true network
// address of the block: host bits set is an error, because a firewall
// rule that says 10.0.0.1/8 is almost always a typo.
func ParseCIDR(s string) (Net, error) {
	addr, mask, ok := strings.Cut(s, "/")
	if !ok {
		return Net{}, fmt.Errorf("cidrkit: %q is missing a prefix length", s)
	}
	ip, err := ParseIP(addr)
	if err != nil {
		return Net{}, err
	}
	bits, err := strconv.Atoi(mask)
	if err != nil || bits < 0 || bits > 32 {
		return Net{}, fmt.Errorf("cidrkit: bad prefix length %q in %q", mask, s)
	}
	n := Net{base: ip & maskFor(bits), bits: bits}
	if n.base != ip {
		return Net{}, fmt.Errorf("cidrkit: %q has host bits set (network address is %s)", s, FormatIP(n.base))
	}
	return n, nil
}

// Contains reports whether ip falls inside the block.
func (n Net) Contains(ip uint32) bool {
	return ip&maskFor(n.bits) == n.base
}

// String renders the block in CIDR notation.
func (n Net) String() string {
	return fmt.Sprintf("%s/%d", FormatIP(n.base), n.bits)
}

func maskFor(bits int) uint32 {
	return ^uint32(0) << (32 - uint(bits))
}
