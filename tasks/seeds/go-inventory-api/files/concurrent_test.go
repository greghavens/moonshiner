package inventory

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

// Optimistic concurrency is the whole point of the ETag scheme: when many
// writers race on the same If-Match generation, exactly one may win.

func TestConcurrentPutsExactlyOneWinner(t *testing.T) {
	srv := httptest.NewServer(NewServer(NewMemStore()))
	t.Cleanup(srv.Close)

	resp, _, err := doRaw("POST", srv.URL+"/items", nil, itemBody("RACE-1", "before", 1, 100))
	if err != nil || resp.StatusCode != http.StatusCreated {
		t.Fatalf("seed item: err=%v status=%v", err, resp.StatusCode)
	}

	const n = 20
	statuses := make([]int, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r, _, err := doRaw("PUT", srv.URL+"/items/RACE-1",
				map[string]string{"If-Match": `"1"`},
				itemBody("RACE-1", fmt.Sprintf("writer-%02d", i), i, 100+i))
			if err != nil {
				statuses[i] = -1
				return
			}
			statuses[i] = r.StatusCode
		}(i)
	}
	wg.Wait()

	var winners []int
	stale := 0
	for i, s := range statuses {
		switch s {
		case http.StatusOK:
			winners = append(winners, i)
		case http.StatusPreconditionFailed:
			stale++
		default:
			t.Fatalf("writer %d got unexpected status %d", i, s)
		}
	}
	if len(winners) != 1 || stale != n-1 {
		t.Fatalf("racing PUTs on one If-Match generation: %d winners and %d 412s, want exactly 1 and %d", len(winners), stale, n-1)
	}

	r2, raw := doReq(t, "GET", srv.URL+"/items/RACE-1", nil, "")
	if et := r2.Header.Get("ETag"); et != `"2"` {
		t.Fatalf("after exactly one winning PUT the ETag must be \"2\", got %q", et)
	}
	want := fmt.Sprintf("writer-%02d", winners[0])
	if m := asJSON(t, raw); m["name"] != want {
		t.Fatalf("stored item %v does not belong to the winning writer %q", m, want)
	}
}

func TestConcurrentCreatesSingleWinner(t *testing.T) {
	srv := httptest.NewServer(NewServer(NewMemStore()))
	t.Cleanup(srv.Close)

	const n = 10
	statuses := make([]int, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r, _, err := doRaw("POST", srv.URL+"/items", nil,
				itemBody("DUP-1", fmt.Sprintf("creator-%02d", i), i, 100))
			if err != nil {
				statuses[i] = -1
				return
			}
			statuses[i] = r.StatusCode
		}(i)
	}
	wg.Wait()

	created, conflicted := 0, 0
	winner := -1
	for i, s := range statuses {
		switch s {
		case http.StatusCreated:
			created++
			winner = i
		case http.StatusConflict:
			conflicted++
		default:
			t.Fatalf("creator %d got unexpected status %d", i, s)
		}
	}
	if created != 1 || conflicted != n-1 {
		t.Fatalf("racing creates of one sku: %d created / %d conflicted, want 1 / %d", created, conflicted, n-1)
	}

	_, raw := doReq(t, "GET", srv.URL+"/items/DUP-1", nil, "")
	want := fmt.Sprintf("creator-%02d", winner)
	if m := asJSON(t, raw); m["name"] != want {
		t.Fatalf("stored item %v does not belong to the winning creator %q", m, want)
	}
}
