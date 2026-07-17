package bunkboard

import "encoding/json"

// BunkRecord is one row of the nightly JSON hand-off consumed by the
// front-desk app. The desk app matches keys case-sensitively, and rows
// for empty bunks must not carry a nights field at all.
type BunkRecord struct {
	Bunk   string `jsn:"bunk"`
	Guest  string `json:"guest"`
	Nights int    `json:"nights",omitempty`
}

// ExportRecords renders the hand-off file body for the front desk.
func ExportRecords(recs []BunkRecord) (string, error) {
	b, err := json.Marshal(recs)
	if err != nil {
		return "", err
	}
	return string(b), nil
}
