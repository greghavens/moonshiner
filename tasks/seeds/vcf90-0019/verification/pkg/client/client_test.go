package client_test

import (
	"context"
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"testing"

	"vcfhosts/pkg/client"
	"vcfhosts/pkg/mock"
)

const (
	testUsername = "administrator@vsphere.local"
	testPassword = "VMw@re1!VMw@re1!"
	testToken    = "eyJhbGciOi.sddc-manager-access-token"

	contractPath = "../../docs/contract.json"
	fixturePath  = "../../testdata/hosts.json"

	mgmtDomainID = "d1a5c0e0-0001-4c9a-9b21-1a2b3c4d5e01"
)

// emitOrder is the whole inventory, ascending by fqdn.
var emitOrder = []string{
	"5f2c9b10-0003-4d3a-8e5f-0a1b2c3d4e03", // ESXi-05.vrack.vsphere.local
	"5f2c9b10-0004-4d3a-8e5f-0a1b2c3d4e04", // ESXi-06.vrack.vsphere.local
	"5f2c9b10-0005-4d3a-8e5f-0a1b2c3d4e05", // esx-mgmt-01.vrack.vsphere.local
	"5f2c9b10-0001-4d3a-8e5f-0a1b2c3d4e01", // esxi-01.vrack.vsphere.local
	"5f2c9b10-0002-4d3a-8e5f-0a1b2c3d4e02", // esxi-02.vrack.vsphere.local
	"5f2c9b10-0006-4d3a-8e5f-0a1b2c3d4e06", // esxi-03.vrack.vsphere.local
	"5f2c9b10-0008-4d3a-8e5f-0a1b2c3d4e08", // esxi-04.vrack.vsphere.local
	"5f2c9b10-0011-4d3a-8e5f-0a1b2c3d4e11", // esxi-10.vrack.vsphere.local
	"5f2c9b10-0007-4d3a-8e5f-0a1b2c3d4e07", // esxi-11.vrack.vsphere.local
	"5f2c9b10-0010-4d3a-8e5f-0a1b2c3d4e10", // esxi-12.vrack.vsphere.local
	"5f2c9b10-0012-4d3a-8e5f-0a1b2c3d4e12", // esxi-20.vrack.vsphere.local
	"5f2c9b10-0013-4d3a-8e5f-0a1b2c3d4e13", // esxi-21.vrack.vsphere.local
	"5f2c9b10-0014-4d3a-8e5f-0a1b2c3d4e14", // esxi-30.vrack.vsphere.local
	"5f2c9b10-0009-4d3a-8e5f-0a1b2c3d4e09", // esxi-9.vrack.vsphere.local
}

func newServer(t *testing.T, repeatBoundary bool) *mock.Server {
	t.Helper()
	srv, err := mock.New(mock.Config{
		ContractPath:   contractPath,
		FixturePath:    fixturePath,
		Username:       testUsername,
		Password:       testPassword,
		AccessToken:    testToken,
		RepeatBoundary: repeatBoundary,
	})
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(srv.Close)
	return srv
}

func newClient(t *testing.T, srv *mock.Server, user, pass string) *client.Client {
	t.Helper()
	c, err := client.New(client.Config{BaseURL: srv.URL(), Username: user, Password: pass})
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}
	return c
}

func ids(hosts []client.Host) []string {
	out := make([]string, 0, len(hosts))
	for _, h := range hosts {
		out = append(out, h.ID)
	}
	return out
}

func TestNewValidatesItsConfiguration(t *testing.T) {
	cases := []struct {
		name    string
		cfg     client.Config
		wantErr bool
	}{
		{"complete", client.Config{BaseURL: "http://127.0.0.1:8080", Username: "u", Password: "p"}, false},
		{"no base url", client.Config{Username: "u", Password: "p"}, true},
		{"blank base url", client.Config{BaseURL: "   ", Username: "u", Password: "p"}, true},
		{"no username", client.Config{BaseURL: "http://127.0.0.1:8080", Password: "p"}, true},
		{"no password", client.Config{BaseURL: "http://127.0.0.1:8080", Username: "u"}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := client.New(tc.cfg)
			if tc.wantErr && err == nil {
				t.Fatal("expected an error")
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
		})
	}
}

