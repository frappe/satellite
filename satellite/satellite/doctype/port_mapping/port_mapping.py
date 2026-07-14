"""Port Mapping — a public TCP port forwarded to a backend VM's service port.

The L4 twin of `Subdomain`: the routing key is the allocated `public_port` (read-only)
and the target VM + service port are fixed once chosen — repointing a live mapping is a
delete-and-recreate. `before_insert` allocates the lowest free port in the pool so the
`{protocol}-{public_port}` autoname picks it up; insert / active-toggle / delete each
reconcile the proxy fleet. Ported from Atlas (spec/17-tcp-proxy) as a guest-plane
concern.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from satellite.routing.desired import routing_address
from satellite.routing.ports import allocate_port
from satellite.services.routing import enqueue_reconcile

IMMUTABLE_AFTER_INSERT = (
	"virtual_machine",
	"target_port",
)


class PortMapping(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active: DF.Check
		address: DF.Data
		protocol: DF.Literal["tcp", "ssh", "mariadb"]
		public_port: DF.Int
		target_port: DF.Int
		virtual_machine: DF.Link
	# end: auto-generated types

	def before_insert(self) -> None:
		"""Allocate the public port: the lowest port in the pool not already held by an
		active OR inactive mapping. Runs before set_new_name so the
		`{protocol}-{public_port}` autoname picks it up."""
		self.public_port = allocate_port()

	def validate(self) -> None:
		self._validate_immutability()
		self.address = routing_address(self.virtual_machine)

	def after_insert(self) -> None:
		"""Auto-reconcile: a new active mapping changes the region's served port map, so
		push it to the fleet."""
		enqueue_reconcile()

	def on_update(self) -> None:
		"""`public_port`, `virtual_machine`, and `target_port` are all read-only/immutable,
		so `active` is the only mutable field that changes the served map. Reconcile only
		when it actually flipped."""
		original = self.get_doc_before_save()
		if original and original.active != self.active:
			enqueue_reconcile()

	def on_trash(self) -> None:
		"""Deleting an active mapping drops it from the served map; reconcile so the proxy
		fleet stops forwarding the port."""
		enqueue_reconcile()

	def _validate_immutability(self) -> None:
		"""Lock the target VM and service port once written. `public_port` is read-only
		(allocated), and `active` toggles the mapping in/out of the served map."""
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			if getattr(original, field) != getattr(self, field):
				frappe.throw(f"{field} is immutable after insert")
