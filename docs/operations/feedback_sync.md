# Feedback Confidence Sync Operations

**Document ID**: OPS-FEEDBACK-SYNC  
**Version**: 2.0  
**Last Updated**: 2026-01-05

## Overview

This document covers operational procedures for the feedback → confidence synchronization system that updates Knowledge Graph (KG) template rankings based on accumulated feedback.

### What It Does

1. **Aggregates feedback** from `kg.feedback_records` table
2. **Computes confidence scores** using weighted algorithm
3. **Updates `kg.nodes.confidence_score`** for templates with feedback
4. **Applies decay** to unused patterns (P4 feature)
5. **Logs history** to `kg.confidence_history` for audit

### Why Schedule It

- **Not latency-sensitive**: Confidence updates can lag minutes/hours without affecting production quality
- **Batching is efficient**: One bulk update is cheaper than per-feedback updates
- **External scheduler preferred**: Keep application stateless, avoid in-process timers
- **Operationally visible**: Scheduled jobs are easier to monitor than background threads

## Quick Start

### Combined Maintenance (Recommended)

```bash
# Run sync + decay in one operation
integration-coworker kg-maintenance --json

# Preview what would be decayed
integration-coworker kg-decay --dry-run
```

### One-Time Manual Sync

```bash
# Using integration-coworker CLI (preferred)
integration-coworker kg-maintenance

# Or sync only
python -c "
from integration_coworker.feedback.confidence import update_all_confidences
result = update_all_confidences(reason='manual_sync')
print(f'Updated {result[\"updated\"]} templates')
"
```

### One-Time Manual Decay

```bash
# Apply decay to unused patterns (P4)
integration-coworker kg-decay

# Or via Python
python -c "
from integration_coworker.feedback import apply_confidence_decay
decayed = apply_confidence_decay(decay_factor=0.95, min_confidence=0.1)
print(f'Decayed {decayed} patterns')
"
```

### Verify Operations

```bash
# Check recent history (syncs show feedback_count > 0, decays show reason='decay')
psql $DATABASE_URL -c "
SELECT node_key, old_confidence, new_confidence, feedback_count, reason, created_at
FROM kg.confidence_history
ORDER BY created_at DESC
LIMIT 10;
"
```

## Scheduling Options

### Option 1: systemd Timer (Recommended for Linux)

**Sync Timer** - runs every 15 minutes:

Create `/etc/systemd/system/integration-coworker-sync.service`:

```ini
[Unit]
Description=Integration Coworker Confidence Sync
After=network.target

[Service]
Type=oneshot
User=app
Environment=DATABASE_URL=postgres://user:pass@host:5432/db
WorkingDirectory=/opt/integration-coworker
ExecStart=/opt/integration-coworker/.venv/bin/integration-coworker kg-maintenance --skip-decay --json

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/integration-coworker-sync.timer`:

```ini
[Unit]
Description=Run confidence sync every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
```

**Decay Timer** - runs weekly (P4 feature):

Create `/etc/systemd/system/integration-coworker-decay.service`:

```ini
[Unit]
Description=Integration Coworker Confidence Decay (P4)
After=network.target

[Service]
Type=oneshot
User=app
Environment=DATABASE_URL=postgres://user:pass@host:5432/db
WorkingDirectory=/opt/integration-coworker
ExecStart=/opt/integration-coworker/.venv/bin/integration-coworker kg-decay --json

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/integration-coworker-decay.timer`:

```ini
[Unit]
Description=Apply decay to unused patterns weekly (Sunday 3am)

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable both:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now integration-coworker-sync.timer
sudo systemctl enable --now integration-coworker-decay.timer
systemctl list-timers | grep integration
```

### Option 2: Cron (Universal)

Add to crontab (`crontab -e`):

```cron
# Run confidence sync every 15 minutes
*/15 * * * * DATABASE_URL="postgres://..." /path/to/.venv/bin/integration-coworker kg-maintenance --skip-decay --json >> /var/log/confidence-sync.log 2>&1

# Run decay weekly on Sunday at 3am (P4 feature)
0 3 * * 0 DATABASE_URL="postgres://..." /path/to/.venv/bin/integration-coworker kg-decay --json >> /var/log/confidence-decay.log 2>&1
```

### Option 3: Kubernetes CronJob

**Sync CronJob** (every 15 minutes):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: kg-confidence-sync
  namespace: integration-coworker
