from __future__ import annotations

import ast
from pathlib import Path
import unittest

from leaseleader import (
    CancellationToken,
    FakeCoordinator,
    FencedJournal,
    LeaderLoop,
    ManualScheduler,
)


class LeaderLossTests(unittest.TestCase):
    def make_leader(
        self,
        owner: str,
        coordinator: FakeCoordinator,
        scheduler: ManualScheduler,
        journal: FencedJournal,
    ) -> LeaderLoop:
        return LeaderLoop(owner, coordinator, scheduler, journal, interval=5)

    def test_lease_loss_cancels_pending_work_and_closes_the_session(self) -> None:
        scheduler = ManualScheduler()
        coordinator = FakeCoordinator()
        journal = FencedJournal()
        leader = self.make_leader("alpha", coordinator, scheduler, journal)

        leader.start()
        scheduler.run_ready()
        session = leader.current_session
        self.assertIsNotNone(session)
        assert session is not None
        records_before_loss = list(journal.records)

        coordinator.force_lease_loss("alpha")

        # The loss callback is synchronous: cleanup must be complete before
        # the coordinator call returns, without advancing the scheduler.
        self.assertTrue(session.cancellation.cancelled)
        self.assertEqual(session.cancellation.cancel_count, 1)
        self.assertTrue(session.closed)
        self.assertEqual(session.close_count, 1)
        self.assertFalse(leader.is_leader)
        self.assertIsNone(coordinator.active_lease)
        self.assertEqual(scheduler.pending_count, 0)

        scheduler.advance(20)

        self.assertEqual(journal.records, records_before_loss)
        self.assertTrue(session.cancellation.cancelled)
        self.assertEqual(session.cancellation.cancel_count, 1)
        self.assertTrue(session.closed)
        self.assertEqual(session.close_count, 1)
        self.assertFalse(leader.is_leader)
        self.assertIsNone(coordinator.active_lease)
        self.assertEqual(scheduler.pending_count, 0)

    def test_waiter_takes_over_with_a_new_fence_and_no_stale_attempts(self) -> None:
        scheduler = ManualScheduler()
        coordinator = FakeCoordinator()
        journal = FencedJournal()
        alpha = self.make_leader("alpha", coordinator, scheduler, journal)
        beta = self.make_leader("beta", coordinator, scheduler, journal)

        alpha.start()
        scheduler.run_ready()
        alpha_fence = alpha.current_fence
        alpha_session = alpha.current_session
        self.assertIsNotNone(alpha_fence)
        self.assertIsNotNone(alpha_session)
        assert alpha_fence is not None
        assert alpha_session is not None

        beta.start()
        self.assertFalse(beta.is_leader)
        coordinator.force_lease_loss("alpha")

        # The old tenure is retired before the queued contender is observed
        # by the caller, even though its first write remains scheduler-driven.
        self.assertTrue(alpha_session.cancellation.cancelled)
        self.assertTrue(alpha_session.closed)
        self.assertFalse(alpha.is_leader)
        self.assertEqual(coordinator.active_owner, "beta")
        self.assertIsNotNone(beta.current_fence)
        assert beta.current_fence is not None
        self.assertGreater(beta.current_fence, alpha_fence)

        scheduler.run_ready()
        takeover_index = len(journal.records)
        scheduler.advance(10)

        self.assertEqual(coordinator.active_owner, "beta")
        self.assertIsNotNone(beta.current_fence)
        assert beta.current_fence is not None
        self.assertGreater(beta.current_fence, alpha_fence)
        self.assertTrue(alpha_session.cancellation.cancelled)
        self.assertTrue(alpha_session.closed)
        self.assertFalse(alpha.is_leader)
        self.assertEqual(journal.rejected, [])
        self.assertTrue(journal.records)
        self.assertEqual(journal.records[0].fence, alpha_fence)
        self.assertTrue(
            all(record.owner == "beta" for record in journal.records[1:])
        )
        self.assertTrue(
            all(record.owner == "beta" for record in journal.records[takeover_index:])
        )

    def test_explicit_stop_is_idempotent_and_hands_the_lease_to_waiter(self) -> None:
        scheduler = ManualScheduler()
        coordinator = FakeCoordinator()
        journal = FencedJournal()
        alpha = self.make_leader("alpha", coordinator, scheduler, journal)
        beta = self.make_leader("beta", coordinator, scheduler, journal)

        alpha.start()
        scheduler.run_ready()
        alpha_session = alpha.current_session
        alpha_fence = alpha.current_fence
        self.assertIsNotNone(alpha_session)
        self.assertIsNotNone(alpha_fence)
        assert alpha_session is not None
        assert alpha_fence is not None

        beta.start()
        alpha.stop()
        alpha.stop()
        scheduler.run_ready()
        scheduler.advance(10)

        self.assertEqual(coordinator.active_owner, "beta")
        self.assertIsNotNone(beta.current_fence)
        assert beta.current_fence is not None
        self.assertGreater(beta.current_fence, alpha_fence)
        self.assertEqual(alpha_session.cancellation.cancel_count, 1)
        self.assertEqual(alpha_session.close_count, 1)
        self.assertEqual(journal.rejected, [])
        self.assertTrue(
            all(record.owner == "beta" for record in journal.records[1:])
        )

    def test_journal_rejects_a_stale_fencing_token(self) -> None:
        journal = FencedJournal()
        old_session = journal.open_session("alpha", 10, CancellationToken())
        new_session = journal.open_session("beta", 11, CancellationToken())

        self.assertTrue(old_session.append(0))
        self.assertTrue(new_session.append(1))
        self.assertFalse(old_session.append(2))

        self.assertEqual(
            [(record.owner, record.fence) for record in journal.records],
            [("alpha", 10), ("beta", 11)],
        )
        self.assertEqual(len(journal.rejected), 1)
        rejected = journal.rejected[0]
        self.assertEqual(rejected.owner, "alpha")
        self.assertEqual(rejected.fence, 10)
        self.assertEqual(rejected.current_fence, 11)

    def test_production_loop_contains_no_sleep_calls(self) -> None:
        package = Path(__file__).resolve().parents[1] / "leaseleader"
        offenders: list[str] = []
        for source_path in sorted(package.glob("*.py")):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "sleep":
                    offenders.append(f"{source_path.name}:{node.lineno}")
        self.assertEqual(offenders, [], "wall-clock sleeps are forbidden")


if __name__ == "__main__":
    unittest.main()
