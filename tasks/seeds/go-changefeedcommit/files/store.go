package changefeed

import (
	"errors"
	"os"
	"sort"
	"sync"
)

var (
	ErrClosed     = errors.New("changefeed: store closed")
	ErrTxDone     = errors.New("changefeed: transaction is done")
	ErrConflict   = errors.New("changefeed: commit conflict")
	ErrEmptyKey   = errors.New("changefeed: empty key")
	ErrEmptyTopic = errors.New("changefeed: empty topic")
	ErrNoPending  = errors.New("changefeed: no pending batches")
	ErrOutOfOrder = errors.New("changefeed: batches must be acknowledged in order")
)

// Options controls commit fault injection and retry behavior.
type Options struct {
	// MaxRetries is the number of fresh attempts allowed after a conflict.
	// Negative values are treated as zero.
	MaxRetries int

	// BeforeCommit runs after a callback has staged work. ErrConflict asks
	// Update to retry; any other error aborts the update.
	BeforeCommit func(attempt int) error

	// Sync replaces the file durability barrier when non-nil.
	Sync func() error
}

type Event struct {
	Sequence uint64
	BatchID  uint64
	Index    int
	Topic    string
	Key      string
	Payload  []byte
}

type Batch struct {
	ID     uint64
	Events []Event
}

type mutation struct {
	Key    string `json:"key"`
	Value  []byte `json:"value,omitempty"`
	Delete bool   `json:"delete,omitempty"`
}

type eventDraft struct {
	Topic   string `json:"topic"`
	Key     string `json:"key"`
	Payload []byte `json:"payload,omitempty"`
}

type pendingValue struct {
	value   []byte
	deleted bool
}

type Store struct {
	mu sync.RWMutex

	file   *os.File
	opts   Options
	closed bool

	state     map[string][]byte
	pending   []Batch
	nextBatch uint64
	nextSeq   uint64
}

type Tx struct {
	mu sync.Mutex

	done      bool
	snapshot  map[string][]byte
	overlay   map[string]pendingValue
	mutations []mutation
	events    []eventDraft
}

func Open(path string, opts *Options) (*Store, error) {
	options := Options{}
	if opts != nil {
		options = *opts
	}
	if options.MaxRetries < 0 {
		options.MaxRetries = 0
	}

	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	store := &Store{
		file:      file,
		opts:      options,
		state:     make(map[string][]byte),
		nextBatch: 1,
		nextSeq:   1,
	}
	if err := store.replay(); err != nil {
		_ = file.Close()
		return nil, err
	}
	return store, nil
}

func (s *Store) Update(fn func(*Tx) error) error {
	for attempt := 1; ; attempt++ {
		tx, err := s.newTx()
		if err != nil {
			return err
		}

		callbackErr := fn(tx)
		mutations, events := tx.finish()
		if callbackErr != nil {
			return callbackErr
		}
		if len(mutations) == 0 && len(events) == 0 {
			return nil
		}

		err = s.commitAttempt(attempt, mutations, events)
		if errors.Is(err, ErrConflict) && attempt <= s.opts.MaxRetries {
			continue
		}
		return err
	}
}

func (s *Store) newTx() (*Tx, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.closed {
		return nil, ErrClosed
	}
	snapshot := make(map[string][]byte, len(s.state))
	for key, value := range s.state {
		snapshot[key] = cloneBytes(value)
	}
	return &Tx{
		snapshot: snapshot,
		overlay:  make(map[string]pendingValue),
	}, nil
}

func (tx *Tx) Put(key string, value []byte) error {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	if tx.done {
		return ErrTxDone
	}
	if key == "" {
		return ErrEmptyKey
	}
	value = cloneBytes(value)
	tx.mutations = append(tx.mutations, mutation{Key: key, Value: value})
	tx.overlay[key] = pendingValue{value: cloneBytes(value)}
	return nil
}

func (tx *Tx) Delete(key string) error {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	if tx.done {
		return ErrTxDone
	}
	if key == "" {
		return ErrEmptyKey
	}
	tx.mutations = append(tx.mutations, mutation{Key: key, Delete: true})
	tx.overlay[key] = pendingValue{deleted: true}
	return nil
}

func (tx *Tx) Get(key string) ([]byte, bool, error) {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	if tx.done {
		return nil, false, ErrTxDone
	}
	if pending, ok := tx.overlay[key]; ok {
		if pending.deleted {
			return nil, false, nil
		}
		return cloneBytes(pending.value), true, nil
	}
	value, ok := tx.snapshot[key]
	return cloneBytes(value), ok, nil
}