func TestListHostsBuildsTheQueryString(t *testing.T) {
	cases := []struct {
		name      string
		filter    client.Filter
		wantQuery []string
		wantIDs   []string
	}{
		{
			name:      "no filter and no page size",
			filter:    client.Filter{},
			wantQuery: []string{"page=0"},
			wantIDs:   emitOrder,
		},
		{
			name:      "page size only",
			filter:    client.Filter{PageSize: 6},
			wantQuery: []string{"page=0&size=6", "page=1&size=6", "page=2&size=6"},
			wantIDs:   emitOrder,
		},
		{
			name:      "one filter",
			filter:    client.Filter{DomainID: mgmtDomainID, PageSize: 10},
			wantQuery: []string{"domainId=" + mgmtDomainID + "&page=0&size=10"},
			wantIDs: []string{
				"5f2c9b10-0003-4d3a-8e5f-0a1b2c3d4e03",
				"5f2c9b10-0004-4d3a-8e5f-0a1b2c3d4e04",
				"5f2c9b10-0005-4d3a-8e5f-0a1b2c3d4e05",
				"5f2c9b10-0001-4d3a-8e5f-0a1b2c3d4e01",
				"5f2c9b10-0002-4d3a-8e5f-0a1b2c3d4e02",
			},
		},
		{
			name:      "unassigned hosts have no domain or cluster",
			filter:    client.Filter{Status: "UNASSIGNED_USEABLE"},
			wantQuery: []string{"page=0&status=UNASSIGNED_USEABLE"},
			wantIDs: []string{
				"5f2c9b10-0012-4d3a-8e5f-0a1b2c3d4e12",
				"5f2c9b10-0013-4d3a-8e5f-0a1b2c3d4e13",
			},
		},
		{
			name:      "nothing matches",
			filter:    client.Filter{FQDN: "esxi-99.vrack.vsphere.local"},
			wantQuery: []string{"fqdn=esxi-99.vrack.vsphere.local&page=0"},
			wantIDs:   []string{},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newServer(t, false)
			c := newClient(t, srv, testUsername, testPassword)

			got, err := c.ListHosts(context.Background(), tc.filter)
			if err != nil {
				t.Fatalf("ListHosts: %v", err)
			}
			if gotIDs := ids(got); !equal(gotIDs, tc.wantIDs) {
				t.Fatalf("hosts = %v, want %v", gotIDs, tc.wantIDs)
			}

			log := srv.Requests()
			var queries []string
			for _, r := range log {
				if r.OperationID == "getHosts" {
					queries = append(queries, r.RawQuery)
				}
			}
			if !equal(queries, tc.wantQuery) {
				t.Fatalf("query strings = %v, want %v", queries, tc.wantQuery)
			}
			for _, q := range queries {
				if strings.Contains(q, "=&") || strings.HasSuffix(q, "=") {
					t.Errorf("query %q carries an empty value", q)
				}
			}
		})
	}
}

func TestSignInBodyCarriesOnlyTheCredentialsInUse(t *testing.T) {
	srv := newServer(t, false)
	c := newClient(t, srv, testUsername, testPassword)

	if _, err := c.ListHosts(context.Background(), client.Filter{}); err != nil {
		t.Fatalf("ListHosts: %v", err)
	}

	log := srv.Requests()
	if len(log) == 0 || log[0].OperationID != "createToken" {
		t.Fatalf("first request = %+v, want createToken", log[0])
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(log[0].Body, &fields); err != nil {
		t.Fatalf("sign in body %q: %v", string(log[0].Body), err)
	}
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if !equal(keys, []string{"password", "username"}) {
		t.Fatalf("sign in body fields = %v, want exactly [password username]", keys)
	}
	if got := log[0].Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("sign in Content-Type = %q, want application/json", got)
	}
}

