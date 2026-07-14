"""MeshService — the real cross-host WireGuard mesh, as a Satellite service handler
(spec/28 Phase 1). Binding a VM to `mesh` puts its /128 on the fabric; unbinding withdraws
it. Because the mesh is CROSS-HOST, both apply and withdraw reconcile the WHOLE fabric for
the VM's Atlas — a single VM's /128 joining/leaving changes every OTHER host's AllowedIPs.

The VM's own status already gates residency (a Terminated VM is excluded by
_residents_by_host), so withdraw is just "reconcile now that the row is gone/terminated".
This replaces the Phase-0 reduced demo (a /etc/satellite/mesh/peers registry file) with the
real WireGuard reconcile ported to Satellite's own SSH.
"""

from __future__ import annotations

import frappe

from satellite.host_mesh import reconcile_host_mesh

_LOG = "Satellite Mesh"


class MeshService:
	def apply(self, vm, binding) -> None:
		"""Advertise this VM's /128 fleet-wide: reconcile the whole mesh for its Atlas."""
		reconcile_host_mesh(vm.atlas)

	def withdraw(self, vm, binding) -> None:
		"""Withdraw this VM's /128: reconcile the whole mesh for its Atlas (the row is now
		Terminated/gone, so residency excludes it)."""
		reconcile_host_mesh(vm.atlas)


def reconcile_all_meshes() -> None:
	"""Scheduled backstop sweep (hooks.scheduler_events): re-reconcile every registered
	Atlas's mesh so a rebooted/drifted host self-heals. Fail-open per Atlas — one Atlas's
	partition never wedges the others."""
	for atlas in frappe.get_all("Atlas", filters={"enabled": 1}, pluck="name"):
		try:
			reconcile_host_mesh(atlas)
		except Exception as exception:
			frappe.log_error(f"Mesh sweep failed for Atlas {atlas}: {exception}", _LOG)
