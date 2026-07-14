"""TLS Certificate — the record of one issued regional wildcard cert.

One row per wildcard domain, holding the on-disk PEM paths (the bytes live on the
Satellite node, out of the DB — mirroring the SSH key path) and the validity window the
renew scheduler reads. `services.tls` writes it after issuance and pushes the PEMs to
the proxy fleet.
"""

from __future__ import annotations

from frappe.model.document import Document


class TLSCertificate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		domain: DF.Data
		fullchain_path: DF.Data | None
		last_error: DF.SmallText | None
		not_after: DF.Datetime | None
		not_before: DF.Datetime | None
		privkey_path: DF.Data | None
		status: DF.Literal["Active", "Failed"]
	# end: auto-generated types

	pass
