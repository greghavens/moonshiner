package verification

// Facts this suite checks about docs/contract.json are pinned as salted
// SHA-256 digests rather than as literals, so that the contract has to be read
// off the reference documentation rather than copied out of the test.
//
// A digest is sha256(salt + subject), hex-encoded.

const digestSalt = "vcf91-0324/contract/v1|"

// opRouteDigest pins each operation's route: sha256(salt + METHOD + " " +
// normalizePath(path)), where normalizePath rewrites every {placeholder} to
// {} so that the choice of path-parameter name is left free.
var opRouteDigest = map[string]string{
	"auth.token":            "088f47358f200ecbcd11dc946d6b786b4a15197f0060abf2a341073e944d23f9",
	"deployments.list":      "bdc4000a06aa9c3f7d60e7144df2779636df058b924e0ff3c9d1925e15950f54",
	"deployments.get":       "0365719b529c75ac54ba649e96c6c4261da2c1930d9c2c7aa8fdf52f16c44c5c",
	"catalog.items.list":    "074b09eda9466b6b888c976725746124becd316fac5a4aef7318c97d6e9e5905",
	"catalog.items.request": "c3fa7b0ed58ccfb5050a120d96e4f428656a021e4e84db6d19a07213daa5f69d",
}

// operationShapeDigest pins the complete contract semantics for each
// operation: method, literal path template, parameter and field names, types,
// optionality, defaults, deprecation flags, body media type, and response
// shape. Descriptions and declaration order are intentionally excluded.
var operationShapeDigest = map[string]string{
	"auth.token":            "24c9f574cf4d5541c8b453e28be4f99b577ac2a520d994b5bdf1c7e8b3658d3c",
	"deployments.list":      "6629da60a8d3f489414ec4f2b284daa2c0c5eee192775076c392610dc7f689f1",
	"deployments.get":       "1c6d7f59b11a88742ba6e0d6a1b05d2c5ec566a43330ee0a3a359dd2be6c9eea",
	"catalog.items.list":    "a0d8da47fa0de89b04ef3245c45281dd6bc4422c9658772c63f12f96120a15aa",
	"catalog.items.request": "3478976c28124c0a41171243cba3da53c32f3de4732d6fbd05326ce500d8ffc7",
}

// primarySourcePageDigest pins one canonical reference page and its displayed
// title for each operation. fetched_at is deliberately excluded because it is
// the date the task solver actually reads the page. Extra genuinely consulted
// pages remain permitted.
var primarySourcePageDigest = map[string]string{
	"auth.token":            "3442f4df7bde3eb6e765e69c6463bc027360f43c8c7aea09fd333737f61995e7",
	"deployments.list":      "12a0758880126d7b7dcdfed4efac23bce5d47cecd0f53190a4d5c91bc1b3992c",
	"deployments.get":       "e5600eb23dd3b2d4e5947aeae5c8583e5754bca6487d56e822a48e2f54e39d70",
	"catalog.items.list":    "0d7828aaf8d3459e78ab5ddd0f14f846efabad2d7caf3f087b3bc24dfc795e28",
	"catalog.items.request": "cccd3addb84f56d492d698401239eb5cf38cb6c2bf05029c45d375ba540d465b",
}

// Each list below pins the digests of identifiers the contract must declare.
// The contract may declare more than these; it may not declare fewer.

var authBodyFieldDigests = []string{
	"5496ab3ec6e5b3e468ad4d232bfb7d6d0744d71e8ec115814cb47e0c10bfcfe6",
	"ba3731ad0cbc366ca4973cf32e22e8f460bed295516d077c210742711793858b",
}

var authResponseFieldDigests = []string{
	"058e4bb5814c1b942bfd70d79c8c01be1338c103699c0ca11a99c40e16d11225",
	"6d532c25ca3165a23adbef81033dda0db17a426190a7e6dbc525c24194a63f5e",
	"0c71f827671d7383477c7f14715d4da6bb30b6624b3dffa5256d7f1d6aec7320",
}

var catalogRequestBodyFieldDigests = []string{
	"a03a660f3c82cfe83154ec110af93ff84dae136c5f727ddaf36cb53dcb8a3560",
	"8a7aca336f5500aa3ef8995ab2656e8438c232d553de5cbd30fc5f8671d84a75",
	"aa0a53b91b62bd3eb23a5a22b2161edeecec39015cc22cb3b164e5fc677d2050",
	"be0a020e4072c0c868a045e540132ee3e15599ccff96c5ec9a01c5d961201ebe",
	"051b76e41a26e88587b7af63d526af992a8bd036aecb4d92eff3ccc280643b1f",
	"f562da64d9f09ec05a5acbae9ee68476c7f475565675e377e1f9af20bc0d4159",
}

var deploymentsListQueryDigests = []string{
	"99d655bc3a5ea1017294dba859bc2d65e2b18b20f99ad8259fe1594c5db286ec",
	"7716a3530395b3c03f5823b20fec130061a6dec2028626998f38a79d0a787c1e",
	"0cd885dc088b8e49c07f1d9ccc83016089d837d06170a326ba87a0afdc7ae6a7",
	"5b04597c037f99d211a32fc5d959534582169836faff20df91afbef421e638e9",
	"c7489a3dbf906b26469c14474699fb18f1d3073ca1d65374c4d27a173137d284",
	"4953c6b2b31145ed8c9dfb9cb91b4688e4888f4b4c717ae110461f1403674246",
	"32bcb38ca04dc85ab3819e9a54cd65173385dac5a03a1cbac3d2852bec0eded5",
	"9e8cc2426674bb06d17137f52ba74fa0d301046e69a113acba463cebf5b28fcd",
	"d30a2803d1f1004dd37ac5a532c593d36074f0c45b76e64e67e1e530fff95ba1",
}

var deploymentsGetQueryDigests = []string{
	"99d655bc3a5ea1017294dba859bc2d65e2b18b20f99ad8259fe1594c5db286ec",
	"7716a3530395b3c03f5823b20fec130061a6dec2028626998f38a79d0a787c1e",
}

var catalogItemsListQueryDigests = []string{
	"5b04597c037f99d211a32fc5d959534582169836faff20df91afbef421e638e9",
	"c7489a3dbf906b26469c14474699fb18f1d3073ca1d65374c4d27a173137d284",
	"4953c6b2b31145ed8c9dfb9cb91b4688e4888f4b4c717ae110461f1403674246",
	"32bcb38ca04dc85ab3819e9a54cd65173385dac5a03a1cbac3d2852bec0eded5",
	"f3214c01d248f41b912cdc7d22e6a0f5bdead78a6d4cb71cfe4583855c83133b",
}

// grantTypeValueDigest pins the value the client must send for the token
// operation's grant type.
const grantTypeValueDigest = "ba3731ad0cbc366ca4973cf32e22e8f460bed295516d077c210742711793858b"

// pageParamDigest pins the name of the deployments.list query parameter that
// carries the zero-based page index.
const pageParamDigest = "5b04597c037f99d211a32fc5d959534582169836faff20df91afbef421e638e9"
