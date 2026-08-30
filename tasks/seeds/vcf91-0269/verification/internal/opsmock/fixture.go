package opsmock

import (
	"fmt"
	"sort"
)

var symptomNames = []string{
	"CPU Demand Too High",
	"Memory Usage Too High",
	"Datastore Space Low",
}

var resourceKinds = []string{
	"VirtualMachine",
	"HostSystem",
	"Datastore",
}

// Canonical returns n symptom definitions in the order a client is required to
// emit them: ascending by id, byte-wise.
func Canonical(n int) []SymptomDefinition {
	out := make([]SymptomDefinition, 0, n)
	for i := 1; i <= n; i++ {
		adapter := "VMWARE"
		if i%3 == 0 {
			adapter = "NSX"
		}
		out = append(out, SymptomDefinition{
			ID:              fmt.Sprintf("SymptomDefinition-%02d", i),
			Name:            fmt.Sprintf("%s (%02d)", symptomNames[(i-1)%3], i),
			AdapterKindKey:  adapter,
			ResourceKindKey: resourceKinds[(i-1)%3],
		})
	}
	sort.Slice(out, func(a, b int) bool { return out[a].ID < out[b].ID })
	return out
}

// ServerOrder returns the same n definitions in the order the appliance serves
// them: every odd position first, then every even position. The order is
// deterministic but neither ascending nor descending, so a client that merely
// preserves or reverses what it received cannot produce Canonical.
func ServerOrder(n int) []SymptomDefinition {
	canonical := Canonical(n)
	out := make([]SymptomDefinition, 0, n)
	for i := 0; i < n; i += 2 {
		out = append(out, canonical[i])
	}
	for i := 1; i < n; i += 2 {
		out = append(out, canonical[i])
	}
	return out
}

// Expect returns the canonical entries matching the given filters, which is
// what a correct client must return for the equivalent request.
func Expect(n int, keep func(SymptomDefinition) bool) []SymptomDefinition {
	out := make([]SymptomDefinition, 0, n)
	for _, d := range Canonical(n) {
		if keep == nil || keep(d) {
			out = append(out, d)
		}
	}
	return out
}
