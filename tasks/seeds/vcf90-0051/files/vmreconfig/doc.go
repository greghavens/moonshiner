// Package vmreconfig applies a reconfiguration change set to a virtual machine
// through the VMware Cloud Foundation 9.0 vSphere Automation API for vCenter.
//
// A change set is applied in order: read the power state, power the machine
// down, resize memory, resize CPU, add virtual disks, then restore the power
// state. The steps are not independent - the memory and CPU updates are only
// accepted while the machine is powered off - so a change set is applied as a
// sequence, and a step that fails stops the ones behind it.
//
// The point of this package is what happens then. A change set that fails part
// way through has still changed the machine, and the caller has to be told
// exactly which steps landed, which one failed and why, which were never
// attempted, and what power state the machine was actually left in. Apply
// therefore returns a fully populated Report alongside its error rather than
// discarding the work it already did.
//
// The wire contract is docs/contract.json, derived from the vSphere Automation
// API specification recorded in docs/official_sources.json. It is the authority
// on paths, the action query parameter, the session header, success status
// codes and the required versus optional property sets. The rule that catches
// most implementations is contract.omitEmptyRule: a property the caller did not
// set must be absent from the serialized request, not present and empty, all
// the way down through nested objects.
//
// This package is a stub. Implement Config, Client, New, the change types, the
// report types, APIError, Apply and the error sentinels, and add table-driven
// tests alongside them.
package vmreconfig
