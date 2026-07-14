"""The webhook receiver — the endpoint an Atlas POSTs a VM lifecycle event to
(spec/28). Verifies the HMAC signature against the sending Atlas's shared secret,
resolves which Atlas sent it (by base_url), and enqueues a registration sync.

allow_guest because it is machine-to-machine (no Frappe session); the HMAC over the
raw body IS the authentication. The body carries only identity — Satellite reads the
full VM back through the Atlas read API during the sync.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import frappe

from satellite.satellite.doctype.atlas.atlas import Atlas

SIGNATURE_HEADER = "X-Atlas-Signature"


def _reject(message: str) -> dict:
	"""Reject with a clean 403. The body is unauthenticated until the HMAC checks out,
	so a bad sender/signature is a permission failure — returned, not raised, so it is a
	tidy 403 rather than a 500 (and never trips the dev server's debugger)."""
	frappe.local.response.http_status_code = 403
	return {"error": message}


def _authentic(atlas: str, body: bytes) -> bool:
	secret = frappe.get_doc("Atlas", atlas).get_password("webhook_secret", raise_exception=False) or ""
	expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
	return hmac.compare_digest(expected, frappe.get_request_header(SIGNATURE_HEADER) or "")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive() -> dict:
	body = frappe.request.get_data()  # exact bytes Atlas signed
	data = json.loads(body or b"{}")

	atlas = Atlas.for_base_url(data.get("atlas") or "")
	if not atlas:
		return _reject("unknown Atlas")
	if not _authentic(atlas, body):
		return _reject("bad signature")

	frappe.enqueue(
		"satellite.registration.handle_event",
		queue="short",
		atlas=atlas,
		event=data.get("event"),
		remote_id=data.get("virtual_machine"),
	)
	return {"ok": True}
