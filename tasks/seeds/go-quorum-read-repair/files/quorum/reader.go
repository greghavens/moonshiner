package quorum

import (
	"context"
	"errors"
	"fmt"
)

// ErrNoQuorum is returned when no exact record is confirmed by enough
// replicas. Failed reads and conflicting records do not count toward a record's
// confirmation.
var ErrNoQuorum = errors.New("no read quorum")

// Record is the versioned value stored by a replica.
type Record struct {
	Version uint64
	Value   string
}

// Replica is the portion of a replica used by quorum reads and read repair.
// Repair must be safe to call with the record returned by Read.
type Replica interface {
	Read(ctx context.Context, key string) (Record, error)
	Repair(ctx context.Context, key string, record Record) error
}

// Reader performs quorum-confirmed reads over a fixed set of replicas.
type Reader struct {
	replicas []Replica
	quorum   int
}

// NewReader constructs a Reader. A quorum must be reachable and positive.
func NewReader(replicas []Replica, quorum int) (*Reader, error) {
	if quorum <= 0 || quorum > len(replicas) {
		return nil, fmt.Errorf("invalid quorum %d for %d replicas", quorum, len(replicas))
	}

	return &Reader{
		replicas: append([]Replica(nil), replicas...),
		quorum:   quorum,
	}, nil
}

type readResult struct {
	index  int
	record Record
	err    error
}

// Read obtains every replica's response, selects the greatest record confirmed
// by a quorum, and repairs successful replicas that returned an older record.
// Records are ordered first by version and then lexicographically by value.
// Repair errors are best-effort and do not change a successful read.
func (r *Reader) Read(ctx context.Context, key string) (Record, error) {
	if err := ctx.Err(); err != nil {
		return Record{}, err
	}

	results := make(chan readResult, len(r.replicas))
	for index, replica := range r.replicas {
		go func(index int, replica Replica) {
			record, err := replica.Read(ctx, key)
			results <- readResult{index: index, record: record, err: err}
		}(index, replica)
	}

	reads := make([]readResult, 0, len(r.replicas))
	for len(reads) < len(r.replicas) {
		select {
		case <-ctx.Done():
			return Record{}, ctx.Err()
		case result := <-results:
			reads = append(reads, result)
		}
	}

	selected, err := selectConfirmed(reads, r.quorum)
	if err != nil {
		return Record{}, err
	}

	for _, result := range reads {
		if err := ctx.Err(); err != nil {
			return Record{}, err
		}
		if result.err == nil && recordLess(result.record, selected) {
			_ = r.replicas[result.index].Repair(ctx, key, selected)
		}
	}

	return selected, nil
}

func selectConfirmed(reads []readResult, quorum int) (Record, error) {
	counts := make(map[Record]int)
	var selected Record
	haveSelection := false

	for _, result := range reads {
		if result.err != nil {
			continue
		}
		counts[result.record]++
		if !haveSelection || recordLess(selected, result.record) {
			selected = result.record
			haveSelection = true
		}
	}

	confirmed := false
	for record, count := range counts {
		if count < quorum {
			continue
		}
		confirmed = true
		if recordLess(selected, record) {
			selected = record
		}
	}

	if !confirmed {
		return Record{}, ErrNoQuorum
	}
	return selected, nil
}

func recordLess(left, right Record) bool {
	if left.Version != right.Version {
		return left.Version < right.Version
	}
	return left.Value < right.Value
}
