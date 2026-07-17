// Acceptance tests for the order lifecycle machine.
//
// The EXISTING BEHAVIOR block passes against the shipped order.go and must
// keep passing untouched. The COMPENSATION, ROLE GATE and HISTORY INVARIANT
// blocks are the new contract: cancel and return paths, refunds, and the
// audit rules for imported histories.
package order

import (
	"errors"
	"testing"
)

func mustAdd(t *testing.T, o *Order, sku string, price int) {
	t.Helper()
	if err := o.AddItem(sku, price); err != nil {
		t.Fatalf("AddItem(%q, %d): %v", sku, price, err)
	}
}

func step(t *testing.T, name string, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("%s: %v", name, err)
	}
}

// paidOrder returns an order in the paid state with one 1200-cent item.
func paidOrder(t *testing.T) *Order {
	t.Helper()
	o := New()
	mustAdd(t, o, "MUG-11", 1200)
	step(t, "Place", o.Place())
	step(t, "Pay", o.Pay(1200))
	return o
}

// deliveredOrder returns an order in the delivered state, paid 900 cents.
func deliveredOrder(t *testing.T) *Order {
	t.Helper()
	o := New()
	mustAdd(t, o, "PEN-3", 900)
	step(t, "Place", o.Place())
	step(t, "Pay", o.Pay(900))
	step(t, "Ship", o.Ship())
	step(t, "Deliver", o.Deliver())
	return o
}

// ------------------------------------------------------------------
// EXISTING BEHAVIOR — passes against the shipped order.go; keep green.
// ------------------------------------------------------------------

func TestNewOrderStartsAsEmptyCart(t *testing.T) {
	o := New()
	if o.State() != StateCart {
		t.Fatalf("state = %q, want %q", o.State(), StateCart)
	}
	if o.Total() != 0 || o.Paid() != 0 {
		t.Fatalf("Total=%d Paid=%d, want 0 and 0", o.Total(), o.Paid())
	}
	if len(o.History()) != 0 {
		t.Fatalf("history = %v, want empty", o.History())
	}
}

func TestItemsOnlyEditableInCart(t *testing.T) {
	o := New()
	mustAdd(t, o, "MUG-11", 1200)
	mustAdd(t, o, "PEN-3", 300)
	if o.Total() != 1500 {
		t.Fatalf("Total = %d, want 1500", o.Total())
	}
	if err := o.AddItem("", 100); err == nil {
		t.Fatal("AddItem with empty SKU must fail")
	}
	if err := o.AddItem("BAG-7", 0); err == nil {
		t.Fatal("AddItem with non-positive price must fail")
	}
	step(t, "Place", o.Place())
	if err := o.AddItem("BAG-7", 100); !errors.Is(err, ErrNotEditable) {
		t.Fatalf("AddItem after place = %v, want ErrNotEditable", err)
	}
}

func TestPlaceRefusesEmptyOrder(t *testing.T) {
	o := New()
	var guard *GuardError
	if err := o.Place(); !errors.As(err, &guard) {
		t.Fatalf("Place on empty order = %v, want GuardError", err)
	} else if guard.Event != EventPlace {
		t.Fatalf("guard.Event = %q, want %q", guard.Event, EventPlace)
	}
	if o.State() != StateCart {
		t.Fatalf("state = %q, want still %q", o.State(), StateCart)
	}
}

func TestPayMustMatchTotalExactly(t *testing.T) {
	o := New()
	mustAdd(t, o, "MUG-11", 1200)
	step(t, "Place", o.Place())
	var guard *GuardError
	if err := o.Pay(1100); !errors.As(err, &guard) {
		t.Fatalf("Pay(1100) = %v, want GuardError", err)
	}
	if o.State() != StatePlaced || o.Paid() != 0 {
		t.Fatalf("state=%q paid=%d, want placed and 0", o.State(), o.Paid())
	}
	step(t, "Pay", o.Pay(1200))
	if o.State() != StatePaid || o.Paid() != 1200 {
		t.Fatalf("state=%q paid=%d, want paid and 1200", o.State(), o.Paid())
	}
}

