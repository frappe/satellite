# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class Service(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		config: DF.Code | None
		enabled: DF.Check
		handler_path: DF.Data
		service_key: DF.Data
		title: DF.Data | None
	# end: auto-generated types

	_DOCTYPE_NAME = "Service"
