package grpcshift_test

import (
	"context"
	"errors"
	"os"
	"reflect"
	"testing"

	"go-grpc-api-shift/internal/generated/stockpb"
	"go-grpc-api-shift/internal/rpc"
	"go-grpc-api-shift/internal/stockserver"
)

type memoryRepository struct {
	records       map[string]stockserver.Record
	updates       map[string][]int32
	lookupErr     error
	snapshotErr   error
	lookupHook    func()
	snapshotHook  func()
	lookupCalls   int
	snapshotCalls int
}

func (r *memoryRepository) Lookup(_ context.Context, sku string) (stockserver.Record, error) {
	r.lookupCalls++
	if r.lookupHook != nil {
		r.lookupHook()
	}
	if r.lookupErr != nil {
		return stockserver.Record{}, r.lookupErr
	}
	record, ok := r.records[sku]
	if !ok {
		return stockserver.Record{}, stockserver.ErrMissing
	}
	return record, nil
}

func (r *memoryRepository) Snapshot(_ context.Context, sku string) ([]int32, error) {
	r.snapshotCalls++
	if r.snapshotErr != nil {
		return nil, r.snapshotErr
	}
	updates := append([]int32(nil), r.updates[sku]...)
	if r.snapshotHook != nil {
		r.snapshotHook()
	}
	return updates, nil
}

type auditInterceptor struct {
	methods []string
	actors  []string
	after   int
}

func (a *auditInterceptor) intercept(
	ctx context.Context,
	request any,
	info rpc.UnaryServerInfo,
	handler rpc.UnaryHandler,
) (any, error) {
	a.methods = append(a.methods, info.FullMethod)
	a.actors = append(a.actors, rpc.Actor(ctx))
	response, err := handler(ctx, request)
	a.after++
	return response, err
}

func fixtureRepository() *memoryRepository {
	return &memoryRepository{
		records: map[string]stockserver.Record{
			"SKU-RED": {SKU: "SKU-RED", Quantity: 7, Warehouse: "wh-north"},
			"SKU-BLUE": {SKU: "SKU-BLUE", Quantity: 19, Warehouse: "wh-south"},
		},
		updates: map[string][]int32{"SKU-RED": {7, 6, 4}},
	}
}

func registeredHost(repository stockserver.Repository, audit *auditInterceptor) *stockpb.Host {
	host := stockpb.NewHost()
	stockpb.RegisterStockServer(host, stockserver.New(repository), audit.intercept)
	return host
}

func TestUnaryInterceptorAndDynamicResponsesSurviveRegistration(t *testing.T) {
	repository := fixtureRepository()
	audit := &auditInterceptor{}
	host := registeredHost(repository, audit)
	ctx := rpc.WithActor(context.Background(), "fixture-operator")

	red, err := host.InvokeLookup(ctx, &stockpb.LookupRequest{Sku: "SKU-RED"})
	if err != nil {
		t.Fatal(err)
	}
	blue, err := host.InvokeLookup(ctx, &stockpb.LookupRequest{Sku: "SKU-BLUE"})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(red, &stockpb.LookupResponse{Sku: "SKU-RED", Quantity: 7, Warehouse: "wh-north"}) ||
		!reflect.DeepEqual(blue, &stockpb.LookupResponse{Sku: "SKU-BLUE", Quantity: 19, Warehouse: "wh-south"}) {
		t.Fatalf("responses red=%+v blue=%+v", red, blue)
	}
	wantMethods := []string{stockpb.StockLookupFullMethodName, stockpb.StockLookupFullMethodName}
	if !reflect.DeepEqual(audit.methods, wantMethods) ||
		!reflect.DeepEqual(audit.actors, []string{"fixture-operator", "fixture-operator"}) ||
		audit.after != 2 || repository.lookupCalls != 2 {
		t.Fatalf("methods=%v actors=%v after=%d lookups=%d", audit.methods, audit.actors, audit.after, repository.lookupCalls)
	}
}

