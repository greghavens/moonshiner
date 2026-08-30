package verify

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strings"
	"testing"

	"example.com/vcf90/gosc/guestcust"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func response(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func validLinuxProfile() *guestcust.Profile {
	return &guestcust.Profile{
		VM:          "vm-validation",
		GuestFamily: "LINUX",
		Hostname:    guestcust.Hostname{Kind: "FIXED", Value: "host"},
		Domain:      "corp.example.com",
		NICs: []guestcust.NIC{{
			IPv4: guestcust.IPv4{Mode: "DHCP"},
		}},
	}
}

func validWindowsProfile() *guestcust.Profile {
	return &guestcust.Profile{
		VM:          "vm-validation",
		GuestFamily: "WINDOWS",
		Hostname:    guestcust.Hostname{Kind: "FIXED", Value: "HOST"},
		Windows: &guestcust.WindowsBlock{
			FullName:     "Operator",
			Organization: "Example",
			ProductKey:   "KEY",
			Workgroup:    "WORKGROUP",
		},
	}
}

// TestInvalidProfilesMakeNoRequests protects the local-validation requirement.
// Every case is an invalid condition named explicitly in the task; validation must
// happen before even the precheck is sent.
func TestInvalidProfilesMakeNoRequests(t *testing.T) {
	tests := []struct {
		name string
		make func() *guestcust.Profile
		want []string
	}{
		{"missing vm", func() *guestcust.Profile { p := validLinuxProfile(); p.VM = ""; return p }, []string{"vm", "virtual machine"}},
		{"unknown guest family", func() *guestcust.Profile { p := validLinuxProfile(); p.GuestFamily = "SOLARIS"; return p }, []string{"guest_family", "guest family"}},
		{"linux domain is required", func() *guestcust.Profile { p := validLinuxProfile(); p.Domain = ""; return p }, []string{"domain"}},
		{"windows block is required", func() *guestcust.Profile { p := validWindowsProfile(); p.Windows = nil; return p }, []string{"windows"}},
		{"fixed hostname needs a value", func() *guestcust.Profile { p := validLinuxProfile(); p.Hostname.Value = ""; return p }, []string{"hostname", "fixed"}},
		{"prefix hostname needs a value", func() *guestcust.Profile {
			p := validLinuxProfile()
			p.Hostname = guestcust.Hostname{Kind: "PREFIX"}
			return p
		}, []string{"hostname", "prefix"}},
		{"unknown hostname kind", func() *guestcust.Profile { p := validLinuxProfile(); p.Hostname.Kind = "RANDOM"; return p }, []string{"hostname", "kind"}},
		{"both windows join modes", func() *guestcust.Profile { p := validWindowsProfile(); p.Windows.DomainUsername = "joiner"; return p }, []string{"both", "workgroup"}},
		{"domain join needs a domain", func() *guestcust.Profile {
			p := validWindowsProfile()
			p.Windows.Workgroup = ""
			p.Windows.DomainUsername = "joiner"
			return p
		}, []string{"domain"}},
		{"static ipv4 needs an address", func() *guestcust.Profile {
			p := validLinuxProfile()
			p.NICs[0].IPv4 = guestcust.IPv4{Mode: "STATIC", Prefix: 24}
			return p
		}, []string{"address"}},
		{"static ipv4 prefix cannot be zero", func() *guestcust.Profile {
			p := validLinuxProfile()
			p.NICs[0].IPv4 = guestcust.IPv4{Mode: "STATIC", Address: "10.0.0.2"}
			return p
		}, []string{"prefix"}},
		{"static ipv4 prefix cannot exceed 32", func() *guestcust.Profile {
			p := validLinuxProfile()
			p.NICs[0].IPv4 = guestcust.IPv4{Mode: "STATIC", Address: "10.0.0.2", Prefix: 33}
			return p
		}, []string{"prefix"}},
		{"unknown ipv4 mode", func() *guestcust.Profile { p := validLinuxProfile(); p.NICs[0].IPv4.Mode = "DISABLED"; return p }, []string{"ipv4", "mode"}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			hc := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
				calls++
				return nil, errors.New("unexpected request")
			})}
			c := guestcust.NewClient("http://127.0.0.1", sessionID, hc)
			res, err := c.ApplyProfile(context.Background(), tc.make())
			if err == nil {
				t.Fatal("ApplyProfile accepted the invalid profile")
			}
			if errors.Is(err, guestcust.ErrNotCustomizable) {
				t.Errorf("local validation error was reported as a precheck refusal: %v", err)
			}
			mentionsPart := false
			for _, part := range tc.want {
				if strings.Contains(strings.ToLower(err.Error()), strings.ToLower(part)) {
					mentionsPart = true
					break
				}
			}
			if !mentionsPart {
				t.Errorf("error %q does not identify the invalid profile part (want one of %v)", err, tc.want)
			}
			if res.Applied {
				t.Error("Applied = true for an invalid profile")
			}
			if calls != 0 {
				t.Errorf("invalid profile caused %d request(s), want none", calls)
			}
		})
	}
}

