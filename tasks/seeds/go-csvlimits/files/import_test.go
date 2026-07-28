package csvlimits

import (
	"context"
	"errors"
	"fmt"
	"io"
	"reflect"
	"runtime"
	"strings"
	"testing"
)

func generousLimits() Limits {
	return Limits{
		MaxRowBytes:    1024,
		MaxFields:      16,
		MaxFieldBytes:  512,
		MaxDiagnostics: 16,
	}
}

func TestQuotedLineEndingsRemainInsideFields(t *testing.T) {
	input := "\"left\nright\",one\r\n\"cr\r\nlf\",two\n"
	var rows [][]string

	got, err := Import(
		context.Background(),
		strings.NewReader(input),
		generousLimits(),
		func(_ context.Context, row []string) error {
			rows = append(rows, append([]string(nil), row...))
			return nil
		},
		nil,
	)
	if err != nil {
		t.Fatalf("Import returned error: %v", err)
	}
	wantRows := [][]string{
		{"left\nright", "one"},
		{"cr\r\nlf", "two"},
	}
	if !reflect.DeepEqual(rows, wantRows) {
		t.Fatalf("rows = %#v, want %#v", rows, wantRows)
	}
	if got.Accepted != 2 || got.Rejected != 0 {
		t.Fatalf("result = %+v, want 2 accepted and 0 rejected", got)
	}
}

func TestLimitDiagnosticsUseFirstExcessSourceByte(t *testing.T) {
	tests := []struct {
		name    string
		input   string
		limits  Limits
		want    Diagnostic
		wantRow []string
	}{
		{
			name:  "raw row bytes include quotes and quoted newline",
			input: "\"a\nb\",x\nok,z\n",
			limits: Limits{
				MaxRowBytes: 5, MaxFields: 8, MaxFieldBytes: 32, MaxDiagnostics: 8,
			},
			want: Diagnostic{
				Violation: RowBytes,
				Position:  Position{Record: 1, Line: 2, Column: 3},
			},
			wantRow: []string{"ok", "z"},
		},
		{
			name:  "decoded field byte",
			input: "\"a\nbc\",x\nok,z\n",
			limits: Limits{
				MaxRowBytes: 64, MaxFields: 8, MaxFieldBytes: 3, MaxDiagnostics: 8,
			},
			want: Diagnostic{
				Violation: FieldBytes,
				Position:  Position{Record: 1, Line: 2, Column: 2},
			},
			wantRow: []string{"ok", "z"},
		},
		{
			name:  "comma opens excessive field",
			input: "a,\"b\nc\",d\nok,z\n",
			limits: Limits{
				MaxRowBytes: 64, MaxFields: 2, MaxFieldBytes: 32, MaxDiagnostics: 8,
			},
			want: Diagnostic{
				Violation: FieldCount,
				Position:  Position{Record: 1, Line: 2, Column: 3},
			},
			wantRow: []string{"ok", "z"},
		},
		{
			name:  "row wins a same-byte tie",
			input: "a,b,c\nz\n",
			limits: Limits{
				MaxRowBytes: 3, MaxFields: 2, MaxFieldBytes: 32, MaxDiagnostics: 8,
			},
			want: Diagnostic{
				Violation: RowBytes,
				Position:  Position{Record: 1, Line: 1, Column: 4},
			},
			wantRow: []string{"z"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var rows [][]string
			result, err := Import(
				context.Background(),
				strings.NewReader(tt.input),
				tt.limits,
				func(_ context.Context, row []string) error {
					rows = append(rows, append([]string(nil), row...))
					return nil
				},
				nil,
			)
			if err != nil {
				t.Fatalf("Import returned error: %v", err)
			}
			if result.Rejected != 1 || result.Accepted != 1 {
				t.Fatalf("result = %+v, want one rejected and one accepted", result)
			}
			if !reflect.DeepEqual(result.Diagnostics, []Diagnostic{tt.want}) {
				t.Fatalf("diagnostics = %+v, want %+v", result.Diagnostics, []Diagnostic{tt.want})
			}
			if !reflect.DeepEqual(rows, [][]string{tt.wantRow}) {
				t.Fatalf("accepted rows = %#v, want trailing valid row %#v", rows, tt.wantRow)
			}
		})
	}
}

