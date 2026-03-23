# Benchmark Report: phase0-core

- **Started**: 2026-03-23T02:50:48.554158+00:00
- **Finished**: 2026-03-23T02:50:57.112372+00:00
- **Total cases**: 19
- **Passed**: 19
- **Failed**: 0
- **Errors**: 0
- **Skipped**: 0

**Pass rate**: 100.0%

## Aggregate Metrics

| Metric | Value |
|--------|-------|
| Total duration | 8484.0 ms |
| Avg duration | 446.5 ms |
| Total tasks | 24 |
| Total routes | 23 |
| Total clarifications | 4 |
| Total interventions | 2 |

## Case Results

| Case ID | Status | Duration | Tasks | Routes | Clarifications | Interventions |
|---------|--------|----------|-------|--------|----------------|---------------|
| contacts.ambiguity.same_name | ✅ passed | 3609.0ms | 1 | 1 | 1 | 0 |
| contacts.happy_path.by_name | ✅ passed | 172.0ms | 1 | 1 | 0 | 0 |
| contacts.not_found.unknown_person | ✅ passed | 156.0ms | 1 | 1 | 0 | 0 |
| contacts.happy_path.query_openid | ✅ passed | 141.0ms | 1 | 1 | 0 | 0 |
| contacts.read_only.no_intervention | ✅ passed | 141.0ms | 1 | 1 | 0 | 0 |
| hr.happy_path.attendance | ✅ passed | 140.0ms | 1 | 1 | 0 | 0 |
| hr.clarification.identity | ✅ passed | 172.0ms | 1 | 1 | 1 | 0 |
| hr.happy_path.leave_balance | ✅ passed | 156.0ms | 1 | 1 | 0 | 0 |
| hr.unsupported.permission_denied | ✅ passed | 188.0ms | 1 | 1 | 0 | 0 |
| meeting.clarification.missing_time | ✅ passed | 328.0ms | 1 | 1 | 1 | 0 |
| meeting.conflict.room | ✅ passed | 437.0ms | 1 | 1 | 0 | 0 |
| meeting.dependency.contacts | ✅ passed | 235.0ms | 2 | 2 | 0 | 0 |
| meeting.governance.cancel | ✅ passed | 281.0ms | 1 | 1 | 0 | 1 |
| meeting.governance.cancel_rejected | ✅ passed | 313.0ms | 1 | 1 | 0 | 1 |
| meeting.happy_path.basic | ✅ passed | 375.0ms | 1 | 1 | 0 | 0 |
| workflow.clarification.resume | ✅ passed | 312.0ms | 2 | 1 | 1 | 0 |
| workflow.contacts.to.hr.basic | ✅ passed | 406.0ms | 2 | 2 | 0 | 0 |
| workflow.contacts.to.meeting.basic | ✅ passed | 516.0ms | 2 | 2 | 0 | 0 |
| workflow.dependency.helper_resume | ✅ passed | 406.0ms | 2 | 2 | 0 | 0 |
