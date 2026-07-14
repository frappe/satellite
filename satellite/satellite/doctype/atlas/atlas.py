# Copyright (c) 2026, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Atlas(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Data | None
		api_secret: DF.Password | None
		base_url: DF.Data
		enabled: DF.Check
		title: DF.Data
		webhook_secret: DF.Password | None
	# end: auto-generated types

	_DOCTYPE_NAME = "Atlas"

	def base(self) -> str:
		"""The base URL without a trailing slash, for composing API paths."""
		return (self.base_url or "").rstrip("/")

	@staticmethod
	def for_base_url(base_url: str) -> str | None:
		"""The Atlas record whose base_url matches an incoming webhook's `atlas` field,
		so the receiver can resolve which provisioner sent it. None if unknown."""
		normalized = (base_url or "").rstrip("/")
		for name, url in frappe.get_all("Atlas", fields=["name", "base_url"], as_list=True):
			if (url or "").rstrip("/") == normalized:
				return name
		return None
