"""Satellite's VM services — one per service concern that attaches to Atlas's
generic VM lifecycle through the seam Atlas exposes (spec/28 §3A).

Each service is a plain class implementing the `VMService` protocol Atlas defines
in `atlas.atlas.vm_services`; it is registered by dotted path in the
`atlas_vm_services` hook. A service holds the DECISION (what routing map / peer set
/ deploy step a VM needs) and ships setup scripts, but performs NO infrastructure
itself: every host/guest effect goes through Atlas's exposed execution API
(`run_host_script`, `run_guest_script`). That invariant — satellite never opens SSH,
calls a provider, or touches a host/guest directly — is what keeps satellite a pure
controller layer and lets its whole surface be tested against Atlas's Fake seam with
no cloud droplet.
"""
