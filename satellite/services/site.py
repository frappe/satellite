"""Site service — install a Frappe site into a VM's baked bench (spec/14, spec/28).

The guest-plane service installation: binding `site` to a VM deploys the site into its
golden bench (rename the baked site to the FQDN, regenerate the vhost, confirm serving).
The per-site inputs ride the binding `config` — the FQDN Satellite owns (routing) plus
the opaque Central handoff params (central_endpoint/auth_token, admin mode/domain) it
forwards into the guest without interpreting. Central never calls here: the binding is
created off the mirrored VM, and the deployed site phones Central back over its baked-in
central_endpoint.
"""

from __future__ import annotations

import json

import frappe

from satellite.bench import deploy_site, readiness_path_for_mode, wait_for_http


class SiteService:
	def apply(self, vm, binding) -> None:
		"""Deploy the site into the guest, then block on the HTTP readiness gate
		(Contract B) so the binding only reports Applied once Frappe actually serves the
		FQDN."""
		config = json.loads(binding.config) if binding.config else {}
		fqdn = config.get("fqdn")
		if not fqdn:
			frappe.throw("site binding config needs an 'fqdn'")
		deploy_site(
			vm.name,
			fqdn,
			central_endpoint=config.get("central_endpoint"),
			central_auth_token=config.get("central_auth_token"),
			mode=config.get("mode"),
			admin_domain=config.get("admin_domain"),
		)
		mode = config.get("mode") or vm.build_mode
		wait_for_http(vm.guest_ipv6, fqdn, path=readiness_path_for_mode(mode))

	def withdraw(self, vm, binding) -> None:
		"""No-op: the baked site is the VM's own identity, so unbinding the service does
		not drop it — that is VM termination (deregister removes the mirror). Kept for the
		handler contract; idempotent."""
