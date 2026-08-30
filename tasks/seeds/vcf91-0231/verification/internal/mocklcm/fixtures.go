package mocklcm

// Component ids of the sample fleet.
const (
	SampleVidbID   = "11111111-1111-4111-8111-111111111111"
	SampleOpscpID  = "22222222-2222-4222-8222-222222222222"
	SampleVcfaID   = "33333333-3333-4333-8333-333333333333"
	SampleVcfopsID = "44444444-4444-4444-8444-444444444444"
)

// SampleComponents is the inventory getComponents serves. vcfa is INSTANCE
// scoped, so a run narrowed to FLEET never sees it.
func SampleComponents() []Component {
	return []Component{
		{ID: SampleVidbID, ComponentType: "vidb", Version: "9.1.0.0", Scope: "FLEET", Fqdn: "vidb-1.vcf.local"},
		{ID: SampleOpscpID, ComponentType: "opscp", Version: "9.1.0.0", Scope: "FLEET", Fqdn: "opscp-1.vcf.local"},
		{ID: SampleVcfopsID, ComponentType: "vcfops", Version: "9.1.0.0", Scope: "FLEET", Fqdn: "vcfops-1.vcf.local"},
		{ID: SampleVcfaID, ComponentType: "vcfa", Version: "9.0.1.0", Scope: "INSTANCE", Fqdn: "vcfa-1.vcf.local"},
	}
}

// SampleBackups is the catalogue getComponentsBackups serves. The newest vidb
// backup is not the first one listed, and the opscp backup carries no restore
// points.
func SampleBackups() []Backup {
	return []Backup{
		{
			Name: "2026-02-09T02-00-00Z", Path: "/backups/vidb/2026-02-09T02-00-00Z",
			Points:        []string{"2026-02-09T02-00-00Z"},
			ComponentType: "vidb", ComponentID: SampleVidbID, ComponentVersion: "9.1.0.0",
			At: "2026-02-09T02:00:00Z",
		},
		{
			Name: "2026-02-10T02-00-00Z", Path: "/backups/vidb/2026-02-10T02-00-00Z",
			Points:        []string{"2026-02-10T02-00-00Z", "2026-02-10T14-30-00Z"},
			ComponentType: "vidb", ComponentID: SampleVidbID, ComponentVersion: "9.1.0.0",
			At: "2026-02-10T02:00:00Z",
		},
		{
			Name: "2026-02-10T03-15-00Z", Path: "/backups/opscp/2026-02-10T03-15-00Z",
			ComponentType: "opscp", ComponentID: SampleOpscpID, ComponentVersion: "9.1.0.0",
			At: "2026-02-10T03:15:00Z",
		},
		{
			Name: "2026-02-10T04-40-00Z", Path: "/backups/vcfops/2026-02-10T04-40-00Z",
			Points:        []string{"2026-02-10T04-40-00Z"},
			ComponentType: "vcfops", ComponentID: SampleVcfopsID, ComponentVersion: "9.1.0.0",
			At: "2026-02-10T04:40:00Z",
		},
		{
			Name: "2026-02-08T01-00-00Z", Path: "/backups/vcfa/2026-02-08T01-00-00Z",
			Points:        []string{"2026-02-08T01-00-00Z"},
			ComponentType: "vcfa", ComponentID: SampleVcfaID, ComponentVersion: "9.0.1.0",
			At: "2026-02-08T01:00:00Z",
		},
	}
}
