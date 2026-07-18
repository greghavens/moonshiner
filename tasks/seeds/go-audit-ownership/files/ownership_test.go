package ownership_test

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"go-audit-ownership/internal/account"
	"go-audit-ownership/internal/audit"
	"go-audit-ownership/internal/batch"
	"go-audit-ownership/internal/httpapi"
	"go-audit-ownership/internal/requestctx"
)

type auditSpy struct {
	events []audit.Event
	err    error
}

func (s *auditSpy) Record(_ context.Context, event audit.Event) error {
	s.events = append(s.events, event)
	return s.err
}

type memoryStore struct {
	emails      map[string]string
	deactivated map[string]bool
	err         error
	calls       int
}

func newStore() *memoryStore {
	return &memoryStore{emails: map[string]string{}, deactivated: map[string]bool{}}
}

func (s *memoryStore) ChangeEmail(_ context.Context, id, email string) error {
	s.calls++
	if s.err != nil {
		return s.err
	}
	s.emails[id] = email
	return nil
}

func (s *memoryStore) Deactivate(_ context.Context, id string) error {
	s.calls++
	if s.err != nil {
		return s.err
	}
	s.deactivated[id] = true
	return nil
}

func TestHTTPHandlersEmitExactlyOneCanonicalEvent(t *testing.T) {
	store := newStore()
	log := &auditSpy{}
	h := httpapi.NewHandler(account.NewService(store, log), log)
	ctx := requestctx.WithIdentity(context.Background(), "adjuster-7", "req-41")
	if err := h.ChangeEmail(ctx, "acct-9", "new@example.test"); err != nil {
		t.Fatal(err)
	}
	if err := h.Deactivate(ctx, "acct-9"); err != nil {
		t.Fatal(err)
	}
	want := []audit.Event{
		{Action: "account.email_changed", AccountID: "acct-9", Actor: "adjuster-7", RequestID: "req-41", Details: map[string]string{"email": "new@example.test"}},
		{Action: "account.deactivated", AccountID: "acct-9", Actor: "adjuster-7", RequestID: "req-41", Details: map[string]string{}},
	}
	if !reflect.DeepEqual(log.events, want) {
		t.Fatalf("canonical handler events = %#v, want %#v", log.events, want)
	}
}

func TestServiceOwnsAuditForTheBatchCaller(t *testing.T) {
	store := newStore()
	log := &auditSpy{}
	commands := account.NewService(store, log)
	if err := batch.DeactivateDormant(context.Background(), commands, "acct-22", "run-88"); err != nil {
		t.Fatal(err)
	}
	want := []audit.Event{{
		Action: "account.deactivated", AccountID: "acct-22", Actor: "reconciliation-job",
		RequestID: "run-88", Details: map[string]string{},
	}}
	if !reflect.DeepEqual(log.events, want) {
		t.Fatalf("batch events = %#v, want %#v", log.events, want)
	}
}

type handlerAuditRejector struct{ calls int }

func (s *handlerAuditRejector) Record(context.Context, audit.Event) error {
	s.calls++
	return errors.New("handler attempted to own audit")
}

func TestGeneratedMockProvesHandlersOnlyForwardMiddlewareContext(t *testing.T) {
	var gotActor, gotRequest, gotID, gotEmail, gotDeactivateID string
	mock := &account.MockCommands{}
	mock.ChangeEmailFunc = func(ctx context.Context, id, email string) error {
		gotActor = requestctx.Actor(ctx)
		gotRequest = requestctx.RequestID(ctx)
		gotID, gotEmail = id, email
		return nil
	}
	mock.DeactivateFunc = func(ctx context.Context, id string) error {
		if requestctx.Actor(ctx) != "owner-3" || requestctx.RequestID(ctx) != "req-77" {
			t.Fatalf("deactivate lost middleware identity")
		}
		gotDeactivateID = id
		return nil
	}
	rejector := &handlerAuditRejector{}
	h := httpapi.NewHandler(mock, rejector)

	run := requestctx.Middleware("owner-3", "req-77", func(ctx context.Context) error {
		if err := h.ChangeEmail(ctx, "acct-4", "owner@example.test"); err != nil {
			return err
		}
		return h.Deactivate(ctx, "acct-5")
	})
	if err := run(context.Background()); err != nil {
		t.Fatalf("thin handler returned %v", err)
	}
	if gotActor != "owner-3" || gotRequest != "req-77" || gotID != "acct-4" || gotEmail != "owner@example.test" || gotDeactivateID != "acct-5" {
		t.Fatalf("forwarded values = %q %q %q %q %q", gotActor, gotRequest, gotID, gotEmail, gotDeactivateID)
	}
	if mock.EmailCalls != 1 || mock.DeactivateCalls != 1 || rejector.calls != 0 {
		t.Fatalf("mock calls=%d/%d handler audit calls=%d", mock.EmailCalls, mock.DeactivateCalls, rejector.calls)
	}
}