func TestListHostsSweepsEveryPageOnce(t *testing.T) {
	srv := newServer(t, true)
	c := newClient(t, srv, testUsername, testPassword)

	got, err := c.ListHosts(context.Background(), client.Filter{PageSize: 4})
	if err != nil {
		t.Fatalf("ListHosts: %v", err)
	}
	if gotIDs := ids(got); !equal(gotIDs, emitOrder) {
		t.Fatalf("hosts = %v, want %v", gotIDs, emitOrder)
	}

	seen := make(map[string]int)
	for _, h := range got {
		seen[h.ID]++
	}
	for id, n := range seen {
		if n != 1 {
			t.Errorf("host %s emitted %d times, want once", id, n)
		}
	}

	var pages []string
	for _, r := range srv.Requests() {
		if r.OperationID == "getHosts" {
			pages = append(pages, r.RawQuery)
		}
	}
	want := []string{"page=0&size=4", "page=1&size=4", "page=2&size=4", "page=3&size=4"}
	if !equal(pages, want) {
		t.Fatalf("pages = %v, want %v", pages, want)
	}
}

func TestListHostsMapsTheHostRecord(t *testing.T) {
	srv := newServer(t, false)
	c := newClient(t, srv, testUsername, testPassword)

	got, err := c.ListHosts(context.Background(), client.Filter{FQDN: "esxi-01.vrack.vsphere.local"})
	if err != nil {
		t.Fatalf("ListHosts: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("hosts = %v, want one", ids(got))
	}
	h := got[0]
	cases := []struct {
		field string
		got   string
		want  string
	}{
		{"ID", h.ID, "5f2c9b10-0001-4d3a-8e5f-0a1b2c3d4e01"},
		{"FQDN", h.FQDN, "esxi-01.vrack.vsphere.local"},
		{"ESXiVersion", h.ESXiVersion, "9.0.0-24280767"},
		{"HardwareVendor", h.HardwareVendor, "Dell Inc."},
		{"HardwareModel", h.HardwareModel, "PowerEdge R650"},
		{"Status", h.Status, "ASSIGNED"},
		{"CompatibleStorageType", h.CompatibleStorageType, "VSAN"},
		{"DomainID", h.DomainID, mgmtDomainID},
		{"ClusterID", h.ClusterID, "c9f3b2a1-0001-4f1b-a7c3-4d5e6f708901"},
		{"NetworkPoolID", h.NetworkPoolID, "9b7e4d20-0001-41c8-8d55-6f7a8b9c0d01"},
	}
	for _, tc := range cases {
		if tc.got != tc.want {
			t.Errorf("%s = %q, want %q", tc.field, tc.got, tc.want)
		}
	}
	if !equal(h.IPAddresses, []string{"10.0.0.101", "10.0.4.101"}) {
		t.Errorf("IPAddresses = %v, want the two addresses of the fixture", h.IPAddresses)
	}
}

func TestListHostsSurfacesFailures(t *testing.T) {
	cases := []struct {
		name string
		user string
		pass string
	}{
		{"wrong password", testUsername, "not-the-password"},
		{"unknown user", "someone@vsphere.local", testPassword},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newServer(t, false)
			c := newClient(t, srv, tc.user, tc.pass)

			if _, err := c.ListHosts(context.Background(), client.Filter{}); err == nil {
				t.Fatal("ListHosts returned no error")
			}
			log := srv.Requests()
			if len(log) != 1 {
				t.Fatalf("log has %d entries, want 1: the sweep must stop at the failed sign in\n%v", len(log), log)
			}
			if log[0].Status != http.StatusBadRequest {
				t.Errorf("sign in answered %d, want 400", log[0].Status)
			}
		})
	}
}

func equal(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range want {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
