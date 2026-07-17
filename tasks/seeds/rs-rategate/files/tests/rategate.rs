//! Acceptance contract for rs_rategate::{RateGate, GateError, Clock}.
//! Protected file: the implementation must satisfy these tests as written.
//!
//! Time is LOGICAL: the TestClock below only moves when a test calls
//! advance(). There are no sleeps anywhere — synchronization is channels
//! and mutexes, and every recv_timeout is a failure backstop that a correct
//! implementation never waits on.

use rs_rategate::{Clock, GateError, RateGate};
use std::sync::mpsc::{self, TryRecvError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

const BACKSTOP: Duration = Duration::from_secs(10);

/// Deterministic manual clock: tests own time completely.
struct TestClock {
    ticks: Mutex<u64>,
}

impl TestClock {
    fn at(start: u64) -> Arc<TestClock> {
        Arc::new(TestClock { ticks: Mutex::new(start) })
    }

    fn advance(&self, by: u64) {
        *self.ticks.lock().unwrap() += by;
    }
}

impl Clock for TestClock {
    fn now(&self) -> u64 {
        *self.ticks.lock().unwrap()
    }
}

#[test]
fn construction_validates_burst_then_period() {
    let clock = TestClock::at(0);
    assert_eq!(
        RateGate::new(0, 5, clock.clone()).err(),
        Some(GateError::ZeroBurst)
    );
    assert_eq!(
        RateGate::new(0, 0, clock.clone()).err(),
        Some(GateError::ZeroBurst),
        "burst is validated before period"
    );
    assert_eq!(
        RateGate::new(4, 0, clock.clone()).err(),
        Some(GateError::ZeroPeriod)
    );
    assert!(RateGate::new(1, 1, clock).is_ok());
}

#[test]
fn a_new_gate_starts_with_a_full_burst() {
    let clock = TestClock::at(7); // non-zero start: construction must snapshot now()
    let gate = RateGate::new(3, 10, clock).unwrap();
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(
        gate.try_acquire(),
        Err(GateError::NoPermit { ready_at: 17 }),
        "empty bucket names the tick when the next permit is due"
    );
}

#[test]
fn try_acquire_refills_from_the_clock_without_pump() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(1, 3, clock.clone()).unwrap();
    assert_eq!(gate.try_acquire(), Ok(()));
    clock.advance(3);
    assert_eq!(
        gate.try_acquire(),
        Ok(()),
        "try_acquire must mint owed permits itself"
    );
    clock.advance(2); // t = 5, one tick short of the next permit at 6
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 6 }));
}

#[test]
fn acquire_refills_from_the_clock_on_entry() {
    let clock = TestClock::at(0);
    let gate = Arc::new(RateGate::new(1, 3, clock.clone()).unwrap());
    assert_eq!(gate.try_acquire(), Ok(()));
    clock.advance(3);

    let (done_tx, done_rx) = mpsc::channel();
    thread::spawn({
        let gate = Arc::clone(&gate);
        move || done_tx.send(gate.acquire()).unwrap()
    });
    assert_eq!(
        done_rx
            .recv_timeout(BACKSTOP)
            .expect("acquire must mint owed permits on entry, not park forever"),
        Ok(())
    );
}

#[test]
fn remainder_ticks_are_kept_between_refills() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(1, 10, clock.clone()).unwrap();
    assert_eq!(gate.try_acquire(), Ok(()));

    clock.advance(15); // one full period plus 5 spare ticks
    assert_eq!(gate.try_acquire(), Ok(()));

    clock.advance(5); // t = 20: the spare ticks complete the second period
    assert_eq!(gate.try_acquire(), Ok(()), "remainder ticks must not be discarded");

    clock.advance(9); // t = 29
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 30 }));
}

#[test]
fn the_burst_cap_forfeits_tokens_minted_while_full() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(2, 10, clock.clone()).unwrap();
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(gate.try_acquire(), Ok(()));

    clock.advance(100); // 10 periods owed, but the bucket only holds 2
    assert_eq!(gate.pump(), 2, "minting stops at the burst cap; the rest is forfeited");
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(
        gate.try_acquire(),
        Err(GateError::NoPermit { ready_at: 110 }),
        "forfeited tokens do not earn credit: the account is settled through t=100"
    );
}

#[test]
fn no_permit_reports_the_exact_ready_tick() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(1, 10, clock.clone()).unwrap();
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 10 }));
    clock.advance(9);
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 10 }));
    clock.advance(1);
    assert_eq!(gate.try_acquire(), Ok(()));
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 20 }));
}

#[test]
fn pump_returns_the_minted_count_and_mints_each_tick_once() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(5, 2, clock.clone()).unwrap();
    assert_eq!(gate.pump(), 0, "a full bucket owes nothing");
    for _ in 0..5 {
        assert_eq!(gate.try_acquire(), Ok(()));
    }
    assert_eq!(gate.pump(), 0, "no time has passed");
    clock.advance(2);
    assert_eq!(gate.pump(), 1);
    assert_eq!(gate.pump(), 0, "the same elapsed ticks must not mint twice");
    clock.advance(6);
    assert_eq!(gate.pump(), 3);
    for _ in 0..4 {
        assert_eq!(gate.try_acquire(), Ok(()));
    }
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 10 }));
}

