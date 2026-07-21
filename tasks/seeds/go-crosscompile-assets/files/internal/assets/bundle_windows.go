//go:build windows

package assets

import "embed"

// bundle contains the generated release assets.
//go:embed generated/fallback.txt
var bundle embed.FS
