# Database connection pool pressure

## Runbook ID: `RB-DB-POOL-01`

### Symptoms and checks

Connection acquisition timeouts can occur when active connections reach the configured pool limit or when connection wait counts rise. Compare active and maximum connections, wait counts, request latency, and the affected service's connection timeout logs. Check whether a recent release or abnormal traffic change precedes the symptom.

### Bounded response

1. Confirm pool occupancy and wait counts over more than one sampling interval.
2. Check database health and the number of long-running or idle transactions using the approved operations console.
3. If the service owner confirms headroom and the database is healthy, recycle one affected application instance at a time and verify that connection acquisition and request errors improve.
4. Escalate to the database on-call if saturation returns, the database reports resource pressure, or recovery would require changing pool limits.

Do not raise the pool limit or terminate sessions without an owner review and a database capacity check.
