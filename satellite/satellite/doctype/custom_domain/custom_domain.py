"""Custom Domain — an arbitrary external host (shop.acme.com) routed to a bench site.

The full-FQDN sibling of `Subdomain`. A `Subdomain` keys on a bare label under the one
regional wildcard (terminated at the proxy under the wildcard cert); a `Custom Domain`
keys on the whole host the customer owns. TLS is SNI passthrough — the proxy reads the
SNI at L4 and forwards the raw TLS stream to the backend VM's :443; the bench
terminates TLS with its own cert. So a row only declares "this host → that backend":
insert / active-toggle / delete each reconcile the proxy fleet's :443 SNI + :80 ACME
maps. Ported from Atlas (spec/18 Phase 2) as a guest-plane concern.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from satellite.routing.desired import routing_address
from satellite.services.routing import enqueue_reconcile

IMMUTABLE_AFTER_INSERT = (
	"domain",
	"virtual_machine",
)


class CustomDomain(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active: DF.Check
		address: DF.Data
		domain: DF.Data
		site: DF.Data | None
		status: DF.Literal["Active", "Failed"]
		virtual_machine: DF.Link
	# end: auto-generated types

	def validate(self) -> None:
		self._validate_immutability()
		self.address = routing_address(self.virtual_machine)

	def after_insert(self) -> None:
		"""Auto-reconcile: a new active custom-domain mapping changes the region's served
		map, so push it to the fleet."""
		enqueue_reconcile()

	def on_update(self) -> None:
		"""`active` is the only field that changes the served maps (it drops the row from
		both the :443 SNI and :80 ACME maps), so reconcile when it flips. A no-op save does
		not SSH the fleet."""
		original = self.get_doc_before_save()
		if original and original.active != self.active:
			enqueue_reconcile()

	def on_trash(self) -> None:
		"""Deleting an active mapping drops it from the served SNI map; reconcile so the
		proxy fleet stops forwarding the custom domain."""
		enqueue_reconcile()

	def _validate_immutability(self) -> None:
		if self.is_new():
			return
		original = self.get_doc_before_save()
		if not original:
			return
		for field in IMMUTABLE_AFTER_INSERT:
			if getattr(original, field) != getattr(self, field):
				frappe.throw(f"{field} is immutable after insert")
