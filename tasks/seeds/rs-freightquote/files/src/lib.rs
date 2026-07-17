//! rs-freightquote — LTL freight-rate quoting engine.
//!
//! Module plan is fixed by the pricing-desk ticket; every module below is a
//! stub waiting for its implementation. The acceptance suite in tests/ and
//! the rate sheet in tariffs/ are contract — do not touch them.

pub mod audit;
pub mod brackets;
pub mod money;
pub mod quote;
pub mod ratefile;
pub mod surcharge;
pub mod tariff;
pub mod zones;
