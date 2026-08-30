package wire

import (
	"net/http"
	"net/url"
	"strings"
	"testing"

	"vcfauto/mock"
)

func rec() mock.RecordedRequest {
	return mock.RecordedRequest{
		Operation: "deployments.list",
		Method:    http.MethodGet,
		Path:      "/deployment/api/deployments",
		Status:    http.StatusOK,
		Header:    http.Header{"Authorization": {"Bearer abc"}},
	}
}

func TestCheck(t *testing.T) {
	tests := []struct {
		name string
		got  func(mock.RecordedRequest) mock.RecordedRequest
		want Expectation
		// wantSubstr, when non-empty, must appear in the error. An empty
		// wantSubstr means the check must pass.
		wantSubstr string
	}{
		{
			name: "identical request passes",
			got:  func(r mock.RecordedRequest) mock.RecordedRequest { return r },
			want: Expectation{Operation: "deployments.list", Method: http.MethodGet,
				Path: "/deployment/api/deployments", Status: http.StatusOK},
		},
		{
			name: "wrong path",
			got:  func(r mock.RecordedRequest) mock.RecordedRequest { return r },
			want: Expectation{Path: "/deployment/api/deployment"},

			wantSubstr: "path:",
		},
		{
			name: "wrong operation",
			got:  func(r mock.RecordedRequest) mock.RecordedRequest { return r },
			want: Expectation{Operation: "catalog.items.list"},

			wantSubstr: "operation:",
		},
		{
			name: "parameter sent empty when none expected",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Query = url.Values{"search": {""}}
				return r
			},
			want:       Expectation{},
			wantSubstr: "must be omitted entirely rather than sent with an empty value",
		},
		{
			name: "parameter sent with a value when none expected",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Query = url.Values{"search": {"web"}}
				return r
			},
			want:       Expectation{},
			wantSubstr: "must not be sent at all",
		},
		{
			name: "parameter absent when one expected",
			got:  func(r mock.RecordedRequest) mock.RecordedRequest { return r },
			want: Expectation{Query: url.Values{"page": {"0"}}},

			wantSubstr: "is absent",
		},
		{
			name: "matching query passes",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Query = url.Values{"page": {"1"}, "size": {"4"}}
				return r
			},
			want: Expectation{Query: url.Values{"page": {"1"}, "size": {"4"}}},
		},
		{
			name: "body key sent empty when object should be bare",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte(`{"reason":""}`)
				return r
			},
			want:       Expectation{JSONBody: `{}`},
			wantSubstr: "must be omitted from the object, not sent empty",
		},
		{
			name: "body key sent null when object should be bare",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte(`{"reason":null}`)
				return r
			},
			want:       Expectation{JSONBody: `{}`},
			wantSubstr: "not sent null",
		},
		{
			name: "bare object matches bare object",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte(`{}`)
				return r
			},
			want: Expectation{JSONBody: `{}`},
		},
		{
			name: "body compares by value not by byte order",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte(`{"projectId":"p","deploymentName":"d"}`)
				return r
			},
			want: Expectation{JSONBody: `{"deploymentName":"d","projectId":"p"}`},
		},
		{
			name: "zero value that was deliberately set is kept",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte(`{"bulkRequestCount":0}`)
				return r
			},
			want: Expectation{JSONBody: `{"bulkRequestCount":0}`},
		},
		{
			name: "expected header absent",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Header = http.Header{}
				return r
			},
			want:       Expectation{Header: map[string]string{"Authorization": "Bearer abc"}},
			wantSubstr: "is absent",
		},
		{
			name: "header must not be present",
			got:  func(r mock.RecordedRequest) mock.RecordedRequest { return r },
			want: Expectation{AbsentHeaders: []string{"Authorization"}},

			wantSubstr: "must not be sent at all",
		},
		{
			name: "no body expected but one sent",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte(`{}`)
				return r
			},
			want:       Expectation{NoBody: true},
			wantSubstr: "want no body at all",
		},
		{
			name: "form body compared exactly",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte("grant_type=refresh_token&refresh_token=t")
				return r
			},
			want: Expectation{FormBody: url.Values{
				"grant_type": {"refresh_token"}, "refresh_token": {"t"}}},
		},
		{
			name: "form body with an extra field",
			got: func(r mock.RecordedRequest) mock.RecordedRequest {
				r.Body = []byte("grant_type=refresh_token&scope=")
				return r
			},
			want:       Expectation{FormBody: url.Values{"grant_type": {"refresh_token"}}},
			wantSubstr: "must be omitted entirely",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := Check(tc.got(rec()), tc.want)
			if tc.wantSubstr == "" {
				if err != nil {
					t.Fatalf("Check() = %v, want nil", err)
				}
				return
			}
			if err == nil {
				t.Fatalf("Check() = nil, want an error mentioning %q", tc.wantSubstr)
			}
			if !strings.Contains(err.Error(), tc.wantSubstr) {
				t.Fatalf("Check() = %v, want it to mention %q", err, tc.wantSubstr)
			}
		})
	}
}

func TestCheckReportsEveryDiscrepancy(t *testing.T) {
	r := rec()
	r.Query = url.Values{"search": {""}, "name": {"x"}}
	err := Check(r, Expectation{Operation: "catalog.items.list", Method: http.MethodPost})
	if err == nil {
		t.Fatal("Check() = nil, want an error")
	}
	for _, want := range []string{"operation:", "method:", "search", "name"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error does not mention %q: %v", want, err)
		}
	}
}

func TestCheckAll(t *testing.T) {
	tests := []struct {
		name    string
		got     []mock.RecordedRequest
		want    []Expectation
		wantErr bool
	}{
		{name: "empty matches empty"},
		{
			name: "length mismatch",
			got:  []mock.RecordedRequest{rec()},

			wantErr: true,
		},
		{
			name: "in order",
			got:  []mock.RecordedRequest{rec(), rec()},
			want: []Expectation{
				{Operation: "deployments.list"},
				{Operation: "deployments.list"},
			},
		},
		{
			name: "reports the index that differs",
			got:  []mock.RecordedRequest{rec(), rec()},
			want: []Expectation{
				{Operation: "deployments.list"},
				{Operation: "deployments.get"},
			},
			wantErr: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := CheckAll(tc.got, tc.want)
			if tc.wantErr != (err != nil) {
				t.Fatalf("CheckAll() = %v, wantErr %v", err, tc.wantErr)
			}
		})
	}
}
