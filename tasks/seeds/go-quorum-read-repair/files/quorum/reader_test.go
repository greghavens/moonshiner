package quorum

import (
	"context"
	"errors"
	"reflect"
	"sync"
	"testing"
)

type repairCall struct {
	key    string
	record Record
}

type scriptedReplica struct {
	record  Record
	readErr error
	started chan struct{}
	release chan struct{}

	mu      sync.Mutex
	repairs []repairCall
}

func (r *scriptedReplica) Read(ctx context.Context, key string) (Record, error) {
	if r.started != nil {
		close(r.started)
	}
	if r.release != nil {
		<-r.release
	}
	select {
	case <-ctx.Done():
		return Record{}, ctx.Err()
	default:
		return r.record, r.readErr
	}
}

func (r *scriptedReplica) Repair(ctx context.Context, key string, record Record) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.repairs = append(r.repairs, repairCall{key: key, record: record})
	return nil
}

func (r *scriptedReplica) repairSnapshot() []repairCall {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]repairCall(nil), r.repairs...)
}

func newTestReader(t *testing.T, quorum int, scripts ...*scriptedReplica) *Reader {
	t.Helper()
	replicas := make([]Replica, len(scripts))
	for index := range scripts {
		replicas[index] = scripts[index]
	}
	reader, err := NewReader(replicas, quorum)
	if err != nil {
		t.Fatalf("NewReader() error = %v", err)
	}
	return reader
}

func TestReadBreaksConfirmedTiesDeterministically(t *testing.T) {
	alpha1 := &scriptedReplica{record: Record{Version: 7, Value: "alpha"}}
	alpha2 := &scriptedReplica{record: Record{Version: 7, Value: "alpha"}}
	bravo1 := &scriptedReplica{record: Record{Version: 7, Value: "bravo"}}
	bravo2 := &scriptedReplica{record: Record{Version: 7, Value: "bravo"}}
	reader := newTestReader(t, 2, alpha1, bravo1, alpha2, bravo2)

	got, err := reader.Read(context.Background(), "account/4")
	if err != nil {
		t.Fatalf("Read() error = %v", err)
	}
	want := Record{Version: 7, Value: "bravo"}
	if got != want {
		t.Fatalf("Read() = %#v, want %#v", got, want)
	}

	wantRepair := []repairCall{{key: "account/4", record: want}}
	for name, replica := range map[string]*scriptedReplica{"alpha1": alpha1, "alpha2": alpha2} {
		if repairs := replica.repairSnapshot(); !reflect.DeepEqual(repairs, wantRepair) {
			t.Errorf("%s repairs = %#v, want %#v", name, repairs, wantRepair)
		}
	}
	for name, replica := range map[string]*scriptedReplica{"bravo1": bravo1, "bravo2": bravo2} {
		if repairs := replica.repairSnapshot(); len(repairs) != 0 {
			t.Errorf("%s repairs = %#v, want none", name, repairs)
		}
	}
}

func TestReadSelectsGreatestOfMultipleConfirmedVersions(t *testing.T) {
	older := Record{Version: 6, Value: "zulu"}
	newer := Record{Version: 7, Value: "alpha"}
	older1 := &scriptedReplica{record: older}
	newer1 := &scriptedReplica{record: newer}
	older2 := &scriptedReplica{record: older}
	newer2 := &scriptedReplica{record: newer}
	reader := newTestReader(t, 2, older1, newer1, older2, newer2)

	got, err := reader.Read(context.Background(), "account/5")
	if err != nil {
		t.Fatalf("Read() error = %v", err)
	}
	if got != newer {
		t.Fatalf("Read() = %#v, want greatest confirmed %#v", got, newer)
	}
	wantRepair := []repairCall{{key: "account/5", record: newer}}
	for index, replica := range []*scriptedReplica{older1, older2} {
		if repairs := replica.repairSnapshot(); !reflect.DeepEqual(repairs, wantRepair) {
			t.Errorf("older replica %d repairs = %#v, want %#v", index, repairs, wantRepair)
		}
	}
	if len(newer1.repairSnapshot()) != 0 || len(newer2.repairSnapshot()) != 0 {
		t.Fatal("selected replicas must not be repaired")
	}
}

func TestReadRepairsOnlyStaleSuccessfulReplicas(t *testing.T) {
	current1 := &scriptedReplica{record: Record{Version: 5, Value: "current"}}
	stale := &scriptedReplica{record: Record{Version: 3, Value: "old"}}
	current2 := &scriptedReplica{record: Record{Version: 5, Value: "current"}}
	reader := newTestReader(t, 2, current1, stale, current2)

	got, err := reader.Read(context.Background(), "profile/9")
	if err != nil {
		t.Fatalf("Read() error = %v", err)
	}
	want := Record{Version: 5, Value: "current"}
	if got != want {
		t.Fatalf("Read() = %#v, want %#v", got, want)
	}
	if repairs := stale.repairSnapshot(); !reflect.DeepEqual(repairs, []repairCall{{key: "profile/9", record: want}}) {
		t.Fatalf("stale repairs = %#v", repairs)
	}
	if len(current1.repairSnapshot()) != 0 || len(current2.repairSnapshot()) != 0 {
		t.Fatal("current replicas must not be repaired")
	}
}

