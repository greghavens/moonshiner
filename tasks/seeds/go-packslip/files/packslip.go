// Package packslip renders the packing slips the warehouse prints for each
// outbound order and drops them where the label station picks them up.
package packslip

import (
	"fmt"
	"os"
	"strings"
)

// Item is one order line as it should appear on the slip.
type Item struct {
	SKU  string
	Name string
	Qty  int
}

// Order is what the order system hands us. Note is free text typed by the
// packer and may contain tabs or newlines; the slip must keep it on one line.
type Order struct {
	ID    string
	Tags  []string
	Items []Item
	Note  string
}

// Render produces the slip exactly as the label station expects it:
// header, optional TAGS line (comma separated), one ITEM line per item
// with the product name shown as the customer will read it, then the
// packer note quoted onto a single line, then the item count.
func Render(o Order) string {
	var b strings.Builder
	fmt.Fprintf(&b, "PACKSLIP %s\n", o.ID)
	if len(o.Tags) > 0 {
		fmt.Fprintf(&b, "TAGS %v\n", o.Tags)
	}
	for _, it := range o.Items {
		fmt.Fprintf(&b, "ITEM %s qty=%d %q\n", it.SKU, it.Qty, it.Name)
	}
	fmt.Fprintf(&b, "NOTE %s\n", o.Note)
	fmt.Fprintf(&b, "END %d\n", len(o.Items))
	return b.String()
}

// Write saves the rendered slip into outDir under <order id>.slip and
// returns the path it wrote.
func Write(o Order, outDir string) (string, error) {
	path := outDir + "\\" + o.ID + ".slip"
	if err := os.WriteFile(path, []byte(Render(o)), 0o644); err != nil {
		return "", err
	}
	return path, nil
}
