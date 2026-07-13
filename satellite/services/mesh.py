"""MeshService — the private-plane host mesh, as a satellite VM service.

This is the cleanest first extraction of the boundary (spec/28 §6, phase 3): the
WireGuard host mesh that carries each tenant's `fdaa::` private plane
(Atlas spec/25) is *overlay* networking — it connects VMs to each other — so it
belongs to satellite, not to Atlas core (which owns only a VM's own base address).

The full mesh reconciles per-host WireGuard `AllowedIPs` over the host's IPv4. This
controller keeps that shape but reduces the on-host artifact to its essence for the
seam to stand on: a per-host registry file listing the private mesh address of every
satellite-managed VM resident on that host (`/etc/satellite/mesh/peers`). A VM's
provision adds its line; its terminate removes it. Both effects run through Atlas's
exposed `run_host_script` — satellite ships the `satellite-mesh-*` shell verbs and
Atlas stages + runs them on the host; satellite opens no SSH itself.

Every hook is a no-op for a VM that is not `satellite_managed`, so an ordinary
operator VM (a bare compute box) is never touched, and a bare Atlas with satellite
uninstalled never sees this at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import frappe
from frappe import _

from atlas.atlas.vm_services import run_host_script

if TYPE_CHECKING:
	from atlas.atlas.doctype.virtual_machine.virtual_machine import VirtualMachine

# The host-plane verbs satellite ships (satellite/scripts/*.sh), run by Atlas's
# runner on the host. Named-constant so the service, the tests, and the scripts
# agree on one string.
MESH_ADD = "satellite-mesh-add"
MESH_REMOVE = "satellite-mesh-remove"


class MeshService:
	name = "mesh"

	def applies_to(self, vm: "VirtualMachine") -> bool:
		"""Cheap gate: only VMs flagged onto the private mesh. Reads the satellite
		custom field only — no DB fan-out."""
		return bool(vm.get("satellite_managed"))

	def validate(self, vm: "VirtualMachine") -> None:
		"""A mesh member must belong to a Tenant: the private plane is per-tenant (its
		address derives from the tenant `/48`, Atlas spec/25), so a tenant-less VM has
		no mesh identity to advertise. Mirrors Atlas's own dark-VM identity rule."""
		if not vm.tenant:
			frappe.throw(_("A satellite-managed VM needs a Tenant to join its private mesh"))

	def provision_variables(self, vm: "VirtualMachine") -> dict:
		"""No provision-time guest var: this minimal host-mesh registry lives entirely
		on the host (on_provision writes the peer there through run_host_script), so the
		guest needs nothing injected. Returned empty on purpose — the seam still calls
		it, and a different service (routing's ROUTING_BASE_URL, say) is where a real
		provision var rides. Note the core provision-vm task is a STRICT typed CLI: a
		service must only inject flags that task declares, so a satellite-unique guest
		var waits for the provision task to become env-generic (spec/28 §3A / phase 4)."""
		return {}

	def on_provision(self, vm: "VirtualMachine") -> None:
		"""Publish this VM onto its host's mesh registry after provision — the overlay
		analogue of Atlas's core `_reconcile_host_mesh`, but driven entirely through
		the exposed executor. Runs after the provision has committed."""
		run_host_script(
			vm.server,
			MESH_ADD,
			{"VIRTUAL_MACHINE_NAME": vm.name, "MESH_PEER": self.peer_address(vm)},
			timeout_seconds=120,
		)

	def on_status_change(self, vm: "VirtualMachine", old: str, new: str) -> None:
		"""The mesh membership is keyed on existence, not run state, so an ordinary
		start/stop needs no reconcile. Left as an explicit no-op for the seam."""
		return

	def teardown(self, vm: "VirtualMachine") -> None:
		"""Withdraw this VM from its host's mesh registry on terminate. Idempotent (the
		remove verb is a no-op if the line is already gone), matching the seam's
		teardown contract."""
		run_host_script(
			vm.server,
			MESH_REMOVE,
			{"VIRTUAL_MACHINE_NAME": vm.name},
			timeout_seconds=120,
		)

	def peer_address(self, vm: "VirtualMachine") -> str:
		"""The address this VM advertises on the mesh: its derived private `/128` when
		it has one, else its public `/128` — a legible, stable identifier either way."""
		return vm.get("private_address") or vm.get("ipv6_address") or vm.name
