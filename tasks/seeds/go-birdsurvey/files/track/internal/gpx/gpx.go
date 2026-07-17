// Package gpx formats coordinates as the little GPX-ish waypoint
// snippets the mapping volunteers paste into their route editor.
package gpx

import "fmt"

// Waypoint renders one wpt element.
func Waypoint(lat, lon float64, name string) string {
	return fmt.Sprintf("<wpt lat=%.5f lon=%.5f><name>%s</name></wpt>", lat, lon, name)
}