spec:
  schedule: "*/15 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: sync
            image: your-registry/integration-coworker:latest
            command: ["integration-coworker", "kg-maintenance", "--skip-decay", "--json"]
            env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: database-credentials
                  key: url
          restartPolicy: OnFailure
```

**Decay CronJob** (weekly, P4 feature):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: kg-confidence-decay
  namespace: integration-coworker
spec:
  schedule: "0 3 * * 0"  # Sunday 3am
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: decay
            image: your-registry/integration-coworker:latest
            command: ["integration-coworker", "kg-decay", "--json"]
            env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: database-credentials
                  key: url
          restartPolicy: OnFailure
```

### Option 4: AWS Lambda + EventBridge

**Sync Lambda** (every 15 minutes):

```python
# sync_lambda.py
import os
import subprocess
import json

def handler(event, context):
    result = subprocess.run(
        ["integration-coworker", "kg-maintenance", "--skip-decay", "--json"],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["DB_URL"]},
    )
    return json.loads(result.stdout) if result.returncode == 0 else {"error": result.stderr}
```

EventBridge rule: `rate(15 minutes)`

**Decay Lambda** (weekly, P4 feature):

```python
# decay_lambda.py
import os
import subprocess
import json

def handler(event, context):
    result = subprocess.run(
        ["integration-coworker", "kg-decay", "--json"],
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": os.environ["DB_URL"]},
    )
    return json.loads(result.stdout) if result.returncode == 0 else {"error": result.stderr}
```

EventBridge rule: `cron(0 3 ? * SUN *)` (Sunday 3am UTC)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | Postgres connection string |
| `USE_SQLITE` | No | Set to `false` for Postgres (default) |
| `FEEDBACK_HOOKS_ENABLED` | No | Must be `true` for recording (default) |

## Monitoring

### Prometheus Metrics (If Configured)

```prometheus
# Confidence sync duration
integration_coworker_confidence_sync_duration_seconds

# Templates updated per sync
integration_coworker_confidence_templates_updated_total

# Patterns decayed per run (P4)
integration_coworker_confidence_patterns_decayed_total

# Sync/decay errors
integration_coworker_confidence_sync_errors_total
integration_coworker_confidence_decay_errors_total
```

### Log Queries

```bash
# journald (systemd) - sync
journalctl -u integration-coworker-sync.service --since "1 hour ago"

# journald (systemd) - decay
journalctl -u integration-coworker-decay.service --since "1 week ago"

# CloudWatch (AWS)
aws logs filter-log-events \
  --log-group-name /aws/lambda/confidence-sync \
  --filter-pattern "updated"
```

### Health Check SQL

```sql
-- Recent syncs (should see entries every 15 min)
SELECT 
  DATE_TRUNC('hour', created_at) as hour,
  COUNT(*) as syncs,
  SUM(feedback_count) as total_feedback_processed
FROM kg.confidence_history
WHERE created_at > NOW() - INTERVAL '24 hours'
  AND reason != 'decay'
GROUP BY 1
ORDER BY 1 DESC;

-- Recent decays (P4 - should see weekly entries with reason='decay')
SELECT 
  DATE_TRUNC('day', created_at) as day,
  COUNT(*) as patterns_decayed
FROM kg.confidence_history
WHERE created_at > NOW() - INTERVAL '30 days'
  AND reason = 'decay'
GROUP BY 1
ORDER BY 1 DESC;

-- Templates with stale confidence (no sync in 24h despite feedback)
SELECT n.key, n.confidence_score, 
       MAX(f.created_at) as last_feedback,
       MAX(ch.created_at) as last_sync
FROM kg.nodes n
LEFT JOIN kg.feedback_records f ON n.key = f.template_key
LEFT JOIN kg.confidence_history ch ON n.key = ch.node_key
WHERE n.node_type = 'workflow_template'
GROUP BY n.key, n.confidence_score
HAVING MAX(f.created_at) > MAX(ch.created_at) + INTERVAL '24 hours';

-- Patterns eligible for decay (unused 30+ days, above minimum)
SELECT key, confidence_score, last_used_at
FROM kg.nodes
WHERE node_type = 'pattern'
  AND origin = 'learned'
  AND (last_used_at IS NULL OR last_used_at < NOW() - INTERVAL '30 days')
  AND confidence_score > 0.1
ORDER BY confidence_score DESC
LIMIT 20;
```

## Troubleshooting

### Sync Not Running

1. Check scheduler status:
   ```bash
   systemctl status integration-coworker-sync.timer  # systemd
   crontab -l | grep confidence  # cron
   ```