func TestEscapedQuoteChargesOneDecodedByteAtSecondQuote(t *testing.T) {
	limits := generousLimits()
	limits.MaxFieldBytes = 1
	input := "\"a\"\"b\",x\nz\n"

	result, err := Import(context.Background(), strings.NewReader(input), limits, nil, nil)
	if err != nil {
		t.Fatalf("Import returned error: %v", err)
	}
	want := []Diagnostic{{
		Violation: FieldBytes,
		Position:  Position{Record: 1, Line: 1, Column: 4},
	}}
	if !reflect.DeepEqual(result.Diagnostics, want) {
		t.Fatalf("diagnostics = %+v, want %+v", result.Diagnostics, want)
	}
	if result.Accepted != 1 || result.Rejected != 1 {
		t.Fatalf("result = %+v, want one accepted and one rejected", result)
	}
}

func TestDiagnosticRetentionIsBounded(t *testing.T) {
	const records = 2000
	var input strings.Builder
	for i := 0; i < records; i++ {
		input.WriteString("a,b,c\n")
	}
	limits := generousLimits()
	limits.MaxFields = 2
	limits.MaxDiagnostics = 3

	result, err := Import(context.Background(), strings.NewReader(input.String()), limits, nil, nil)
	if err != nil {
		t.Fatalf("Import returned error: %v", err)
	}
	if result.Accepted != 0 || result.Rejected != records {
		t.Fatalf("result = %+v, want %d rejected records", result, records)
	}
	if len(result.Diagnostics) != limits.MaxDiagnostics {
		t.Fatalf("retained %d diagnostics, want %d", len(result.Diagnostics), limits.MaxDiagnostics)
	}
	if result.Suppressed != records-limits.MaxDiagnostics {
		t.Fatalf("suppressed = %d, want %d", result.Suppressed, records-limits.MaxDiagnostics)
	}
	for i, diagnostic := range result.Diagnostics {
		want := Diagnostic{
			Violation: FieldCount,
			Position:  Position{Record: i + 1, Line: i + 1, Column: 4},
		}
		if diagnostic != want {
			t.Fatalf("diagnostic %d = %+v, want %+v", i, diagnostic, want)
		}
	}
}

func TestZeroDiagnosticBudgetRetainsNothing(t *testing.T) {
	limits := generousLimits()
	limits.MaxFields = 1
	limits.MaxDiagnostics = 0

	result, err := Import(context.Background(), strings.NewReader("a,b\nc,d\n"), limits, nil, nil)
	if err != nil {
		t.Fatalf("Import returned error: %v", err)
	}
	if result.Diagnostics == nil {
		t.Fatal("Diagnostics is nil; want a non-nil empty slice")
	}
	if len(result.Diagnostics) != 0 || result.Suppressed != 2 || result.Rejected != 2 {
		t.Fatalf("result = %+v, want 2 rejected and suppressed with no retained diagnostics", result)
	}
}

func TestMaximumDiagnosticBudgetIsValid(t *testing.T) {
	limits := generousLimits()
	limits.MaxDiagnostics = int(^uint(0) >> 1)

	result, err := Import(context.Background(), strings.NewReader(""), limits, nil, nil)
	if err != nil {
		t.Fatalf("Import returned error: %v", err)
	}
	if result.Diagnostics == nil {
		t.Fatal("Diagnostics is nil; want a non-nil empty slice")
	}
}

type cancelOnRead struct {
	cancel context.CancelCauseFunc
	data   []byte
	reads  int
}