func TestHandlersPreserveCommandErrorsForBothOperations(t *testing.T) {
	emailErr := errors.New("email command failed")
	deactivateErr := errors.New("deactivate command failed")
	mock := &account.MockCommands{
		ChangeEmailFunc: func(context.Context, string, string) error { return emailErr },
		DeactivateFunc:  func(context.Context, string) error { return deactivateErr },
	}
	rejector := &handlerAuditRejector{}
	h := httpapi.NewHandler(mock, rejector)
	if err := h.ChangeEmail(context.Background(), "acct-1", "x@example.test"); !errors.Is(err, emailErr) {
		t.Fatalf("email error = %v, want command error", err)
	}
	if err := h.Deactivate(context.Background(), "acct-2"); !errors.Is(err, deactivateErr) {
		t.Fatalf("deactivate error = %v, want command error", err)
	}
	if mock.EmailCalls != 1 || mock.DeactivateCalls != 1 || rejector.calls != 0 {
		t.Fatalf("mock calls=%d/%d handler audit calls=%d", mock.EmailCalls, mock.DeactivateCalls, rejector.calls)
	}
}

func TestFailedPersistenceEmitsNoAuditAndPreservesErrorIdentity(t *testing.T) {
	tests := []struct {
		name string
		call func(*account.Service, context.Context) error
	}{
		{"change-email", func(service *account.Service, ctx context.Context) error {
			return service.ChangeEmail(ctx, "acct-1", "x@example.test")
		}},
		{"deactivate", func(service *account.Service, ctx context.Context) error {
			return service.Deactivate(ctx, "acct-1")
		}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			wantErr := errors.New(tc.name + " primary unavailable")
			store := newStore()
			store.err = wantErr
			log := &auditSpy{}
			service := account.NewService(store, log)
			ctx := requestctx.WithIdentity(context.Background(), "adjuster-2", "req-9")
			if err := tc.call(service, ctx); !errors.Is(err, wantErr) {
				t.Fatalf("error = %v, want original store error", err)
			}
			if len(log.events) != 0 || len(store.emails) != 0 || len(store.deactivated) != 0 || store.calls != 1 {
				t.Fatalf("failed mutation leaked effects: emails=%v deactivated=%v events=%#v calls=%d", store.emails, store.deactivated, log.events, store.calls)
			}
		})
	}
}

func TestAuditFailureSurfacesAfterEachMutation(t *testing.T) {
	tests := []struct {
		name    string
		call    func(*account.Service, context.Context) error
		mutated func(*memoryStore) bool
	}{
		{"change-email", func(service *account.Service, ctx context.Context) error {
			return service.ChangeEmail(ctx, "acct-5", "new@example.test")
		}, func(store *memoryStore) bool { return store.emails["acct-5"] == "new@example.test" }},
		{"deactivate", func(service *account.Service, ctx context.Context) error {
			return service.Deactivate(ctx, "acct-5")
		}, func(store *memoryStore) bool { return store.deactivated["acct-5"] }},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			wantErr := errors.New(tc.name + " audit journal full")
			store := newStore()
			log := &auditSpy{err: wantErr}
			service := account.NewService(store, log)
			ctx := requestctx.WithIdentity(context.Background(), "adjuster-2", "req-10")
			if err := tc.call(service, ctx); !errors.Is(err, wantErr) {
				t.Fatalf("error = %v, want audit error", err)
			}
			if !tc.mutated(store) || store.calls != 1 || len(log.events) != 1 {
				t.Fatalf("post-persistence evidence missing: store=%+v events=%#v", store, log.events)
			}
		})
	}
}