2. Check last run:
   ```sql
   SELECT MAX(created_at) FROM kg.confidence_history WHERE reason != 'decay';
   ```

3. Manual test:
   ```bash
   integration-coworker kg-maintenance --skip-decay --json
   ```

### Decay Not Running (P4)

1. Check scheduler status:
   ```bash
   systemctl status integration-coworker-decay.timer  # systemd
   crontab -l | grep decay  # cron
   ```

2. Check last decay run:
   ```sql
   SELECT MAX(created_at), COUNT(*) as patterns 
   FROM kg.confidence_history 
   WHERE reason = 'decay';
   ```

3. Preview what would decay:
   ```bash
   integration-coworker kg-decay --dry-run
   ```

4. Manual test:
   ```bash
   integration-coworker kg-decay --json
   ```

### Confidence Not Changing

1. Check feedback exists:
   ```sql
   SELECT COUNT(*) FROM kg.feedback_records;
   ```

2. Check template key matches:
   ```sql
   SELECT DISTINCT template_key FROM kg.feedback_records
   EXCEPT
   SELECT key FROM kg.nodes WHERE node_type = 'workflow_template';
   ```
   (Should return empty if keys match)

3. Verify algorithm:
   ```python
   from integration_coworker.feedback.confidence import compute_confidence_score
   feedbacks = [...]  # From DB
   print(compute_confidence_score(feedbacks))
   ```

### Database Connection Issues

1. Verify `DATABASE_URL` is set
2. Check pg_isready:
   ```bash
   pg_isready -h hostname -p 5432
   ```
3. Check schema exists:
   ```sql
   SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'kg';
   ```

## Algorithm Reference

### Confidence Score Computation

The confidence score is computed as:

```
confidence = weighted_average(feedback_scores)

where:
- weight = source_weight * type_weight * recency_factor
- source_weight: langsmith/cli=1.0, api=0.8, auto=0.4
- type_weight: thumbs/score=1.0, auto_test=0.5, auto_lint=0.2
- recency_factor: 2^(-days_old / 30)

GraphRAG final score includes:
- 10% confidence_score (this value)
- 40% graph_score
- 40% similarity_score
- 10% bonuses
```

### Decay Algorithm (P4)

Patterns not used in 30+ days are decayed:

```
new_confidence = max(min_confidence, old_confidence * decay_factor)

where:
- decay_factor: 0.95 (default, 5% reduction per cycle)
- min_confidence: 0.1 (default floor, prevents zero confidence)
- unused_threshold: 30 days (patterns must have last_used_at < NOW - 30d)

Decay is applied weekly (recommended Sunday 3am) to:
- Prevent stale patterns from dominating
- Allow fresh feedback to take precedence
- Gradually phase out patterns that are no longer relevant
```

**Decay Behavior:**
- A pattern at 1.0 confidence decays to 0.95 → 0.90 → 0.86 → ... → 0.1 (floor)
- Takes ~45 weekly cycles to reach floor from 1.0
- Actively used patterns (last_used_at updated) skip decay

See [FEEDBACK_LEARNING_IMPLEMENTATION.md](../decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md) for full details.

## Rollback Procedure

If a bad sync corrupts confidence scores:

```sql
-- Find the bad sync by reason/timestamp
SELECT * FROM kg.confidence_history 
WHERE created_at > '2026-01-05 10:00:00' 
ORDER BY created_at;

-- Restore from history
UPDATE kg.nodes n
SET confidence_score = ch.old_confidence
FROM kg.confidence_history ch
WHERE n.key = ch.node_key
AND ch.created_at > '2026-01-05 10:00:00';

-- Or reset to default
UPDATE kg.nodes SET confidence_score = 1.0 
WHERE node_type = 'workflow_template';
```

If a bad decay needs rollback:

```sql
-- Find decay records
SELECT * FROM kg.confidence_history 
WHERE reason = 'decay'
AND created_at > '2026-01-05 03:00:00'
ORDER BY created_at;

-- Restore decayed patterns
UPDATE kg.nodes n
SET confidence_score = ch.old_confidence
FROM kg.confidence_history ch
WHERE n.key = ch.node_key
AND ch.reason = 'decay'
AND ch.created_at > '2026-01-05 03:00:00';
```

## Related Documentation

- [FEEDBACK_LEARNING_IMPLEMENTATION.md](../decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md) - Architecture
- [RUNBOOK.md](RUNBOOK.md) - General operations
- [db-postgres.md](db-postgres.md) - Database setup
