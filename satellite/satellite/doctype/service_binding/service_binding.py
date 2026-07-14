# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


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
