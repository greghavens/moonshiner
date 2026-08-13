package verify

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"example.com/vcf90/gosc/guestcust"
	"example.com/vcf90/gosc/internal/mockvc"
)

const sessionID = "0f2c9d1e-4b1a-4a2f-9d55-1c1d0b0f77aa"

func loadProfile(t *testing.T, name string) *guestcust.Profile {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "profiles", name))
	if err != nil {
		t.Fatalf("read profile: %v", err)
	}
	p, err := guestcust.LoadProfile(raw)
	if err != nil {
		t.Fatalf("load profile %s: %v", name, err)
	}
	return p
}

func boolp(b bool) *bool { return &b }

// TestPrecheckGatesTheMutation is the whole point of the package: when the
// customization precheck refuses the virtual machine, nothing may be sent.
func TestPrecheckGatesTheMutation(t *testing.T) {
	tests := []struct {
		name         string
		checkStatus  int
		checkBody    any
		wantApplied  bool
		wantStatus   string
		wantReasons  []string
		wantGateErr  bool
		wantAnyError bool
		wantSetCalls int
	}{
		{
			name:         "supported applies",
			checkStatus:  200,
			checkBody:    mockvc.CheckInfo("SUPPORTED", boolp(true), boolp(true)),
			wantApplied:  true,
			wantStatus:   "SUPPORTED",
			wantReasons:  nil,
			wantSetCalls: 1,
		},
		{
			name:         "unsupported guest os blocks",
			checkStatus:  200,
			checkBody:    mockvc.CheckInfo("NOT_SUPPORTED", boolp(false), boolp(true)),
			wantApplied:  false,
			wantStatus:   "NOT_SUPPORTED",
			wantReasons:  []string{"supported_guest_os"},
			wantGateErr:  true,
			wantAnyError: true,
			wantSetCalls: 0,
		},
		{
			name:         "unsupported power state blocks",
			checkStatus:  200,
			checkBody:    mockvc.CheckInfo("NOT_SUPPORTED", boolp(true), boolp(false)),
			wantApplied:  false,
			wantStatus:   "NOT_SUPPORTED",
			wantReasons:  []string{"supported_power_state"},
			wantGateErr:  true,
			wantAnyError: true,
			wantSetCalls: 0,
		},
		{
			name:         "both signals blocked are reported sorted",
			checkStatus:  200,
			checkBody:    mockvc.CheckInfo("NOT_SUPPORTED", boolp(false), boolp(false)),
			wantApplied:  false,
			wantStatus:   "NOT_SUPPORTED",
			wantReasons:  []string{"supported_guest_os", "supported_power_state"},
			wantGateErr:  true,
			wantAnyError: true,
			wantSetCalls: 0,
		},
		{
			name:         "unsupported with the optional signals absent",
			checkStatus:  200,
			checkBody:    mockvc.CheckInfo("NOT_SUPPORTED", nil, nil),
			wantApplied:  false,
			wantStatus:   "NOT_SUPPORTED",
			wantReasons:  []string{"unspecified"},
			wantGateErr:  true,
			wantAnyError: true,
			wantSetCalls: 0,
		},
		{
			name:         "a failed precheck blocks",
			checkStatus:  503,
			checkBody:    map[string]any{"error_type": "SERVICE_UNAVAILABLE"},
			wantApplied:  false,
			wantAnyError: true,
			wantSetCalls: 0,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := mockvc.New(t, contractPath)
			srv.SetCheckInfo(tc.checkStatus, tc.checkBody)

			c := guestcust.NewClient(srv.URL(), sessionID, srv.Client())
			res, err := c.ApplyProfile(context.Background(), loadProfile(t, "linux-dhcp.json"))

			if tc.wantAnyError && err == nil {
				t.Fatal("ApplyProfile returned no error")
			}
			if !tc.wantAnyError && err != nil {
				t.Fatalf("ApplyProfile: %v", err)
			}
			if tc.wantGateErr && !errors.Is(err, guestcust.ErrNotCustomizable) {
				t.Errorf("error %v does not satisfy errors.Is(err, ErrNotCustomizable)", err)
			}
			if !tc.wantGateErr && errors.Is(err, guestcust.ErrNotCustomizable) {
				t.Errorf("error %v wrongly reports the precheck as the cause", err)
			}
			if res.Applied != tc.wantApplied {
				t.Errorf("Result.Applied = %v, want %v", res.Applied, tc.wantApplied)
			}
			if tc.wantStatus != "" && res.CheckStatus != tc.wantStatus {
				t.Errorf("Result.CheckStatus = %q, want %q", res.CheckStatus, tc.wantStatus)
			}
			if tc.wantReasons != nil && !reflect.DeepEqual(res.Reasons, tc.wantReasons) {
				t.Errorf("Result.Reasons = %v, want %v", res.Reasons, tc.wantReasons)
			}
			if tc.wantApplied && len(res.Reasons) != 0 {
				t.Errorf("Result.Reasons = %v, want none when the customization was applied", res.Reasons)
			}

			sets := srv.RequestsFor(opSet)
			if len(sets) != tc.wantSetCalls {
				t.Fatalf("%s was issued %d time(s), want %d — the precheck did not gate the mutation",
					opSet, len(sets), tc.wantSetCalls)
			}
			if checks := srv.RequestsFor(opCheck); len(checks) != 1 {
				t.Errorf("%s was issued %d time(s), want exactly 1", opCheck, len(checks))
			}
			for _, r := range srv.Requests() {
				if r.OperationID == "" {
					t.Errorf("request %s %s?%s matched no contracted operation (status %d)",
						r.Method, r.Path, r.RawQuery, r.Status)
				}
			}
		})
	}
}

