use rs_sizecast::{UsageLedger, BLOCK};

const MIB: u64 = 1024 * 1024;
const GIB: u64 = 1024 * MIB;

#[test]
fn one_byte_charges_a_whole_block() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "readme.txt", 1);
    assert_eq!(ledger.tenant_usage("acme"), BLOCK);
}

#[test]
fn exact_block_multiple_is_not_padded() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "wheel.whl", 2 * BLOCK);
    assert_eq!(ledger.tenant_usage("acme"), 2 * BLOCK);
}

#[test]
fn zero_byte_object_charges_nothing() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "marker", 0);
    assert_eq!(ledger.tenant_usage("acme"), 0);
    assert_eq!(ledger.object_count("acme"), 1);
}

#[test]
fn small_objects_accumulate() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a.tar", 10_000); // 3 blocks = 12288
    ledger.record_upload("acme", "b.tar", 4096); // 1 block
    ledger.record_upload("acme", "c.tar", 1); // 1 block
    assert_eq!(ledger.tenant_usage("acme"), 12_288 + 4_096 + 4_096);
    assert_eq!(ledger.object_count("acme"), 3);
}

#[test]
fn reupload_replaces_previous_charge() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "app.img", 4 * BLOCK);
    ledger.record_upload("acme", "app.img", BLOCK);
    assert_eq!(ledger.tenant_usage("acme"), BLOCK);
    assert_eq!(ledger.object_count("acme"), 1);
}

#[test]
fn remove_frees_the_charge() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a.tar", 3 * BLOCK);
    ledger.record_upload("acme", "b.tar", BLOCK);
    assert!(ledger.remove("acme", "a.tar"));
    assert_eq!(ledger.tenant_usage("acme"), BLOCK);
    assert_eq!(ledger.object_count("acme"), 1);
}

#[test]
fn remove_unknown_key_is_a_noop() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a.tar", BLOCK);
    assert!(!ledger.remove("acme", "zzz"));
    assert!(!ledger.remove("nobody", "a.tar"));
    assert_eq!(ledger.tenant_usage("acme"), BLOCK);
}

#[test]
fn usage_is_isolated_per_tenant() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a.tar", BLOCK);
    ledger.record_upload("blue-sky", "a.tar", 2 * BLOCK);
    assert_eq!(ledger.tenant_usage("acme"), BLOCK);
    assert_eq!(ledger.tenant_usage("blue-sky"), 2 * BLOCK);
    assert_eq!(ledger.tenant_usage("nobody"), 0);
    assert_eq!(ledger.total_usage(), 3 * BLOCK);
}

#[test]
fn landing_exactly_on_quota_is_allowed() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a.tar", BLOCK);
    ledger.record_upload("acme", "b.tar", BLOCK);
    // 8192 used + 4096 new = 12288 == quota: allowed.
    assert!(!ledger.would_exceed("acme", BLOCK, 3 * BLOCK));
    assert!(ledger.would_exceed("acme", BLOCK + 1, 3 * BLOCK));
}

#[test]
fn largest_tenants_sorts_desc_then_by_name() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a", 2 * BLOCK);
    ledger.record_upload("zenith", "a", BLOCK);
    ledger.record_upload("blue-sky", "a", BLOCK);
    assert_eq!(
        ledger.largest_tenants(10),
        vec![
            ("acme".to_string(), 2 * BLOCK),
            ("blue-sky".to_string(), BLOCK),
            ("zenith".to_string(), BLOCK),
        ]
    );
}

#[test]
fn largest_tenants_truncates_to_n() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("acme", "a", 3 * BLOCK);
    ledger.record_upload("blue-sky", "a", 2 * BLOCK);
    ledger.record_upload("zenith", "a", BLOCK);
    assert_eq!(
        ledger.largest_tenants(1),
        vec![("acme".to_string(), 3 * BLOCK)]
    );
}

#[test]
fn five_gib_checkpoint_is_charged_in_full() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("ml-platform", "ckpt-0041.bin", 5 * GIB);
    assert_eq!(ledger.tenant_usage("ml-platform"), 5 * GIB);
}

#[test]
fn four_gib_firmware_image_still_counts() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("embedded", "firmware.iso", 4 * GIB);
    assert_eq!(ledger.tenant_usage("embedded"), 4 * GIB);
    assert_eq!(ledger.total_usage(), 4 * GIB);
}

#[test]
fn multi_gib_objects_total_exactly() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("ml-platform", "ckpt-a.bin", 5 * GIB);
    ledger.record_upload("ml-platform", "ckpt-b.bin", 6 * GIB);
    ledger.record_upload("ml-platform", "notes.txt", 100); // one block
    assert_eq!(ledger.tenant_usage("ml-platform"), 11 * GIB + BLOCK);
}

#[test]
fn quota_gate_catches_a_heavy_tenant() {
    let mut ledger = UsageLedger::new();
    ledger.record_upload("media", "raw-footage.mov", 5 * GIB);
    ledger.record_upload("media", "proxy.mov", 2 * GIB);
    // 7 GiB held; adding 1 GiB against an 7.5 GiB quota must be refused.
    assert!(ledger.would_exceed("media", GIB, 7 * GIB + GIB / 2));
    // ... and a 16 GiB quota still has room.
    assert!(!ledger.would_exceed("media", GIB, 16 * GIB));
}

#[test]
fn largest_tenants_ranks_heavy_hoarder_first() {
    let mut ledger = UsageLedger::new();
    // One tenant holds a single 5 GiB disk image ...
    ledger.record_upload("ml-platform", "snapshot.img", 5 * GIB);
    // ... the other holds 2 GiB spread over four 512 MiB shards.
    for i in 0..4 {
        ledger.record_upload("web-assets", &format!("shard-{i}"), 512 * MIB);
    }
    assert_eq!(
        ledger.largest_tenants(2),
        vec![
            ("ml-platform".to_string(), 5 * GIB),
            ("web-assets".to_string(), 2 * GIB),
        ]
    );
}
