package shortener

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
)

func TestConcurrentCreatesStayConsistent(t *testing.T) {
	srv, client := newTestServer(t)
	const n = 50

	type outcome struct {
		code string
		url  string
		err  error
	}
	results := make(chan outcome, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			url := fmt.Sprintf("https://example.com/doc/%d", i)
			resp, err := client.Post(srv.URL+"/api/links", "application/json",
				strings.NewReader(fmt.Sprintf(`{"url":%q}`, url)))
			if err != nil {
				results <- outcome{err: err}
				return
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusCreated {
				results <- outcome{err: fmt.Errorf("status %d", resp.StatusCode)}
				return
			}
			var body struct {
				Code string `json:"code"`
				URL  string `json:"url"`
			}
			if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
				results <- outcome{err: err}
				return
			}
			results <- outcome{code: body.Code, url: url}
		}(i)
	}
	wg.Wait()
	close(results)

	codes := map[string]string{}
	for r := range results {
		if r.err != nil {
			t.Fatalf("concurrent create failed: %v", r.err)
		}
		if prev, dup := codes[r.code]; dup {
			t.Fatalf("code %q issued twice (%q and %q)", r.code, prev, r.url)
		}
		codes[r.code] = r.url
	}
	if len(codes) != n {
		t.Fatalf("got %d distinct codes, want %d", len(codes), n)
	}

	// Every link must be retrievable with its own URL afterwards.
	for code, url := range codes {
		resp, err := client.Get(srv.URL + "/api/links/" + code)
		if err != nil {
			t.Fatal(err)
		}
		var body struct {
			URL string `json:"url"`
		}
		err = json.NewDecoder(resp.Body).Decode(&body)
		resp.Body.Close()
		if err != nil || resp.StatusCode != http.StatusOK || body.URL != url {
			t.Fatalf("stats for %s: status %d url %q err %v, want url %q", code, resp.StatusCode, body.URL, err, url)
		}
	}
}

func TestConcurrentRedirectsCountEveryHit(t *testing.T) {
	srv, client := newTestServer(t)
	resp, body := create(t, client, srv.URL, `{"url":"https://example.com/hot-path"}`)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create: %d", resp.StatusCode)
	}
	code := body["code"].(string)

	const hits = 60
	errs := make(chan error, hits)
	var wg sync.WaitGroup
	for i := 0; i < hits; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r, err := client.Get(srv.URL + "/r/" + code)
			if err != nil {
				errs <- err
				return
			}
			io.Copy(io.Discard, r.Body)
			r.Body.Close()
			if r.StatusCode != http.StatusFound {
				errs <- fmt.Errorf("redirect status %d", r.StatusCode)
				return
			}
			errs <- nil
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatalf("concurrent redirect failed: %v", err)
		}
	}

	_, stats := doJSON(t, client, http.MethodGet, srv.URL+"/api/links/"+code, "")
	if got, _ := stats["hits"].(float64); got != hits {
		t.Fatalf("hits = %v after %d concurrent redirects, want exactly %d (lost updates?)", stats["hits"], hits, hits)
	}
}