func (tx *Tx) Emit(topic, key string, payload []byte) error {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	if tx.done {
		return ErrTxDone
	}
	if topic == "" {
		return ErrEmptyTopic
	}
	if key == "" {
		return ErrEmptyKey
	}
	tx.events = append(tx.events, eventDraft{
		Topic: topic, Key: key, Payload: cloneBytes(payload),
	})
	return nil
}

func (tx *Tx) finish() ([]mutation, []eventDraft) {
	tx.mu.Lock()
	defer tx.mu.Unlock()
	tx.done = true

	mutations := make([]mutation, len(tx.mutations))
	for i, item := range tx.mutations {
		mutations[i] = item
		mutations[i].Value = cloneBytes(item.Value)
	}
	events := make([]eventDraft, len(tx.events))
	for i, item := range tx.events {
		events[i] = item
		events[i].Payload = cloneBytes(item.Payload)
	}
	return mutations, events
}

// commitAttempt is the legacy split-record implementation. It makes the
// outbox durable before the database record so relays can begin promptly.
func (s *Store) commitAttempt(attempt int, mutations []mutation, events []eventDraft) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return ErrClosed
	}

	batchID := s.nextBatch
	firstSequence := s.nextSeq
	outboxRecord := diskRecord{
		Kind:          recordOutbox,
		BatchID:       batchID,
		FirstSequence: firstSequence,
		Events:        events,
	}
	if err := s.appendRecordLocked(outboxRecord); err != nil {
		return err
	}
	s.nextBatch++
	s.nextSeq += uint64(len(events))

	if s.opts.BeforeCommit != nil {
		if err := s.opts.BeforeCommit(attempt); err != nil {
			return err
		}
	}

	stateRecord := diskRecord{
		Kind:      recordState,
		BatchID:   batchID,
		Mutations: mutations,
	}
	if err := s.appendRecordLocked(stateRecord); err != nil {
		return err
	}

	s.applyMutations(mutations)
	if len(events) != 0 {
		s.pending = append(s.pending, makeBatch(batchID, firstSequence, events))
	}
	return nil
}

func (s *Store) Get(key string) ([]byte, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	value, ok := s.state[key]
	return cloneBytes(value), ok
}

func (s *Store) Keys() []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	keys := make([]string, 0, len(s.state))
	for key := range s.state {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func (s *Store) Pending(maxBatches int) []Batch {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if maxBatches <= 0 {
		return []Batch{}
	}
	if maxBatches > len(s.pending) {
		maxBatches = len(s.pending)
	}
	result := make([]Batch, maxBatches)
	for i := range result {
		result[i] = cloneBatch(s.pending[i])
	}
	return result
}

func (s *Store) Ack(batchID uint64) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return ErrClosed
	}
	if len(s.pending) == 0 {
		return ErrNoPending
	}
	if s.pending[0].ID != batchID {
		return ErrOutOfOrder
	}
	if err := s.appendRecordLocked(diskRecord{
		Kind: recordAck, BatchID: batchID,
	}); err != nil {
		return err
	}
	s.pending = s.pending[1:]
	return nil
}

func (s *Store) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return nil
	}
	s.closed = true
	return s.file.Close()
}

func (s *Store) applyMutations(mutations []mutation) {
	for _, item := range mutations {
		if item.Delete {
			delete(s.state, item.Key)
			continue
		}
		s.state[item.Key] = cloneBytes(item.Value)
	}
}

func makeBatch(id, firstSequence uint64, drafts []eventDraft) Batch {
	batch := Batch{ID: id, Events: make([]Event, len(drafts))}
	for index, draft := range drafts {
		batch.Events[index] = Event{
			Sequence: firstSequence + uint64(index),
			BatchID:  id,
			Index:    index,
			Topic:    draft.Topic,
			Key:      draft.Key,
			Payload:  cloneBytes(draft.Payload),
		}
	}
	return batch
}

func cloneBatch(batch Batch) Batch {
	result := Batch{ID: batch.ID, Events: make([]Event, len(batch.Events))}
	copy(result.Events, batch.Events)
	for i := range result.Events {
		result.Events[i].Payload = cloneBytes(result.Events[i].Payload)
	}
	return result
}

func cloneBytes(value []byte) []byte {
	if value == nil {
		return nil
	}
	return append([]byte(nil), value...)
}
