package vcfmock

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"testing"
)

const contractSHA256 = "e92c164259ade10caeaacdf373eadd2c572f6491fa5ed6bbf9ea7e3f1d3b3d52"

type Operation struct {
	Name         string `json:"operation"`
	Method       string `json:"method"`
	PathTemplate string `json:"path_template"`
	matcher      *regexp.Regexp
}

type contract struct {
	Operations []Operation `json:"operations"`
}

type LoggedRequest struct {
	Operation     string
	Method        string
	Path          string
	RawQuery      string
	Authorization string
	ContentType   string
	Accept        string
	Body          string
}

type Response struct {
	StatusCode int
	Body       string
}

type Server struct {
	testServer *httptest.Server
	mu         sync.Mutex
	requests   []LoggedRequest
}

func New(t testing.TB) *Server {
	t.Helper()
	return NewWithResponses(t, map[string]Response{
		"Submit Resource Action Request": {
			StatusCode: http.StatusConflict,
			Body:       `{"id":"req-power-9","status":"FAILED","details":"resource is busy"}`,
		},
	})
}

func NewWithResponses(t testing.TB, overrides map[string]Response) *Server {
	t.Helper()
	ops := loadPinnedContract(t)
	responses := map[string]Response{
		"Patch Deployment": {
			StatusCode: http.StatusOK,
			Body:       `{"id":"dep-42","name":"payments-prod-renamed"}`,
		},
		"Submit Deployment Action Request": {
			StatusCode: http.StatusOK,
			Body:       `{"id":"req-owner-7","status":"FINISHED"}`,
		},
		"Submit Resource Action Request": {
			StatusCode: http.StatusOK,
			Body:       `{"id":"req-power-9","status":"FINISHED"}`,
		},
	}
	for name, response := range overrides {
		if _, ok := responses[name]; !ok {
			t.Fatalf("response override names unknown operation %q", name)
		}
		responses[name] = response
	}
	s := &Server{}
	s.testServer = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read request body: %v", err)
			http.Error(w, "read body", http.StatusInternalServerError)
			return
		}

		opName := ""
		for _, op := range ops {
			if r.Method == op.Method && op.matcher.MatchString(r.URL.EscapedPath()) {
				opName = op.Name
				break
			}
		}

		s.mu.Lock()
		s.requests = append(s.requests, LoggedRequest{
			Operation:     opName,
			Method:        r.Method,
			Path:          r.URL.EscapedPath(),
			RawQuery:      r.URL.RawQuery,
			Authorization: r.Header.Get("Authorization"),
			ContentType:   r.Header.Get("Content-Type"),
			Accept:        r.Header.Get("Accept"),
			Body:          string(body),
		})
		s.mu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		response, ok := responses[opName]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			_, _ = io.WriteString(w, `{"message":"operation is not in the pinned contract"}`)
			return
		}
		w.WriteHeader(response.StatusCode)
		_, _ = io.WriteString(w, response.Body)
	}))
	t.Cleanup(s.testServer.Close)
	return s
}

func (s *Server) URL() string { return s.testServer.URL }

func (s *Server) Client() *http.Client { return s.testServer.Client() }

func (s *Server) Requests() []LoggedRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]LoggedRequest(nil), s.requests...)
}

func loadPinnedContract(t testing.TB) []Operation {
	t.Helper()
	_, sourceFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate mock source")
	}
	contractPath := filepath.Join(filepath.Dir(sourceFile), "..", "..", "docs", "contract.json")
	b, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read pinned contract: %v", err)
	}
	sum := sha256.Sum256(b)
	if got := hex.EncodeToString(sum[:]); got != contractSHA256 {
		t.Fatalf("docs/contract.json checksum = %s, want %s", got, contractSHA256)
	}
	var c contract
	if err := json.Unmarshal(b, &c); err != nil {
		t.Fatalf("decode pinned contract: %v", err)
	}
	want := map[string]bool{
		"Patch Deployment":                 false,
		"Submit Deployment Action Request": false,
		"Submit Resource Action Request":   false,
	}
	if len(c.Operations) != len(want) {
		t.Fatalf("pinned contract has %d operations, want %d", len(c.Operations), len(want))
	}
	for i := range c.Operations {
		op := &c.Operations[i]
		if _, ok := want[op.Name]; !ok {
			t.Fatalf("unexpected operation in pinned contract: %q", op.Name)
		}
		want[op.Name] = true
		op.matcher = regexp.MustCompile(pathRegexp(op.PathTemplate))
	}
	for name, found := range want {
		if !found {
			t.Fatalf("operation %q missing from pinned contract", name)
		}
	}
	return c.Operations
}

func pathRegexp(pathTemplate string) string {
	quoted := regexp.QuoteMeta(pathTemplate)
	quoted = strings.ReplaceAll(quoted, `\{deploymentId\}`, `[^/]+`)
	quoted = strings.ReplaceAll(quoted, `\{resourceId\}`, `[^/]+`)
	return fmt.Sprintf("^%s$", quoted)
}
