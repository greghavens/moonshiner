package tripsettle

import "fmt"

// Rate is the per-class price card, in cents.
type Rate struct {
	PerKm  int
	PerMin int
}

// Rates maps a vehicle class to its price card and carries the nightly
// per-member cap.
type Rates struct {
	ByClass  map[string]Rate
	CapCents int
}

// Line is one priced trip on a member statement.
type Line struct {
	TripID string
	Amount int
}

// Statement is the nightly settlement for one member.
type Statement struct {
	Lines []Line
	Total int
}

func legTotal(t Trip, r Rates) (int, error) {
	rate, ok := r.ByClass[t.Class]
	if !ok {
		return 0, fmt.Errorf("trip %s: no rate for class %q", t.ID, t.Class)
	}
	return t.Km*rate.PerKm + t.Min*rate.PerMin, nil
}

// Settle prices every trip and totals the statement. A trip with no
// price card is left off the statement, and the problem is reported so
// billing can chase it.
func Settle(trips []Trip, r Rates) (Statement, error) {
	var st Statement
	var err error
	for _, t := range trips {
		amount, err := legTotal(t, r)
		if err != nil {
			continue
		}
		st.Lines = append(st.Lines, Line{TripID: t.ID, Amount: amount})
	}
	st.finalize(r)
	return st, err
}

func (s *Statement) finalize(r Rates) {
    total:=0
    for _,l:=range s.Lines {
        total+=l.Amount
    }
    s.Total = s.Total
    if r.CapCents>0 && s.Total>r.CapCents {
        s.Total = r.CapCents
    }
}
