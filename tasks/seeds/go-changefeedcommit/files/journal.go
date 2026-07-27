package changefeed

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"hash/crc32"
	"io"
)

const (
	recordCommit = "commit"
	recordOutbox = "outbox"
	recordState  = "state"
	recordAck    = "ack"
	maxRecordLen = 16 << 20
)

type diskRecord struct {
	Kind          string       `json:"kind"`
	BatchID       uint64       `json:"batch_id"`
	FirstSequence uint64       `json:"first_sequence,omitempty"`
	Mutations     []mutation   `json:"mutations,omitempty"`
	Events        []eventDraft `json:"events,omitempty"`
}

func (s *Store) replay() error {
	if _, err := s.file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	data, err := io.ReadAll(s.file)
	if err != nil {
		return err
	}

	valid := 0
	for valid < len(data) {
		if len(data)-valid < 8 {
			break
		}
		length := int(binary.BigEndian.Uint32(data[valid : valid+4]))
		wantCRC := binary.BigEndian.Uint32(data[valid+4 : valid+8])
		if length <= 0 || length > maxRecordLen || len(data)-valid-8 < length {
			break
		}
		payload := data[valid+8 : valid+8+length]
		if crc32.ChecksumIEEE(payload) != wantCRC {
			break
		}

		var record diskRecord
		if json.Unmarshal(payload, &record) != nil || !validRecord(record) {
			break
		}
		s.applyRecord(record)
		valid += 8 + length
	}

	if valid != len(data) {
		if err := s.file.Truncate(int64(valid)); err != nil {
			return err
		}
	}
	_, err = s.file.Seek(0, io.SeekEnd)
	return err
}

func validRecord(record diskRecord) bool {
	switch record.Kind {
	case recordCommit:
		if record.BatchID == 0 {
			return false
		}
		return validMutations(record.Mutations) && validEvents(record.Events)
	case recordOutbox:
		return record.BatchID != 0 && validEvents(record.Events)
	case recordState:
		return record.BatchID != 0 && validMutations(record.Mutations)
	case recordAck:
		return record.BatchID != 0 &&
			len(record.Mutations) == 0 && len(record.Events) == 0
	default:
		return false
	}
}

func validMutations(mutations []mutation) bool {
	for _, item := range mutations {
		if item.Key == "" {
			return false
		}
	}
	return true
}

func validEvents(events []eventDraft) bool {
	for _, item := range events {
		if item.Topic == "" || item.Key == "" {
			return false
		}
	}
	return true
}

func (s *Store) applyRecord(record diskRecord) {
	switch record.Kind {
	case recordCommit:
		s.applyMutations(record.Mutations)
		s.applyOutbox(record)
	case recordOutbox:
		s.applyOutbox(record)
	case recordState:
		s.applyMutations(record.Mutations)
	case recordAck:
		firstRemaining := 0
		for firstRemaining < len(s.pending) &&
			s.pending[firstRemaining].ID <= record.BatchID {
			firstRemaining++
		}
		s.pending = s.pending[firstRemaining:]
	}
}

func (s *Store) applyOutbox(record diskRecord) {
	if record.BatchID >= s.nextBatch {
		s.nextBatch = record.BatchID + 1
	}
	afterEvents := record.FirstSequence + uint64(len(record.Events))
	if afterEvents > s.nextSeq {
		s.nextSeq = afterEvents
	}
	if len(record.Events) != 0 {
		s.pending = append(s.pending, makeBatch(
			record.BatchID, record.FirstSequence, record.Events,
		))
	}
}

func (s *Store) appendRecordLocked(record diskRecord) error {
	payload, err := json.Marshal(record)
	if err != nil {
		return err
	}
	if len(payload) > maxRecordLen {
		return errors.New("changefeed: journal record too large")
	}

	start, err := s.file.Seek(0, io.SeekEnd)
	if err != nil {
		return err
	}
	frame := make([]byte, 8+len(payload))
	binary.BigEndian.PutUint32(frame[0:4], uint32(len(payload)))
	binary.BigEndian.PutUint32(frame[4:8], crc32.ChecksumIEEE(payload))
	copy(frame[8:], payload)

	if err := writeFull(s.file, frame); err != nil {
		_ = s.file.Truncate(start)
		_, _ = s.file.Seek(0, io.SeekEnd)
		return err
	}

	if s.opts.Sync != nil {
		err = s.opts.Sync()
	} else {
		err = s.file.Sync()
	}
	if err != nil {
		_ = s.file.Truncate(start)
		_, _ = s.file.Seek(0, io.SeekEnd)
		return err
	}
	return nil
}

func writeFull(writer io.Writer, data []byte) error {
	for len(data) != 0 {
		written, err := writer.Write(data)
		if err != nil {
			return err
		}
		if written == 0 {
			return io.ErrShortWrite
		}
		data = data[written:]
	}
	return nil
}
