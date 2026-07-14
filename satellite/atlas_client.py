"""HTTP client for an Atlas's read API (spec/28).

Satellite never imports Atlas — it reads a provisioner's VMs/Servers over HTTP, authed
with the api_key/secret stored on the `Atlas` record (a System Manager token on that
Atlas, exactly what `atlas/atlas/api/satellite.py` guards with `only_for`). One client
per Atlas; a Satellite federates many.
"""

from __future__ import annotations

import frappe

API = "atlas.atlas.api.satellite"
TIMEOUT_SECONDS = 30


class AtlasClient:
	def __init__(self, atlas: str) -> None:
		self.atlas = frappe.get_doc("Atlas", atlas)

	def _get(self, method: str, **params) -> object:
		import requests

		secret = self.atlas.get_password("api_secret", raise_exception=False) or ""
		response = requests.get(
			f"{self.atlas.base()}/api/method/{API}.{method}",
			params={k: v for k, v in params.items() if v is not None},
			headers={"Authorization": f"token {self.atlas.api_key}:{secret}"},
			timeout=TIMEOUT_SECONDS,
		)
		response.raise_for_status()
		# Frappe wraps a whitelisted method's return value in {"message": ...}.
		return response.json().get("message")

	def get_virtual_machine(self, remote_id: str) -> dict:
		return self._get("get_virtual_machine", name=remote_id)

	def list_virtual_machines(self, modified_after: str | None = None) -> list[dict]:
		return self._get("list_virtual_machines", modified_after=modified_after) or []

	def get_server(self, remote_id: str) -> dict:
		return self._get("get_server", name=remote_id)
