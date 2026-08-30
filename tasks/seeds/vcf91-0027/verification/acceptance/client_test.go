package acceptance_test

import (
	"context"
	"fmt"
	"net/http"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	vcf "example.com/vcf91hosts"
	"example.com/vcf91hosts/internal/contractmock"
)

func boolPointer(value bool) *bool {
	return &value
}

func TestListAllHostsWirePaginationAndStableOrder(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		options     vcf.ListHostsOptions
		pages       [][]contractmock.Host
		wantQueries []string
		wantHosts   []vcf.Host
	}{
		{
			name:    "unset optional fields are absent across every page",
			options: vcf.ListHostsOptions{},
			pages: [][]contractmock.Host{
				{
					{ID: "host-3", FQDN: "zeta.example.test", Status: "ASSIGNED"},
					{ID: "host-2", FQDN: "alpha.example.test", Status: "ASSIGNED"},
				},
				{
					{ID: "host-1", FQDN: "alpha.example.test", Status: "UNASSIGNED_USEABLE"},
					{ID: "host-4", FQDN: "bravo.example.test", Status: "ASSIGNED"},
				},
			},
			wantQueries: []string{"pageNumber=0", "pageNumber=1"},
			wantHosts: []vcf.Host{
				{ID: "host-1", FQDN: "alpha.example.test", Status: "UNASSIGNED_USEABLE"},
				{ID: "host-2", FQDN: "alpha.example.test", Status: "ASSIGNED"},
				{ID: "host-4", FQDN: "bravo.example.test", Status: "ASSIGNED"},
				{ID: "host-3", FQDN: "zeta.example.test", Status: "ASSIGNED"},
			},
		},
		{
			name: "all current filters use exact spellings and false is retained",
			options: vcf.ListHostsOptions{
				PageSize:                   3,
				FQDN:                       "esx-01.example.test",
				Status:                     "ASSIGNED",
				DomainID:                   "domain blue",
				ClusterID:                  "cluster 1",
				NetworkPoolID:              "np-1",
				StorageType:                "VSAN_ESA",
				DatastoreName:              "datastore/blue",
				IPAddressVersionForVmotion: "IPv6",
				IsStandalone:               boolPointer(false),
				IsLifecycleManaged:         boolPointer(true),
				IsVsanWitnessHost:          boolPointer(false),
			},
			pages: [][]contractmock.Host{{
				{ID: "host-9", FQDN: "esx-01.example.test", Status: "ASSIGNED"},
			}},
			wantQueries: []string{
				"clusterId=cluster+1&datastoreName=datastore%2Fblue&domainId=domain+blue&fqdn=esx-01.example.test&ipAddressVersionForVmotion=IPv6&isLifecycleManaged=true&isStandalone=false&isVsanWitnessHost=false&networkpoolId=np-1&pageNumber=0&pageSize=3&status=ASSIGNED&storageType=VSAN_ESA",
			},
			wantHosts: []vcf.Host{{
				ID: "host-9", FQDN: "esx-01.example.test", Status: "ASSIGNED",
			}},
		},
		{
			name: "empty string options are omitted rather than sent empty",
			options: vcf.ListHostsOptions{
				FQDN:          "",
				Status:        "",
				DomainID:      "",
				ClusterID:     "",
				NetworkPoolID: "",
				StorageType:   "",
				DatastoreName: "",
			},
			pages: [][]contractmock.Host{{
				{ID: "host-5", FQDN: "echo.example.test", Status: "ASSIGNED"},
			}},
			wantQueries: []string{"pageNumber=0"},
			wantHosts: []vcf.Host{{
				ID: "host-5", FQDN: "echo.example.test", Status: "ASSIGNED",
			}},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.New(t, filepath.Join("..", "docs", "contract.json"), test.pages)
			client, err := vcf.NewClient(mock.URL()+"/", mock.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			got, err := client.ListAllHosts(context.Background(), test.options)
			if err != nil {
				t.Fatalf("ListAllHosts: %v", err)
			}
			if !reflect.DeepEqual(got, test.wantHosts) {
				t.Fatalf("stable complete inventory\n got: %#v\nwant: %#v", got, test.wantHosts)
			}

			requests := mock.Requests()
			if len(requests) != len(test.wantQueries) {
				t.Fatalf("request count = %d, want %d", len(requests), len(test.wantQueries))
			}
			wantHost := strings.TrimPrefix(mock.URL(), "http://")
			for i, request := range requests {
				assertExactWire(t, i, request, wantHost, test.wantQueries[i])
			}
		})
	}
}

func assertExactWire(t *testing.T, index int, got contractmock.Request, wantHost, wantQuery string) {
	t.Helper()

	wantHeaders := http.Header{
		"Accept":          []string{"application/json"},
		"Accept-Encoding": []string{"gzip"},
		"User-Agent":      []string{"Go-http-client/1.1"},
	}
	checks := []struct {
		name string
		got  any
		want any
	}{
		{name: "method", got: got.Method, want: http.MethodGet},
		{name: "path", got: got.Path, want: "/v1/hosts"},
		{name: "raw query", got: got.RawQuery, want: wantQuery},
		{name: "request URI", got: got.RequestURI, want: "/v1/hosts?" + wantQuery},
		{name: "host", got: got.Host, want: wantHost},
		{name: "headers", got: got.Header, want: wantHeaders},
		{name: "body", got: got.Body, want: ""},
		{name: "content length", got: got.ContentLength, want: int64(0)},
		{name: "transfer encoding", got: got.TransferEncoding, want: []string(nil)},
	}
	for _, check := range checks {
		if !reflect.DeepEqual(check.got, check.want) {
			t.Errorf("request %d %s\n got: %s\nwant: %s", index, check.name, formatValue(check.got), formatValue(check.want))
		}
	}
	if strings.Contains(got.RawQuery, "size=") || strings.Contains(got.RawQuery, "page=") {
		t.Errorf("request %d used a deprecated pagination parameter: %q", index, got.RawQuery)
	}
	if strings.Contains(got.RawQuery, "=&") || strings.HasSuffix(got.RawQuery, "=") {
		t.Errorf("request %d serialized an empty optional value: %q", index, got.RawQuery)
	}
}

func formatValue(value any) string {
	return fmt.Sprintf("%#v", value)
}
