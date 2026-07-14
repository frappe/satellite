"""Subdomain — a bench-site label under the one regional wildcard.

The routing key is the identity (autoname `field:subdomain`, fleet-wide unique) and
the target VM is fixed once chosen — repointing a live subdomain at a different VM is a
delete-and-recreate, not an in-place edit, so the proxy map change is explicit. Insert
/ active-toggle / delete each reconcile the proxy fleet (a mapping change reaches the
edge without an operator running a reconcile by hand). Ported from Atlas (spec/12,
spec/18) as a guest-plane concern of the provisioner/orchestrator split.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from satellite.routing.desired import routing_address
from satellite.services.routing import enqueue_reconcile

IMMUTABLE_AFTER_INSERT = (
	"subdomain",
	"virtual_machine",
)


class Subdomain(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active: DF.Check
		address: DF.Data
		subdomain: DF.Data
		virtual_machine: DF.Link
	# end: auto-generated types

	def validate(self) -> None:
		self._validate_immutability()
		self.address = routing_address(self.virtual_machine)

	def after_insert(self) -> None:
		"""Auto-reconcile: a new active mapping changes the region's served map, so push
		it to the fleet."""
		enqueue_reconcile()

	def on_update(self) -> None:
		"""The routing key and target VM are immutable, so `active` is the only mutable
		field that changes the served map. Reconcile only when it actually flipped — a
		no-op save shouldn't SSH the whole fleet."""
		original = self.get_doc_before_save()
		if original and original.active != self.active:
			enqueue_reconcile()

	def on_trash(self) -> None:
		"""Deleting an active mapping drops it from the served map; reconcile so the proxy
		fleet stops routing the subdomain."""
		enqueue_reconcile()

	def _validate_immutability(self) -> None:
		"""Lock the routing key and its target VM once written. The `address` is the one
		mutable field (it tracks the VM's routing /128), and `active` toggles the mapping
		in/out of the served map."""
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			if getattr(original, field) != getattr(self, field):
				frappe.throw(f"{field} is immutable after insert")
