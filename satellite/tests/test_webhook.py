import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from satellite import webhook

ATLAS = "wh-test-atlas"
BASE = "http://atlas.wh"
SECRET = "wh-secret"


def _ensure_atlas() -> None:
	if not frappe.db.exists("Atlas", ATLAS):
		frappe.get_doc(
			{"doctype": "Atlas", "title": ATLAS, "base_url": BASE, "webhook_secret": SECRET}
		).insert(ignore_permissions=True)


class TestWebhook(IntegrationTestCase):
	def setUp(self) -> None:
		_ensure_atlas()

	def _receive(self, payload: dict, secret: str = SECRET, signature: str | None = None):
		body = json.dumps(payload).encode()
		sig = signature or hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
		request = MagicMock()
		request.get_data.return_value = body
		with (
			patch.object(webhook.frappe, "request", request),
			patch.object(webhook.frappe, "get_request_header", return_value=sig),
			patch.object(webhook.frappe, "enqueue") as enqueue,
		):
			return webhook.receive(), enqueue

	def test_valid_signature_enqueues_sync(self) -> None:
		result, enqueue = self._receive(
			{"atlas": BASE, "event": "vm.registered", "virtual_machine": "vm-1"}
		)
		self.assertEqual(result, {"ok": True})
		enqueue.assert_called_once()
		_, kwargs = enqueue.call_args
		self.assertEqual(kwargs["atlas"], ATLAS)
		# `vm_event`, not `event` — enqueue reserves `event`, so the handler param must be
		# a non-reserved name or the event never reaches handle_event (regression: a valid
		# webhook 200'd but the sync job crashed on the missing arg).
		self.assertEqual(kwargs["vm_event"], "vm.registered")
		self.assertNotIn("event", kwargs)
		self.assertEqual(kwargs["remote_id"], "vm-1")

	def test_bad_signature_rejected(self) -> None:
		result, enqueue = self._receive(
			{"atlas": BASE, "event": "vm.registered", "virtual_machine": "vm-1"}, signature="deadbeef"
		)
		self.assertEqual(frappe.local.response.http_status_code, 403)
		self.assertIn("error", result)
		enqueue.assert_not_called()

	def test_unknown_atlas_rejected(self) -> None:
		result, enqueue = self._receive(
			{"atlas": "http://nope", "event": "vm.registered", "virtual_machine": "vm-1"}
		)
		self.assertEqual(frappe.local.response.http_status_code, 403)
		self.assertIn("error", result)
		enqueue.assert_not_called()