func TestUnaryStatusCodesAndCausesRemainStable(t *testing.T) {
	t.Run("missing", func(t *testing.T) {
		repository := fixtureRepository()
		_, err := registeredHost(repository, &auditInterceptor{}).
			InvokeLookup(context.Background(), &stockpb.LookupRequest{Sku: "SKU-MISSING"})
		if rpc.CodeOf(err) != rpc.CodeNotFound || !errors.Is(err, stockserver.ErrMissing) {
			t.Fatalf("error=%v code=%s", err, rpc.CodeOf(err))
		}
	})

	t.Run("repository", func(t *testing.T) {
		cause := errors.New("fixture repository offline")
		repository := fixtureRepository()
		repository.lookupErr = cause
		_, err := registeredHost(repository, &auditInterceptor{}).
			InvokeLookup(context.Background(), &stockpb.LookupRequest{Sku: "SKU-RED"})
		if rpc.CodeOf(err) != rpc.CodeInternal || !errors.Is(err, cause) {
			t.Fatalf("error=%v code=%s", err, rpc.CodeOf(err))
		}
	})

	t.Run("already-cancelled", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		repository := fixtureRepository()
		_, err := registeredHost(repository, &auditInterceptor{}).
			InvokeLookup(ctx, &stockpb.LookupRequest{Sku: "SKU-RED"})
		if rpc.CodeOf(err) != rpc.CodeCanceled || !errors.Is(err, context.Canceled) || repository.lookupCalls != 0 {
			t.Fatalf("error=%v code=%s calls=%d", err, rpc.CodeOf(err), repository.lookupCalls)
		}
	})

	t.Run("cancelled-during-repository-call", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		repository := fixtureRepository()
		repository.lookupHook = cancel
		repository.lookupErr = context.Canceled
		audit := &auditInterceptor{}
		_, err := registeredHost(repository, audit).
			InvokeLookup(ctx, &stockpb.LookupRequest{Sku: "SKU-RED"})
		if rpc.CodeOf(err) != rpc.CodeCanceled || !errors.Is(err, context.Canceled) ||
			repository.lookupCalls != 1 || audit.after != 1 {
			t.Fatalf("error=%v code=%s calls=%d interceptor-after=%d", err,
				rpc.CodeOf(err), repository.lookupCalls, audit.after)
		}
	})
}

type scriptedStream struct {
	ctx         context.Context
	cancel      context.CancelFunc
	updates     []*stockpb.StockUpdate
	cancelAfter int
	failAt      int
	failErr     error
}

func newStream() *scriptedStream {
	ctx, cancel := context.WithCancel(context.Background())
	return &scriptedStream{ctx: ctx, cancel: cancel}
}

func (s *scriptedStream) Context() context.Context { return s.ctx }

func (s *scriptedStream) Send(update *stockpb.StockUpdate) error {
	s.updates = append(s.updates, update)
	if s.cancelAfter > 0 && len(s.updates) == s.cancelAfter {
		s.cancel()
	}
	if s.failAt > 0 && len(s.updates) == s.failAt {
		return s.failErr
	}
	return nil
}

func TestStreamUsesItsContextAndStopsAfterCancellation(t *testing.T) {
	repository := fixtureRepository()
	repository.updates["SKU-RED"] = []int32{7}
	host := registeredHost(repository, &auditInterceptor{})
	stream := newStream()
	stream.cancelAfter = 1
	err := host.InvokeWatch(&stockpb.WatchRequest{Sku: "SKU-RED"}, stream)
	if rpc.CodeOf(err) != rpc.CodeCanceled || !errors.Is(err, context.Canceled) {
		t.Fatalf("error=%v code=%s", err, rpc.CodeOf(err))
	}
	want := []*stockpb.StockUpdate{{Sku: "SKU-RED", Quantity: 7, Sequence: 1}}
	if !reflect.DeepEqual(stream.updates, want) || repository.snapshotCalls != 1 {
		t.Fatalf("updates=%+v snapshot calls=%d", stream.updates, repository.snapshotCalls)
	}
}