func TestReadToleratesPartialFailureWhenRecordHasQuorum(t *testing.T) {
	want := Record{Version: 11, Value: "ready"}
	current1 := &scriptedReplica{record: want}
	failed := &scriptedReplica{readErr: errors.New("replica unavailable")}
	stale := &scriptedReplica{record: Record{Version: 10, Value: "old"}}
	current2 := &scriptedReplica{record: want}
	reader := newTestReader(t, 2, current1, failed, stale, current2)

	got, err := reader.Read(context.Background(), "job/2")
	if err != nil {
		t.Fatalf("Read() error = %v", err)
	}
	if got != want {
		t.Fatalf("Read() = %#v, want %#v", got, want)
	}
	if repairs := failed.repairSnapshot(); len(repairs) != 0 {
		t.Fatalf("failed replica repairs = %#v, want none", repairs)
	}
	if repairs := stale.repairSnapshot(); !reflect.DeepEqual(repairs, []repairCall{{key: "job/2", record: want}}) {
		t.Fatalf("stale repairs = %#v", repairs)
	}
}

func TestReadReturnsNoQuorumWithoutRepair(t *testing.T) {
	first := &scriptedReplica{record: Record{Version: 1, Value: "one"}}
	second := &scriptedReplica{record: Record{Version: 2, Value: "two"}}
	failed := &scriptedReplica{readErr: errors.New("offline")}
	reader := newTestReader(t, 2, first, second, failed)

	_, err := reader.Read(context.Background(), "key")
	if !errors.Is(err, ErrNoQuorum) {
		t.Fatalf("Read() error = %v, want ErrNoQuorum", err)
	}
	for index, replica := range []*scriptedReplica{first, second, failed} {
		if repairs := replica.repairSnapshot(); len(repairs) != 0 {
			t.Errorf("replica %d repairs = %#v, want none", index, repairs)
		}
	}
}

func TestReadDoesNotCombineDifferentValuesAtOneVersion(t *testing.T) {
	alpha := &scriptedReplica{record: Record{Version: 4, Value: "alpha"}}
	bravo := &scriptedReplica{record: Record{Version: 4, Value: "bravo"}}
	reader := newTestReader(t, 2, alpha, bravo)

	_, err := reader.Read(context.Background(), "key")
	if !errors.Is(err, ErrNoQuorum) {
		t.Fatalf("Read() error = %v, want ErrNoQuorum", err)
	}
	if len(alpha.repairSnapshot()) != 0 || len(bravo.repairSnapshot()) != 0 {
		t.Fatal("different values at one version must not be combined or repaired")
	}
}

func TestReadHonorsCancellationWithoutRepair(t *testing.T) {
	first := &scriptedReplica{
		record:  Record{Version: 1, Value: "one"},
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
	second := &scriptedReplica{
		record:  Record{Version: 1, Value: "one"},
		started: make(chan struct{}),
		release: make(chan struct{}),
	}
	reader := newTestReader(t, 2, first, second)
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() {
		_, err := reader.Read(ctx, "key")
		result <- err
	}()
	<-first.started
	<-second.started
	cancel()

	err := <-result
	close(first.release)
	close(second.release)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Read() error = %v, want context.Canceled", err)
	}
	if len(first.repairSnapshot()) != 0 || len(second.repairSnapshot()) != 0 {
		t.Fatal("canceled read must not repair replicas")
	}
}

func TestReadDoesNotPropagateUnconfirmedHigherRecord(t *testing.T) {
	confirmed := Record{Version: 8, Value: "committed"}
	current1 := &scriptedReplica{record: confirmed}
	outlier := &scriptedReplica{record: Record{Version: 99, Value: "unconfirmed"}}
	stale := &scriptedReplica{record: Record{Version: 7, Value: "old"}}
	current2 := &scriptedReplica{record: confirmed}
	reader := newTestReader(t, 2, current1, outlier, stale, current2)

	got, err := reader.Read(context.Background(), "document/6")
	if err != nil {
		t.Fatalf("Read() error = %v", err)
	}
	if got != confirmed {
		t.Errorf("Read() = %#v, want quorum-confirmed %#v", got, confirmed)
	}
	if repairs := outlier.repairSnapshot(); len(repairs) != 0 {
		t.Fatalf("higher unconfirmed replica repairs = %#v, want none", repairs)
	}
	if repairs := stale.repairSnapshot(); !reflect.DeepEqual(repairs, []repairCall{{key: "document/6", record: confirmed}}) {
		t.Fatalf("stale repairs = %#v, want confirmed repair", repairs)
	}
	for index, replica := range []*scriptedReplica{current1, current2} {
		if repairs := replica.repairSnapshot(); len(repairs) != 0 {
			t.Errorf("current replica %d repairs = %#v, want none", index, repairs)
		}
	}
}
