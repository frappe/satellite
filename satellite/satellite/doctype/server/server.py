# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class Server(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		atlas: DF.Link
		ipv4: DF.Data | None
		remote_id: DF.Data
		server_status: DF.Data | None
	# end: auto-generated types

	_DOCTYPE_NAME = "Server"
