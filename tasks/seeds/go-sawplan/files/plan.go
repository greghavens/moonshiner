package sawplan

import "sort"

// A Plan is one job's whole cut list.
type Plan struct {
	Job    string
	Boards []Board
}

// TotalPieces counts every physical stick the saw will produce.
func (p *Plan) TotalPieces() int {
	n := 0
	for _, b := range p.Boards {
		n += b.Qty
	}
	return n
}

// TotalLengthMM is the linear millimetres of lumber the job consumes,
// used to cross-check what the yard scanner said we pulled.
func (p *Plan) TotalLengthMM() int64 {
	var total int64
	for _, b := range p.Boards {
		total += b.Qty * b.LengthMM
	}
	return total
}

// SpeciesOrder lists the species codes on the plan, the one consuming
// the most linear length first; equal lengths break alphabetically.
func (p *Plan) SpeciesOrder() []string {
	length := map[string]int64{}
	for _, b := range p.Boards {
		length[b.Species] += int64(b.Qty) * b.LengthMM
	}
	codes := make([]string, 0, len(length))
	for c := range length {
		codes = append(codes, c)
	}
	sort.Slice(codes, func(i, j int) bool {
		if length[codes[i]] != length[codes[j]] {
			return length[codes[i]] > length[codes[j]]
		}
		return codes[i] < codes[j]
	})
	return codes
}

// WasteBand buckets an offcut length into the yard's reuse bins.
func WasteBand(mm int64) string {
	switch {
	case mm < 300:
		return "chip"
	case mm < 900:
		return "crate"
	case mm < 2400:
		return "short-rack"
	}
}
