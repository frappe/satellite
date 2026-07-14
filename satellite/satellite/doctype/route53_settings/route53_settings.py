"""Route53 Settings — the AWS creds Satellite's Route53 DNS provider reads."""

from __future__ import annotations

from frappe.model.document import Document


class Route53Settings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_key_id: DF.Data | None
		region: DF.Data | None
		secret_access_key: DF.Password | None
	# end: auto-generated types

	pass