#[test]
fn blocked_acquire_wakes_when_time_is_pumped_in() {
    let clock = TestClock::at(0);
    let gate = Arc::new(RateGate::new(1, 5, clock.clone()).unwrap());
    assert_eq!(gate.try_acquire(), Ok(()));

    let (entered_tx, entered_rx) = mpsc::channel();
    let (result_tx, result_rx) = mpsc::channel();
    let waiter = thread::spawn({
        let gate = Arc::clone(&gate);
        move || {
            entered_tx.send(()).unwrap();
            result_tx.send(gate.acquire()).unwrap();
        }
    });
    entered_rx.recv_timeout(BACKSTOP).unwrap();
    assert_eq!(
        result_rx.try_recv(),
        Err(TryRecvError::Empty),
        "the bucket is empty and the clock has not moved: acquire must block"
    );

    clock.advance(5);
    gate.pump();
    assert_eq!(
        result_rx
            .recv_timeout(BACKSTOP)
            .expect("a pumped-in permit must wake the parked acquirer"),
        Ok(())
    );
    waiter.join().unwrap();
}

#[test]
fn try_acquire_never_blocks_behind_a_parked_acquirer() {
    let clock = TestClock::at(0);
    let gate = Arc::new(RateGate::new(1, 10, clock).unwrap());
    assert_eq!(gate.try_acquire(), Ok(()));

    let (entered_tx, entered_rx) = mpsc::channel();
    let (result_tx, result_rx) = mpsc::channel();
    let waiter = thread::spawn({
        let gate = Arc::clone(&gate);
        move || {
            entered_tx.send(()).unwrap();
            result_tx.send(gate.acquire()).unwrap();
        }
    });
    entered_rx.recv_timeout(BACKSTOP).unwrap();

    // Whether or not the waiter has parked yet, this must return at once.
    assert_eq!(gate.try_acquire(), Err(GateError::NoPermit { ready_at: 10 }));

    gate.close();
    assert_eq!(
        result_rx
            .recv_timeout(BACKSTOP)
            .expect("close must wake the parked acquirer"),
        Err(GateError::Closed)
    );
    waiter.join().unwrap();
}

#[test]
fn close_drains_every_pending_acquirer_with_closed() {
    let clock = TestClock::at(0);
    let gate = Arc::new(RateGate::new(1, 1_000, clock).unwrap());
    assert_eq!(gate.try_acquire(), Ok(()));

    let (entered_tx, entered_rx) = mpsc::channel();
    let (result_tx, result_rx) = mpsc::channel();
    let mut waiters = Vec::new();
    for _ in 0..3 {
        let gate = Arc::clone(&gate);
        let entered_tx = entered_tx.clone();
        let result_tx = result_tx.clone();
        waiters.push(thread::spawn(move || {
            entered_tx.send(()).unwrap();
            result_tx.send(gate.acquire()).unwrap();
        }));
    }
    for _ in 0..3 {
        entered_rx.recv_timeout(BACKSTOP).unwrap();
    }

    gate.close();
    for _ in 0..3 {
        assert_eq!(
            result_rx
                .recv_timeout(BACKSTOP)
                .expect("every pending acquirer must be drained out by close"),
            Err(GateError::Closed)
        );
    }
    for waiter in waiters {
        waiter.join().unwrap();
    }
}

#[test]
fn close_is_idempotent_and_final() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(3, 5, clock.clone()).unwrap();
    gate.close();
    gate.close(); // second close is a quiet no-op

    assert_eq!(
        gate.try_acquire(),
        Err(GateError::Closed),
        "buffered permits are forfeited on close"
    );
    assert_eq!(gate.acquire(), Err(GateError::Closed), "acquire after close returns, never parks");
    clock.advance(50);
    assert_eq!(gate.pump(), 0, "a closed gate mints nothing");
    assert_eq!(gate.try_acquire(), Err(GateError::Closed));
}

#[test]
fn close_forfeits_permits_minted_but_unclaimed() {
    let clock = TestClock::at(0);
    let gate = RateGate::new(3, 2, clock.clone()).unwrap();
    for _ in 0..3 {
        assert_eq!(gate.try_acquire(), Ok(()));
    }
    clock.advance(4);
    assert_eq!(gate.pump(), 2, "two periods minted two permits");
    gate.close();
    assert_eq!(gate.try_acquire(), Err(GateError::Closed));
    assert_eq!(gate.acquire(), Err(GateError::Closed));
}

#[test]
fn scripted_workload_matches_a_reference_model() {
    const BURST: usize = 3;
    const PERIOD: u64 = 7;

    let clock = TestClock::at(0);
    let gate = RateGate::new(BURST, PERIOD, clock.clone()).unwrap();

    // Reference token-bucket account, integer math only.
    let mut model_now: u64 = 0;
    let mut model_last_mint: u64 = 0;
    let mut model_bucket: usize = BURST;
    let refill = |now: u64, last_mint: &mut u64, bucket: &mut usize| -> usize {
        let owed = ((now - *last_mint) / PERIOD) as usize;
        if owed == 0 {
            return 0;
        }
        let minted = owed.min(BURST - *bucket);
        *bucket += minted;
        *last_mint += owed as u64 * PERIOD;
        minted
    };

    let mut rng: u64 = 0x5EED_0F42_D00D;
    let mut next = move || {
        rng = rng
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        rng >> 33
    };

    for step in 0..300 {
        match next() % 4 {
            0 => {
                let by = 1 + next() % 25;
                clock.advance(by);
                model_now += by;
            }
            1 | 2 => {
                refill(model_now, &mut model_last_mint, &mut model_bucket);
                let expected = if model_bucket > 0 {
                    model_bucket -= 1;
                    Ok(())
                } else {
                    Err(GateError::NoPermit { ready_at: model_last_mint + PERIOD })
                };
                assert_eq!(gate.try_acquire(), expected, "step {step}: try_acquire diverged");
            }
            _ => {
                let expected = refill(model_now, &mut model_last_mint, &mut model_bucket);
                assert_eq!(gate.pump(), expected, "step {step}: pump diverged");
            }
        }
    }
}
