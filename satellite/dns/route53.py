"""Route 53 DNS provider — DNS-01 via AWS Route 53.

Reads `Route53 Settings` for the IAM credentials (the secret from the encrypted
Password field). The TXT-record dance is certbot's `dns-route53` plugin's job; this
class only supplies the plugin flag and the AWS credential env. `authenticate()` proves
the credentials reach the account by listing hosted zones. Ported from Atlas.
"""

from __future__ import annotations

import frappe

from satellite.dns import register
from satellite.dns.base import AuthResult, DnsProvider, WildcardTargets

# Round-robin A/AAAA TTL. Short so a proxy rebuild (new /128) or reserved-IP reattach
# propagates quickly — the records are reconciled, not set-and-forget.
WILDCARD_TTL_SECONDS = 60


@register
class Route53DnsProvider(DnsProvider):
	provider_type = "Route53"

	def __init__(self) -> None:
		settings = frappe.get_single("Route53 Settings")
		self.access_key_id = settings.access_key_id
		self.secret_access_key = settings.get_password("secret_access_key", raise_exception=False)
		self.region = settings.region or "us-east-1"

	def _client(self):
		"""A boto3 route53 client from the configured creds. Import is local so the
		boto3 dependency never loads at module import (the registry imports this module on
		every `for_dns_provider_type`)."""
		import boto3

		return boto3.client(
			"route53",
			aws_access_key_id=self.access_key_id,
			aws_secret_access_key=self.secret_access_key,
			region_name=self.region,
		)

	def authenticate(self) -> AuthResult:
		try:
			client = self._client()
		except ImportError:
			return AuthResult(ok=False, error="boto3 not installed on the Satellite node")
		try:
			response = client.list_hosted_zones(MaxItems="1")
		except Exception as exception:
			return AuthResult(ok=False, error=str(exception))
		zones = response.get("HostedZones") or []
		label = zones[0]["Name"].rstrip(".") if zones else "no hosted zones"
		return AuthResult(ok=True, account_label=label)

	def upsert_wildcard(self, domain: str, targets: WildcardTargets) -> list[str]:
		client = self._client()
		zone_id = self._hosted_zone_id(client, domain)
		record_name = f"*.{domain}"
		changes = []
		for record_type, values in (("A", targets.ipv4), ("AAAA", targets.ipv6)):
			if not values:
				# Never publish a wildcard pointing at nothing; leave any existing record of
				# this type untouched (a half-empty fleet shouldn't blackhole).
				continue
			changes.append(
				{
					"Action": "UPSERT",
					"ResourceRecordSet": {
						"Name": record_name,
						"Type": record_type,
						"TTL": WILDCARD_TTL_SECONDS,
						"ResourceRecords": [{"Value": value} for value in values],
					},
				}
			)
		if not changes:
			frappe.throw(f"upsert_wildcard for {record_name}: no proxy addresses to publish")
		client.change_resource_record_sets(
			HostedZoneId=zone_id,
			ChangeBatch={"Comment": f"Satellite regional wildcard for {domain}", "Changes": changes},
		)
		return [f"{change['ResourceRecordSet']['Type']} {record_name}" for change in changes]

	def _hosted_zone_id(self, client, domain: str) -> str:
		"""The hosted zone that owns `domain`, found by walking up the name (the zone may
		be `<domain>` or any parent). Picks the LONGEST matching zone suffix (the most
		specific zone wins)."""
		paginator = client.get_paginator("list_hosted_zones")
		best: tuple[int, str] | None = None
		for page in paginator.paginate():
			for zone in page.get("HostedZones", []):
				if zone.get("Config", {}).get("PrivateZone"):
					continue
				zone_name = zone["Name"].rstrip(".")
				if domain == zone_name or domain.endswith("." + zone_name):
					if best is None or len(zone_name) > best[0]:
						best = (len(zone_name), zone["Id"])
		if best is None:
			frappe.throw(f"no Route 53 hosted zone found for {domain!r}")
		return best[1]

	def credential_env(self) -> dict[str, str]:
		return {
			"AWS_ACCESS_KEY_ID": self.access_key_id,
			"AWS_SECRET_ACCESS_KEY": self.secret_access_key,
			"AWS_DEFAULT_REGION": self.region,
		}

	def certbot_authenticator(self) -> str:
		# `certbot-dns-route53` discovers the hosted zone from the domain name at issue
		# time, so no zone-id is needed — just name the authenticator.
		return "route53"