func TestInitiallyCanceledStreamDoesNotReachTheRepository(t *testing.T) {
	repository := fixtureRepository()
	stream := newStream()
	stream.cancel()
	err := registeredHost(repository, &auditInterceptor{}).
		InvokeWatch(&stockpb.WatchRequest{Sku: "SKU-RED"}, stream)
	if rpc.CodeOf(err) != rpc.CodeCanceled || !errors.Is(err, context.Canceled) {
		t.Fatalf("error=%v code=%s", err, rpc.CodeOf(err))
	}
	if repository.snapshotCalls != 0 || len(stream.updates) != 0 {
		t.Fatalf("snapshot calls=%d updates=%v", repository.snapshotCalls, stream.updates)
	}
}

func TestCancellationAfterSuccessfulEmptySnapshotIsStillCausal(t *testing.T) {
	repository := fixtureRepository()
	stream := newStream()
	repository.snapshotHook = stream.cancel
	err := registeredHost(repository, &auditInterceptor{}).
		InvokeWatch(&stockpb.WatchRequest{Sku: "SKU-EMPTY"}, stream)
	if rpc.CodeOf(err) != rpc.CodeCanceled || !errors.Is(err, context.Canceled) {
		t.Fatalf("error=%v code=%s", err, rpc.CodeOf(err))
	}
	if repository.snapshotCalls != 1 || len(stream.updates) != 0 {
		t.Fatalf("snapshot calls=%d updates=%v", repository.snapshotCalls, stream.updates)
	}
}

func TestNonCancellationStreamStatusIsNotRemapped(t *testing.T) {
	repository := fixtureRepository()
	stream := newStream()
	stream.failAt = 2
	stream.failErr = rpc.Status(rpc.CodeUnavailable, "fixture receiver unavailable", nil)
	err := registeredHost(repository, &auditInterceptor{}).
		InvokeWatch(&stockpb.WatchRequest{Sku: "SKU-RED"}, stream)
	if rpc.CodeOf(err) != rpc.CodeUnavailable || !errors.Is(err, stream.failErr) {
		t.Fatalf("error=%v code=%s", err, rpc.CodeOf(err))
	}
	if len(stream.updates) != 2 {
		t.Fatalf("send attempts=%d", len(stream.updates))
	}
}

func TestSnapshotFailureIsInternalAndCausal(t *testing.T) {
	cause := errors.New("fixture snapshot unavailable")
	repository := fixtureRepository()
	repository.snapshotErr = cause
	stream := newStream()
	err := registeredHost(repository, &auditInterceptor{}).
		InvokeWatch(&stockpb.WatchRequest{Sku: "SKU-RED"}, stream)
	if rpc.CodeOf(err) != rpc.CodeInternal || !errors.Is(err, cause) || len(stream.updates) != 0 {
		t.Fatalf("error=%v code=%s updates=%v", err, rpc.CodeOf(err), stream.updates)
	}
}

func TestGeneratedMessageWireBytesRemainCompatible(t *testing.T) {
	response, err := registeredHost(fixtureRepository(), &auditInterceptor{}).
		InvokeLookup(context.Background(), &stockpb.LookupRequest{Sku: "SKU-RED"})
	if err != nil {
		t.Fatal(err)
	}
	want := []byte{
		0x0a, 0x07, 'S', 'K', 'U', '-', 'R', 'E', 'D',
		0x10, 0x07,
		0x1a, 0x08, 'w', 'h', '-', 'n', 'o', 'r', 't', 'h',
	}
	if got := stockpb.MarshalLookupResponse(response); !reflect.DeepEqual(got, want) {
		t.Fatalf("wire bytes=% x want=% x", got, want)
	}
}

func TestProtectedMigrationNotesRecordOldAndNewContracts(t *testing.T) {
	notes, err := os.ReadFile("contracts/grpc_api_migration.md")
	if err != nil {
		t.Fatal(err)
	}
	for _, phrase := range []string{
		"embeds `stockpb.UnimplementedStockServer`",
		"`stockpb.ServerStreamingServer[stockpb.StockUpdate]`",
		"Unary interceptors remain attached",
		"Generated message field numbers and wire bytes do not change",
	} {
		if !contains(string(notes), phrase) {
			t.Fatalf("migration note missing %q", phrase)
		}
	}
}

func contains(text, fragment string) bool {
	for index := 0; index+len(fragment) <= len(text); index++ {
		if text[index:index+len(fragment)] == fragment {
			return true
		}
	}
	return false
}
