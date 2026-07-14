# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


def handler_for(service: str):
	"""Instantiate the handler class a Service names (spec/28). This is the whole
	'how to add a service' story: a Service row points handler_path at a class with
	apply(vm, binding)/withdraw(vm, binding), and it is dispatched here."""
	return frappe.get_attr(frappe.db.get_value("Service", service, "handler_path"))()


class ServiceBinding(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		binding_status: DF.Literal["Pending", "Applied", "Failed", "Withdrawn"]
		config: DF.Code | None
		last_error: DF.SmallText | None
		service: DF.Link
		virtual_machine: DF.Link
	# end: auto-generated types

	_DOCTYPE_NAME = "Service Binding"

	def after_insert(self) -> None:
		"""Applying a binding drives its service handler at the VM — Satellite reaches
		the host/guest over its own SSH. A failure is recorded as Failed (the row
		persists so an operator can see + retry), never rolled back."""
		vm = frappe.get_doc("Virtual Machine", self.virtual_machine)
		try:
			handler_for(self.service).apply(vm, self)
		except Exception as exception:
			self.db_set({"binding_status": "Failed", "last_error": str(exception)[:500]})
			frappe.log_error(f"Service Binding {self.name} apply failed: {exception}", "Service Binding")
			return
		self.db_set({"binding_status": "Applied", "last_error": None})

	def on_trash(self) -> None:
		"""Withdraw the service effect on delete. Best-effort — a withdraw failure is
		logged but never blocks teardown (the handler's withdraw is idempotent)."""
		try:
			vm = frappe.get_doc("Virtual Machine", self.virtual_machine)
			handler_for(self.service).withdraw(vm, self)
		except Exception as exception:
			frappe.log_error(f"Service Binding {self.name} withdraw failed: {exception}", "Service Binding")
