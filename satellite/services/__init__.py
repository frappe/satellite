"""Satellite's service handlers — one per guest-plane service concern (routing,
proxy, bench/site) that Satellite applies to the VMs an Atlas hands it (spec/28, the
provisioner/orchestrator split). Host-plane fabric (mesh, gateway) stays in Atlas.

Each handler is a plain class named by a `Service` catalog row's `handler_path`. It
implements `apply(vm, binding)` and `withdraw(vm, binding)`, holding the DECISION for
one concern (the peer set, routing map, deploy step a VM needs). Unlike the old
co-installed seam, a handler performs the infrastructure ITSELF: Satellite is a
separate deployment with its own SSH engine, so a handler reaches the host/guest over
`satellite.ssh` and never imports Atlas — it learns a VM's SSH targets from the mirror
row Satellite registered off the Atlas read API.
"""
