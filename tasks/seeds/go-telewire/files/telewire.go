// Package telewire encodes device telemetry readings into the line-based
// wire format consumed by the ingest tier.
//
// One reading per frame:
//
//	<ts>|<device>|<metric>|<value>|<ff>\n
//
// ts and value are decimal (negative allowed); the flags byte is exactly
// two lowercase hex digits. The device and metric fields are escaped so a
// frame is always one line containing exactly four unescaped pipes:
//
//	'\'  -> `\\`    '|'  -> `\|`    '\n' -> `\n`    '\r' -> `\r`
//
// AppendFrame appends the encoded frame to dst and returns the extended
// slice, so hot-path callers can reuse one buffer across frames.
package telewire

import (
	"errors"
	"fmt"
	"strings"
)

// Reading is one telemetry sample from a device.
type Reading struct {
	TS     int64 // unix milliseconds
	Device string
	Metric string
	Value  int64
	Flags  uint8
}

var (
	// ErrEmptyDevice is returned when a reading has no device id.
	ErrEmptyDevice = errors.New("telewire: empty device")
	// ErrEmptyMetric is returned when a reading has no metric name.
	ErrEmptyMetric = errors.New("telewire: empty metric")
)

func escape(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, "|", `\|`)
	s = strings.ReplaceAll(s, "\n", `\n`)
	s = strings.ReplaceAll(s, "\r", `\r`)
	return s
}

// AppendFrame appends one encoded frame for r to dst and returns the
// extended slice. On error dst is returned unchanged.
func AppendFrame(dst []byte, r Reading) ([]byte, error) {
	if r.Device == "" {
		return dst, ErrEmptyDevice
	}
	if r.Metric == "" {
		return dst, ErrEmptyMetric
	}
	frame := fmt.Sprintf("%d|%s|%s|%d|%02x\n",
		r.TS, escape(r.Device), escape(r.Metric), r.Value, r.Flags)
	return append(dst, frame...), nil
}