// TestPrecheckRequestShape pins how the precheck itself goes over the wire.
func TestPrecheckRequestShape(t *testing.T) {
	srv := mockvc.New(t, contractPath)
	srv.SetCheckInfo(200, mockvc.CheckInfo("SUPPORTED", boolp(true), boolp(true)))

	p := loadProfile(t, "linux-static.json")
	c := guestcust.NewClient(srv.URL(), sessionID, srv.Client())
	if _, err := c.ApplyProfile(context.Background(), p); err != nil {
		t.Fatalf("ApplyProfile: %v", err)
	}

	reqs := srv.Requests()
	if len(reqs) != 2 {
		t.Fatalf("got %d requests, want exactly 2 (one precheck, one mutation)", len(reqs))
	}
	check := reqs[0]
	if check.OperationID != opCheck {
		t.Fatalf("the first request was %q, want %q — the precheck must come first", check.OperationID, opCheck)
	}

	tests := []struct {
		name string
		got  string
		want string
	}{
		{"method", check.Method, "POST"},
		{"path", check.Path, "/api/vcenter/vm/" + p.VM + "/guest/customization"},
		{"query", check.RawQuery, "action=check"},
		{"session header", check.Header.Get("vmware-api-session-id"), sessionID},
	}
	for _, tc := range tests {
		if tc.got != tc.want {
			t.Errorf("precheck %s = %q, want %q", tc.name, tc.got, tc.want)
		}
	}
	if len(check.Body) != 0 {
		t.Errorf("precheck carried a %d byte body; the operation takes none: %s", len(check.Body), check.Body)
	}
	if ct := check.Header.Get("Content-Type"); ct != "" {
		t.Errorf("precheck carried Content-Type %q; a bodyless request declares no content type", ct)
	}
	if check.Status != 200 {
		t.Errorf("the loopback vCenter answered the precheck with %d, want 200", check.Status)
	}
	if reqs[1].Status != 204 {
		t.Errorf("the loopback vCenter answered the mutation with %d, want 204: %s", reqs[1].Status, reqs[1].Body)
	}
}
