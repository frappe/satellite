"""Lets Encrypt Settings — the ACME account the Let's Encrypt provider issues under."""

from __future__ import annotations

from frappe.model.document import Document


class LetsEncryptSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_email: DF.Data | None
		acme_directory_url: DF.Data | None
	# end: auto-generated types

	pass