func (r *cancelOnRead) Read(p []byte) (int, error) {
	r.reads++
	if r.reads > 1 {
		panic("source read again after cancellation")
	}
	r.cancel(errCanceledBySource)
	return copy(p, r.data), nil
}

var errCanceledBySource = errors.New("source canceled import")

func TestCancellationObservedBeforeProcessingNewlyReadBytes(t *testing.T) {
	ctx, cancel := context.WithCancelCause(context.Background())
	defer cancel(nil)
	source := &cancelOnRead{cancel: cancel, data: []byte("must,not,arrive\n")}
	called := false

	result, err := Import(
		ctx,
		source,
		generousLimits(),
		func(context.Context, []string) error {
			called = true
			return nil
		},
		func(int) {
			called = true
		},
	)
	if !errors.Is(err, errCanceledBySource) {
		t.Fatalf("error = %v, want cancellation cause %v", err, errCanceledBySource)
	}
	if called {
		t.Fatal("accept or progress called after cancellation became observable")
	}
	if result.Accepted != 0 || result.Rejected != 0 {
		t.Fatalf("result = %+v, want no completed records", result)
	}
	if source.reads != 1 {
		t.Fatalf("source reads = %d, want 1", source.reads)
	}
}

func TestAcceptedProgressFollowsSuccessfulAcceptance(t *testing.T) {
	errRejected := errors.New("sink rejected row")
	var offered [][]string
	var progress []int
	call := 0

	result, err := Import(
		context.Background(),
		strings.NewReader("first,row\nsecond,row\nthird,row\n"),
		generousLimits(),
		func(_ context.Context, row []string) error {
			call++
			offered = append(offered, append([]string(nil), row...))
			if call == 2 {
				return errRejected
			}
			return nil
		},
		func(accepted int) {
			progress = append(progress, accepted)
		},
	)
	if !errors.Is(err, errRejected) {
		t.Fatalf("error = %v, want sink error", err)
	}
	if result.Accepted != 1 || result.Rejected != 0 {
		t.Fatalf("result = %+v, want only the first row accepted", result)
	}
	if !reflect.DeepEqual(progress, []int{1}) {
		t.Fatalf("progress = %v, want [1]", progress)
	}
	wantOffered := [][]string{{"first", "row"}, {"second", "row"}}
	if !reflect.DeepEqual(offered, wantOffered) {
		t.Fatalf("offered = %#v, want %#v", offered, wantOffered)
	}
}

func TestCancellationAfterSuccessfulAcceptanceSkipsProgress(t *testing.T) {
	ctx, cancel := context.WithCancelCause(context.Background())
	defer cancel(nil)
	errCanceledByAcceptor := errors.New("acceptor canceled import")
	progressCalled := false
	offers := 0

	result, err := Import(
		ctx,
		strings.NewReader("accepted,row\nlater,row\n"),
		generousLimits(),
		func(context.Context, []string) error {
			offers++
			cancel(errCanceledByAcceptor)
			return nil
		},
		func(int) {
			progressCalled = true
		},
	)
	if !errors.Is(err, errCanceledByAcceptor) {
		t.Fatalf("error = %v, want cancellation cause %v", err, errCanceledByAcceptor)
	}
	if result.Accepted != 1 || result.Rejected != 0 {
		t.Fatalf("result = %+v, want the offered row accepted", result)
	}
	if offers != 1 {
		t.Fatalf("acceptor calls = %d, want 1", offers)
	}
	if progressCalled {
		t.Fatal("progress called after cancellation became observable")
	}
}