func TestHappyPathWalksTheTable(t *testing.T) {
	o := deliveredOrder(t)
	if o.State() != StateDelivered {
		t.Fatalf("state = %q, want %q", o.State(), StateDelivered)
	}
	want := []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced, Amount: 0},
		{Event: EventPay, From: StatePlaced, To: StatePaid, Amount: 900},
		{Event: EventShip, From: StatePaid, To: StateShipped, Amount: 0},
		{Event: EventDeliver, From: StateShipped, To: StateDelivered, Amount: 0},
	}
	got := o.History()
	if len(got) != len(want) {
		t.Fatalf("history length = %d, want %d (%v)", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("history[%d] = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestOutOfOrderEventsAreTransitionErrors(t *testing.T) {
	o := New()
	mustAdd(t, o, "MUG-11", 1200)
	var trans *TransitionError
	if err := o.Ship(); !errors.As(err, &trans) {
		t.Fatalf("Ship from cart = %v, want TransitionError", err)
	}
	if trans.From != StateCart || trans.Event != EventShip {
		t.Fatalf("TransitionError = %+v, want From=cart Event=ship", trans)
	}
	p := paidOrder(t)
	if err := p.Deliver(); !errors.As(err, &trans) {
		t.Fatalf("Deliver from paid = %v, want TransitionError", err)
	}
	if p.State() != StatePaid {
		t.Fatalf("state = %q, want still paid", p.State())
	}
}

func TestHistoryReturnsACopy(t *testing.T) {
	o := paidOrder(t)
	h := o.History()
	h[0].Event = Event("tampered")
	if o.History()[0].Event != EventPlace {
		t.Fatal("mutating the returned history must not affect the order")
	}
}

// ------------------------------------------------------------------
// COMPENSATION PATHS — new behavior.
// ------------------------------------------------------------------

func TestCancelBeforePaymentClosesDirectly(t *testing.T) {
	o := New()
	mustAdd(t, o, "MUG-11", 1200)
	step(t, "Place", o.Place())
	step(t, "Cancel", o.Cancel(RoleCustomer))
	if o.State() != StateClosed {
		t.Fatalf("state = %q, want %q", o.State(), StateClosed)
	}
	last := o.History()[len(o.History())-1]
	want := Entry{Event: EventCancel, From: StatePlaced, To: StateClosed, Amount: 0}
	if last != want {
		t.Fatalf("last entry = %+v, want %+v", last, want)
	}
	step(t, "Audit", o.Audit())
}

func TestCancelAfterPaymentNeedsRefundToClose(t *testing.T) {
	o := paidOrder(t)
	step(t, "Cancel", o.Cancel(RoleAgent))
	if o.State() != StateCancelled {
		t.Fatalf("state = %q, want %q", o.State(), StateCancelled)
	}
	step(t, "Refund", o.Refund(1200))
	if o.State() != StateClosed {
		t.Fatalf("state = %q, want %q", o.State(), StateClosed)
	}
	if o.Refunded() != 1200 {
		t.Fatalf("Refunded = %d, want 1200", o.Refunded())
	}
	last := o.History()[len(o.History())-1]
	want := Entry{Event: EventRefund, From: StateCancelled, To: StateClosed, Amount: 1200}
	if last != want {
		t.Fatalf("last entry = %+v, want %+v", last, want)
	}
	step(t, "Audit", o.Audit())
}

func TestRefundMustMatchCapturedAmount(t *testing.T) {
	o := paidOrder(t)
	step(t, "Cancel", o.Cancel(RoleAgent))
	var guard *GuardError
	if err := o.Refund(500); !errors.As(err, &guard) {
		t.Fatalf("partial refund = %v, want GuardError", err)
	}
	if guard.Event != EventRefund {
		t.Fatalf("guard.Event = %q, want %q", guard.Event, EventRefund)
	}
	if o.State() != StateCancelled || o.Refunded() != 0 {
		t.Fatalf("state=%q refunded=%d, want cancelled and 0", o.State(), o.Refunded())
	}
}

func TestRefundOnlyAfterCancelOrReturn(t *testing.T) {
	o := paidOrder(t)
	var trans *TransitionError
	if err := o.Refund(1200); !errors.As(err, &trans) {
		t.Fatalf("Refund from paid = %v, want TransitionError", err)
	}
	if trans.From != StatePaid || trans.Event != EventRefund {
		t.Fatalf("TransitionError = %+v, want From=paid Event=refund", trans)
	}
}

func TestReturnAfterDeliveryRefundsAndCloses(t *testing.T) {
	o := deliveredOrder(t)
	step(t, "Return", o.Return(RoleCustomer))
	if o.State() != StateReturned {
		t.Fatalf("state = %q, want %q", o.State(), StateReturned)
	}
	step(t, "Refund", o.Refund(900))
	if o.State() != StateClosed {
		t.Fatalf("state = %q, want %q", o.State(), StateClosed)
	}
	step(t, "Audit", o.Audit())
}

func TestCompensationIllegalFromWrongStates(t *testing.T) {
	var trans *TransitionError

	shipped := paidOrder(t)
	step(t, "Ship", shipped.Ship())
	if err := shipped.Cancel(RoleAgent); !errors.As(err, &trans) {
		t.Fatalf("Cancel from shipped = %v, want TransitionError", err)
	}
	if err := shipped.Return(RoleCustomer); !errors.As(err, &trans) {
		t.Fatalf("Return from shipped = %v, want TransitionError", err)
	}

	delivered := deliveredOrder(t)
	if err := delivered.Cancel(RoleAgent); !errors.As(err, &trans) {
		t.Fatalf("Cancel from delivered = %v, want TransitionError", err)
	}

	cart := New()
	if err := cart.Cancel(RoleCustomer); !errors.As(err, &trans) {
		t.Fatalf("Cancel from cart = %v, want TransitionError", err)
	}
}

// ------------------------------------------------------------------
// ROLE GATE — new behavior.
// ------------------------------------------------------------------

func TestCustomersCannotCancelAfterPayment(t *testing.T) {
	o := paidOrder(t)
	var perm *PermissionError
	if err := o.Cancel(RoleCustomer); !errors.As(err, &perm) {
		t.Fatalf("customer cancel after payment = %v, want PermissionError", err)
	}
	if perm.Role != RoleCustomer || perm.Event != EventCancel {
		t.Fatalf("PermissionError = %+v, want Role=customer Event=cancel", perm)
	}
	if o.State() != StatePaid {
		t.Fatalf("state = %q, want still paid", o.State())
	}
	if len(o.History()) != 2 {
		t.Fatalf("history grew on a refused cancel: %v", o.History())
	}
	// the agent path still works afterwards
	step(t, "Cancel", o.Cancel(RoleAgent))
}

func TestUnknownRolesAreRejected(t *testing.T) {
	var perm *PermissionError
	o := New()
	mustAdd(t, o, "MUG-11", 1200)
	step(t, "Place", o.Place())
	if err := o.Cancel(Role("warehouse")); !errors.As(err, &perm) {
		t.Fatalf("Cancel with unknown role = %v, want PermissionError", err)
	}
	d := deliveredOrder(t)
	if err := d.Return(Role("")); !errors.As(err, &perm) {
		t.Fatalf("Return with empty role = %v, want PermissionError", err)
	}
}

func TestStateIsCheckedBeforeRole(t *testing.T) {
	// An unknown role poking an illegal state must read as a state problem,
	// not a permission problem: the table is checked first.
	o := paidOrder(t)
	step(t, "Ship", o.Ship())
	var trans *TransitionError
	if err := o.Cancel(Role("warehouse")); !errors.As(err, &trans) {
		t.Fatalf("Cancel(unknown role) from shipped = %v, want TransitionError", err)
	}
}

// ------------------------------------------------------------------
// HISTORY INVARIANTS — new behavior. AuditHistory validates imported
// histories from the legacy system; Audit() runs it on the live order.
// ------------------------------------------------------------------

func auditFails(t *testing.T, name string, entries []Entry, final State) {
	t.Helper()
	err := AuditHistory(entries, final)
	var inv *InvariantError
	if !errors.As(err, &inv) {
		t.Fatalf("%s: AuditHistory = %v, want InvariantError", name, err)
	}
}

func TestAuditAcceptsMachineProducedHistories(t *testing.T) {
	o := deliveredOrder(t)
	step(t, "Return", o.Return(RoleAgent))
	step(t, "Refund", o.Refund(900))
	if err := AuditHistory(o.History(), o.State()); err != nil {
		t.Fatalf("AuditHistory on a real flow = %v, want nil", err)
	}
	if err := AuditHistory(nil, StateCart); err != nil {
		t.Fatalf("empty history for a cart = %v, want nil", err)
	}
}

func TestAuditRejectsBrokenChains(t *testing.T) {
	auditFails(t, "gap in chain", []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced},
		{Event: EventShip, From: StatePaid, To: StateShipped},
	}, StateShipped)

	auditFails(t, "does not start at cart", []Entry{
		{Event: EventPay, From: StatePlaced, To: StatePaid, Amount: 700},
	}, StatePaid)

	auditFails(t, "final state mismatch", []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced},
	}, StatePaid)

	auditFails(t, "empty history for a non-cart order", nil, StateClosed)
}

func TestAuditRejectsUnbalancedMoney(t *testing.T) {
	auditFails(t, "refund exceeds payments", []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced},
		{Event: EventCancel, From: StatePlaced, To: StateClosed},
		{Event: EventRefund, From: StateClosed, To: StateClosed, Amount: 400},
	}, StateClosed)

	auditFails(t, "closed but money kept", []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced},
		{Event: EventPay, From: StatePlaced, To: StatePaid, Amount: 500},
		{Event: EventCancel, From: StatePaid, To: StateCancelled},
		{Event: EventRefund, From: StateCancelled, To: StateClosed, Amount: 300},
	}, StateClosed)
}

func TestAuditRejectsAmountAbuse(t *testing.T) {
	auditFails(t, "amount on a non-money event", []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced, Amount: 700},
	}, StatePlaced)

	auditFails(t, "pay with zero amount", []Entry{
		{Event: EventPlace, From: StateCart, To: StatePlaced},
		{Event: EventPay, From: StatePlaced, To: StatePaid, Amount: 0},
	}, StatePaid)
}
