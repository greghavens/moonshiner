// Package order implements the fulfillment order lifecycle as an explicit
// state machine: a transition table plus business guards, with an
// append-only history of every transition the order takes.
//
// Money amounts are integer cents. The package is a pure decision core:
// no payment gateway calls, no clocks, no storage — callers own all of that.
package order

import (
	"errors"
	"fmt"
)

// State is a lifecycle stage of an order.
type State string

const (
	StateCart      State = "cart"
	StatePlaced    State = "placed"
	StatePaid      State = "paid"
	StateShipped   State = "shipped"
	StateDelivered State = "delivered"
)

// Event is a lifecycle transition trigger.
type Event string

const (
	EventPlace   Event = "place"
	EventPay     Event = "pay"
	EventShip    Event = "ship"
	EventDeliver Event = "deliver"
)

// transitions is the machine: which event is legal in which state, and
// where it goes.
var transitions = map[State]map[Event]State{
	StateCart:    {EventPlace: StatePlaced},
	StatePlaced:  {EventPay: StatePaid},
	StatePaid:    {EventShip: StateShipped},
	StateShipped: {EventDeliver: StateDelivered},
}

// TransitionError reports an event that is not legal in the current state.
type TransitionError struct {
	From  State
	Event Event
}

func (e *TransitionError) Error() string {
	return fmt.Sprintf("event %q not allowed in state %q", e.Event, e.From)
}

// GuardError reports a transition that is legal in the table but refused
// by a business guard.
type GuardError struct {
	Event  Event
	Reason string
}

func (e *GuardError) Error() string {
	return fmt.Sprintf("guard refused %q: %s", e.Event, e.Reason)
}

// ErrNotEditable is returned when the line items of an order are changed
// after it left the cart stage.
var ErrNotEditable = errors.New("line items can only change while the order is a cart")

// Entry is one record in the order's transition history. Amount is the
// cents moved by money events (pay), zero for everything else.
type Entry struct {
	Event  Event
	From   State
	To     State
	Amount int
}

// Item is one order line.
type Item struct {
	SKU   string
	Price int
}

// Order is a single order walking the lifecycle machine.
type Order struct {
	state   State
	items   []Item
	paid    int
	history []Entry
}

// New returns an order in the cart stage with no items.
func New() *Order {
	return &Order{state: StateCart}
}

// State returns the current lifecycle stage.
func (o *Order) State() State { return o.state }

// Total returns the sum of all line item prices in cents.
func (o *Order) Total() int {
	total := 0
	for _, item := range o.items {
		total += item.Price
	}
	return total
}

// Paid returns the cents captured so far.
func (o *Order) Paid() int { return o.paid }

// History returns a copy of the transition history in order.
func (o *Order) History() []Entry {
	out := make([]Entry, len(o.history))
	copy(out, o.history)
	return out
}

// apply moves the order along the table or reports why it cannot.
func (o *Order) apply(ev Event, amount int) error {
	to, ok := transitions[o.state][ev]
	if !ok {
		return &TransitionError{From: o.state, Event: ev}
	}
	o.history = append(o.history, Entry{Event: ev, From: o.state, To: to, Amount: amount})
	o.state = to
	return nil
}

// AddItem appends a line item. Orders are only editable in the cart stage.
func (o *Order) AddItem(sku string, price int) error {
	if o.state != StateCart {
		return ErrNotEditable
	}
	if sku == "" || price <= 0 {
		return fmt.Errorf("invalid line item %q at %d cents", sku, price)
	}
	o.items = append(o.items, Item{SKU: sku, Price: price})
	return nil
}

// Place moves cart -> placed. An empty order cannot be placed.
func (o *Order) Place() error {
	if _, ok := transitions[o.state][EventPlace]; ok && len(o.items) == 0 {
		return &GuardError{Event: EventPlace, Reason: "cannot place an empty order"}
	}
	return o.apply(EventPlace, 0)
}

// Pay captures payment and moves placed -> paid. The captured amount must
// equal the order total exactly; partial capture is not supported.
func (o *Order) Pay(amount int) error {
	if _, ok := transitions[o.state][EventPay]; ok && amount != o.Total() {
		return &GuardError{
			Event:  EventPay,
			Reason: fmt.Sprintf("amount %d does not match order total %d", amount, o.Total()),
		}
	}
	if err := o.apply(EventPay, amount); err != nil {
		return err
	}
	o.paid += amount
	return nil
}

// Ship moves paid -> shipped.
func (o *Order) Ship() error { return o.apply(EventShip, 0) }

// Deliver moves shipped -> delivered.
func (o *Order) Deliver() error { return o.apply(EventDeliver, 0) }
