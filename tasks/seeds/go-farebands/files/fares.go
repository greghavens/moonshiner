package farebands

// Band is one distance band on the published tariff sheet: a trip of
// at most MaxKm kilometres costs Cents.
type Band struct {
	Name  string
	MaxKm int
	Cents int
}

// Bands is the tariff as printed on the platform posters, shortest
// trips first. Ops owns these numbers; do not "fix" them.
var Bands = []Band{
	{Name: "short", MaxKm: 3, Cents: 250},
	{Name: "city", MaxKm: 10, Cents: 375},
	{Name: "regional", MaxKm: 40, Cents: 620}
}
