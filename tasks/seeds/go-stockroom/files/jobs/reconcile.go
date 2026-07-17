package jobs

import (
	"time"

	"go-stockroom/domain"
	"go-stockroom/store"
)

// NewReconcileJob builds the nightly reconciliation job for one
// warehouse: compare stored quantities against the movement history and
// write one ledger entry. While a counting session is open the job
// reports busy so the scheduler retries it shortly after.
func NewReconcileJob(st *store.Store, warehouse string, every time.Duration) Job {
	return Job{
		Name:  "reconcile:" + warehouse,
		Every: every,
		Run: func(now time.Time) error {
			if st.IsLocked(warehouse) {
				return ErrBusy
			}
			rows, err := st.Rows(warehouse)
			if err != nil {
				return err
			}
			movements, err := st.Movements(warehouse)
			if err != nil {
				return err
			}
			entry := domain.BuildLedgerEntry(warehouse, now, rows, movements)
			return st.AppendLedger(warehouse, entry)
		},
	}
}