func TestPrecheckTransportFailureIsNotARefusal(t *testing.T) {
	calls := 0
	hc := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls++
		return nil, errors.New("connection lost")
	})}
	res, err := guestcust.NewClient("http://127.0.0.1", sessionID, hc).
		ApplyProfile(context.Background(), validLinuxProfile())
	if err == nil {
		t.Fatal("ApplyProfile returned no error")
	}
	if errors.Is(err, guestcust.ErrNotCustomizable) {
		t.Errorf("precheck transport failure was reported as a refusal: %v", err)
	}
	if res.Applied {
		t.Error("Applied = true after a failed precheck")
	}
	if calls != 1 {
		t.Errorf("got %d requests, want the precheck only", calls)
	}
}

func TestUnsupportedUnknownStatusUsesUnspecifiedReason(t *testing.T) {
	calls := 0
	hc := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls++
		return response(200, `{"check_status":"UNKNOWN"}`), nil
	})}
	res, err := guestcust.NewClient("http://127.0.0.1", sessionID, hc).
		ApplyProfile(context.Background(), validLinuxProfile())
	if !errors.Is(err, guestcust.ErrNotCustomizable) {
		t.Fatalf("error %v does not satisfy errors.Is(err, ErrNotCustomizable)", err)
	}
	if res.CheckStatus != "UNKNOWN" || res.Applied || !reflect.DeepEqual(res.Reasons, []string{"unspecified"}) {
		t.Errorf("Result = %+v, want UNKNOWN, not applied, reason unspecified", res)
	}
	if calls != 1 {
		t.Errorf("got %d requests, want the precheck only", calls)
	}
}

func TestMutationFailureIsNotReportedAsAppliedOrRefused(t *testing.T) {
	calls := 0
	hc := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls++
		if calls == 1 {
			return response(200, `{"check_status":"SUPPORTED"}`), nil
		}
		return response(500, `{"error_type":"INTERNAL_SERVER_ERROR"}`), nil
	})}
	res, err := guestcust.NewClient("http://127.0.0.1", sessionID, hc).
		ApplyProfile(context.Background(), validLinuxProfile())
	if err == nil {
		t.Fatal("ApplyProfile returned no error for a failed mutation")
	}
	if errors.Is(err, guestcust.ErrNotCustomizable) {
		t.Errorf("mutation failure was reported as a precheck refusal: %v", err)
	}
	if res.CheckStatus != "SUPPORTED" || res.Applied {
		t.Errorf("Result = %+v, want SUPPORTED and not applied", res)
	}
	if calls != 2 {
		t.Errorf("got %d requests, want one precheck and one mutation", calls)
	}
}

func TestAlternateHostnameAndIPv4ModesOmitIrrelevantValues(t *testing.T) {
	tests := []struct {
		hostMode string
		ipMode   string
	}{
		{"USER_INPUT_REQUIRED", "USER_INPUT_REQUIRED"},
		{"VIRTUAL_MACHINE", "DHCP"},
	}

	for _, tc := range tests {
		t.Run(tc.hostMode+"_"+tc.ipMode, func(t *testing.T) {
			p := validLinuxProfile()
			// GuestFamily is authoritative: an unrelated operator block is not an
			// API configuration and must stay out of the request.
			p.Windows = validWindowsProfile().Windows
			p.Hostname = guestcust.Hostname{Kind: tc.hostMode, Value: "must-not-be-sent"}
			p.NICs[0].IPv4 = guestcust.IPv4{
				Mode:     tc.ipMode,
				Address:  "192.0.2.10",
				Prefix:   24,
				Gateways: []string{"192.0.2.1"},
			}

			calls := 0
			var setBody []byte
			hc := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
				calls++
				if calls == 1 {
					return response(200, `{"check_status":"SUPPORTED"}`), nil
				}
				var err error
				setBody, err = io.ReadAll(req.Body)
				if err != nil {
					return nil, err
				}
				return response(204, ""), nil
			})}
			res, err := guestcust.NewClient("http://127.0.0.1", sessionID, hc).
				ApplyProfile(context.Background(), p)
			if err != nil {
				t.Fatalf("ApplyProfile: %v", err)
			}
			if !res.Applied || calls != 2 {
				t.Fatalf("Result = %+v, requests = %d; want applied after two requests", res, calls)
			}

			want := fmt.Sprintf(`{"spec":{"configuration_spec":{"linux_config":{"hostname":{"type":%q},"domain":"corp.example.com"}},"global_dns_settings":{},"interfaces":[{"adapter":{"ipv4":{"type":%q}}}]}}`, tc.hostMode, tc.ipMode)
			var gotDoc, wantDoc any
			gotDec := json.NewDecoder(bytes.NewReader(setBody))
			gotDec.UseNumber()
			wantDec := json.NewDecoder(strings.NewReader(want))
			wantDec.UseNumber()
			if err := gotDec.Decode(&gotDoc); err != nil {
				t.Fatalf("decode request body %q: %v", setBody, err)
			}
			if err := wantDec.Decode(&wantDoc); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(gotDoc, wantDoc) {
				t.Errorf("request body does not omit mode-irrelevant values\n got %s\nwant %s", setBody, want)
			}
		})
	}
}