func TestLimitEnforcementDoesNotMaterializeHugeRecord(t *testing.T) {
	if testing.Short() {
		t.Skip("allocation boundary probe")
	}
	const payloadBytes = 8 << 20
	input := strings.Repeat("x", payloadBytes) + "\n"
	limits := Limits{
		MaxRowBytes:    32,
		MaxFields:      2,
		MaxFieldBytes:  16,
		MaxDiagnostics: 1,
	}

	runtime.GC()
	var before runtime.MemStats
	runtime.ReadMemStats(&before)
	result, err := Import(context.Background(), strings.NewReader(input), limits, nil, nil)
	var after runtime.MemStats
	runtime.ReadMemStats(&after)
	runtime.KeepAlive(input)

	if err != nil {
		t.Fatalf("Import returned error: %v", err)
	}
	if result.Rejected != 1 || result.Accepted != 0 {
		t.Fatalf("result = %+v, want one rejected record", result)
	}
	const allocationCeiling = 4 << 20
	if allocated := after.TotalAlloc - before.TotalAlloc; allocated > allocationCeiling {
		t.Fatalf(
			"Import allocated %d bytes for an over-limit record; hard limit enforcement must not materialize it (ceiling %d)",
			allocated,
			allocationCeiling,
		)
	}
}

func TestSyntaxErrorsHaveStablePhysicalPosition(t *testing.T) {
	_, err := Import(
		context.Background(),
		strings.NewReader("ok,row\r\nbad\"quote,x\n"),
		generousLimits(),
		nil,
		nil,
	)
	var syntaxErr *SyntaxError
	if !errors.As(err, &syntaxErr) {
		t.Fatalf("error = %v, want *SyntaxError", err)
	}
	want := Position{Record: 2, Line: 2, Column: 4}
	if syntaxErr.Position != want {
		t.Fatalf("syntax position = %+v, want %+v", syntaxErr.Position, want)
	}
}

func TestValidationAndReaderErrors(t *testing.T) {
	valid := generousLimits()
	tests := []struct {
		name   string
		ctx    context.Context
		src    io.Reader
		limits Limits
		want   error
	}{
		{name: "nil context", ctx: nil, src: strings.NewReader(""), limits: valid, want: ErrNilContext},
		{name: "nil reader", ctx: context.Background(), src: nil, limits: valid, want: ErrNilReader},
		{
			name: "zero row bytes", ctx: context.Background(), src: strings.NewReader(""),
			limits: Limits{MaxFields: 1, MaxFieldBytes: 1}, want: ErrInvalidLimits,
		},
		{
			name: "negative diagnostic budget", ctx: context.Background(), src: strings.NewReader(""),
			limits: Limits{MaxRowBytes: 1, MaxFields: 1, MaxFieldBytes: 1, MaxDiagnostics: -1},
			want:   ErrInvalidLimits,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := Import(tt.ctx, tt.src, tt.limits, nil, nil)
			if !errors.Is(err, tt.want) {
				t.Fatalf("error = %v, want errors.Is(_, %v)", err, tt.want)
			}
		})
	}

	errRead := errors.New("reader failed")
	result, err := Import(context.Background(), errorReader{err: errRead}, valid, nil, nil)
	if !errors.Is(err, errRead) {
		t.Fatalf("reader error = %v, want %v", err, errRead)
	}
	if result.Accepted != 0 || result.Rejected != 0 {
		t.Fatalf("result = %+v after reader error, want no completed records", result)
	}
}

type errorReader struct {
	err error
}

func (r errorReader) Read([]byte) (int, error) {
	return 0, r.err
}

func ExampleImport() {
	var rows [][]string
	result, err := Import(
		context.Background(),
		strings.NewReader("a,b\n\"c\nd\",e\n"),
		generousLimits(),
		func(_ context.Context, row []string) error {
			rows = append(rows, append([]string(nil), row...))
			return nil
		},
		func(accepted int) {
			fmt.Printf("accepted %d\n", accepted)
		},
	)
	fmt.Printf("result: %d accepted, %d rejected, err=%v\n", result.Accepted, result.Rejected, err)
	fmt.Printf("last row: %q\n", rows[len(rows)-1])
	// Output:
	// accepted 1
	// accepted 2
	// result: 2 accepted, 0 rejected, err=<nil>
	// last row: ["c\nd" "e"]
}
