// Package guestcust applies operator-authored customization profiles to virtual
// machines through the vSphere Automation API on a VCF 9.0 vCenter.
//
// The exported surface below is consumed by verify/ and must keep these exact
// signatures. Everything else in the package — the wire types, the translation from
// a Profile to those wire types, the HTTP plumbing — is unwritten.
package guestcust

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
)

// ErrNotCustomizable reports that the customization precheck refused the virtual
// machine. ApplyProfile returns an error satisfying errors.Is(err, ErrNotCustomizable)
// in that case, and must not have issued the mutating request.
var ErrNotCustomizable = errors.New("guestcust: virtual machine is not customizable")

// Profile is an operator-authored customization profile, as stored in profiles/.
type Profile struct {
	VM          string        `json:"vm"`
	GuestFamily string        `json:"guest_family"` // "LINUX" or "WINDOWS"
	Hostname    Hostname      `json:"hostname"`
	Domain      string        `json:"domain"`
	TimeZone    string        `json:"time_zone"`
	Script      string        `json:"script"`
	DNSServers  []string      `json:"dns_servers"`
	DNSSuffixes []string      `json:"dns_suffixes"`
	NICs        []NIC         `json:"nics"`
	Windows     *WindowsBlock `json:"windows"`
}

// Hostname is how the profile asks for the guest's host name to be chosen.
type Hostname struct {
	Kind  string `json:"kind"` // "FIXED", "PREFIX", "VIRTUAL_MACHINE" or "USER_INPUT_REQUIRED"
	Value string `json:"value"`
}

// NIC is one virtual network adapter's addressing.
type NIC struct {
	MAC  string `json:"mac"`
	IPv4 IPv4   `json:"ipv4"`
}

// IPv4 is the IPv4 addressing for one adapter.
type IPv4 struct {
	Mode     string   `json:"mode"` // "DHCP", "STATIC" or "USER_INPUT_REQUIRED"
	Address  string   `json:"address"`
	Prefix   int64    `json:"prefix"`
	Gateways []string `json:"gateways"`
}

// WindowsBlock carries the Windows-only settings of a profile.
type WindowsBlock struct {
	FullName       string `json:"full_name"`
	Organization   string `json:"organization"`
	ProductKey     string `json:"product_key"`
	AutoLogon      bool   `json:"auto_logon"`
	AutoLogonCount int64  `json:"auto_logon_count"`
	TimeZoneID     int64  `json:"time_zone_id"`
	AdminPassword  string `json:"admin_password"`
	Workgroup      string `json:"workgroup"`
	DomainUsername string `json:"domain_username"`
	DomainPassword string `json:"domain_password"`
}

// LoadProfile decodes a profile document.
func LoadProfile(data []byte) (*Profile, error) {
	var p Profile
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&p); err != nil {
		return nil, err
	}
	return &p, nil
}

// Result is the outcome of an ApplyProfile call.
type Result struct {
	// CheckStatus is the check_status the precheck reported.
	CheckStatus string `json:"check_status"`
	// Applied reports whether the customization was sent to vCenter.
	Applied bool `json:"applied"`
	// Reasons names each precheck signal that refused the virtual machine,
	// sorted ascending. Empty when Applied is true.
	Reasons []string `json:"reasons"`
}

// Client talks to one vCenter.
type Client struct {
	baseURL   string
	sessionID string
	hc        *http.Client
}

// NewClient returns a Client for the vCenter reachable at baseURL, authenticating
// with sessionID. A nil hc means http.DefaultClient.
func NewClient(baseURL, sessionID string, hc *http.Client) *Client {
	if hc == nil {
		hc = http.DefaultClient
	}
	return &Client{baseURL: baseURL, sessionID: sessionID, hc: hc}
}

// ApplyProfile runs the customization precheck for the profile's virtual machine
// and, only if the precheck permits it, applies the profile's customization
// specification.
func (c *Client) ApplyProfile(ctx context.Context, p *Profile) (Result, error) {
	panic("guestcust: ApplyProfile is not implemented")
}
