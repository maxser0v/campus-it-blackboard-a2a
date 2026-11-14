# Campus DNS resolution failure

## Runbook ID: `RB-DNS-01`

### Symptoms and checks

Elevated NXDOMAIN responses or resolver logs with no address can make a service unreachable while application CPU and request volume remain ordinary. Compare answers from the affected campus resolver and an approved independent resolver; confirm the authoritative record and its recent change history.

### Bounded response

1. Verify the exact hostname, resolver, client network, and returned DNS status.
2. Check the authoritative zone and recent approved record changes with the DNS owner.
3. If the record is incorrect and the owner approves, restore the last known-good value using the DNS change process.
4. Verify resolution from the affected network and monitor service errors before closing the incident.

Do not flush broad resolver caches or alter authoritative records without DNS owner approval.
