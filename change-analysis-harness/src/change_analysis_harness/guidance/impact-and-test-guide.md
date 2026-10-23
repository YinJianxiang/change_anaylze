# Impact and test guide

## Risk level

Use the highest applicable level.

| Level | Indicators |
|---|---|
| High | Authentication/authorization, money, destructive operations, sensitive data, schema migration, public API breaking change, concurrency/transaction behavior, broad shared component, deployment/configuration dependency |
| Medium | Core workflow behavior, API contract extension, state management, validation, caching, retry behavior, shared service with bounded consumers |
| Low | Isolated presentation/copy, internal refactor with stable contract and adequate tests, additive low-use behavior |

Raise risk when coverage is absent, the blast radius is unclear, frontend/backend contracts appear unsynchronized, rollback is difficult, or historical data is involved. Lowering risk requires evidence, not assumption.

## Change-to-test heuristics

| Change signal | Inspect | Minimum test dimensions |
|---|---|---|
| Route/view/component | navigation, guards, state, API calls, responsive/error states | component/UI, E2E core path, related-page regression |
| Form/validation | frontend/backend rule parity, boundary values, normalization | valid, invalid, empty, boundary, duplicate submission |
| API client/controller | method/path, request/response schema, status codes | contract, auth, validation, compatibility, error mapping |
| Service/business rule | callers, branches, state transitions, transactions | unit branches, integration, invalid transition, idempotency |
| Model/schema/migration | nullability, defaults, indexes, constraints, old data | forward migration, old/new records, rollback, DB compatibility |
| Auth/permission | identities, roles, object ownership, tenant boundary | anonymous, insufficient role, allowed role, cross-tenant access |
| Async/queue/cache | retry, ordering, duplication, invalidation, recovery | retry, duplicate delivery, stale data, partial failure |
| External integration | timeout, malformed response, quota, fallback | success, timeout, 4xx/5xx, malformed data, retry/fallback |
| Config/feature flag | defaults, environments, rollout and rollback | missing/invalid config, off/on state, deployment order |
| Shared utility | all callers and data shapes | focused unit tests plus representative consumer regression |
| Shared component | pages, routes, props/events, state and API consumers | component contract, representative pages, accessibility, visual/state regression |
| Payment/order state | amount precision, authorization, idempotency, transitions, events | duplicate/retry, invalid transition, reconciliation, rollback, audit evidence |

## Coverage rules

- Trace deleted behavior as carefully as added behavior; removed routes, fields, fallbacks, and tests may create regressions.
- Check both sides of a frontend/backend contract even when only one side changed.
- Include pre-existing records and mixed-version deployment when contracts or schemas change.
- Treat changed tests as evidence, not proof of completeness.
- Separate executable scenarios from general quality recommendations.
- Do not recommend every test category mechanically; justify each included category from evidence.
- Raise shared code risk when repository search finds consumers across modules. Record direct callers, inferred business scenarios, related tests, and confidence.
- Treat regex/text hits as reference evidence, not proof of a runtime call. Mark dynamic dispatch and generated code as limits.
- When database evidence is needed, use the optional `change_analysis_harness.database_cli` with a least-privilege read-only account, parameterized single statements, bounded rows and timeouts. Never change data or invoke unknown stored functions. Record environment, query purpose, time, sanitized parameters and truncation; prefer metadata or anonymized aggregates. Database samples do not prove full requirement implementation or correspond automatically to the fixed code revision. Keep credentials and sensitive rows out of reports; failed connections are evidence gaps, not permission to bypass read-only guards.

## Requirement completeness (Mode B)

- Review every requirement, not just acceptance points visible in the diff. Read available body text, tables, linked documents, and business rules in images/prototypes; disclose unread or unavailable content.
- Decompose the requirement into individually verifiable acceptance points with stable IDs and source locations. Include functions, fields, rules, permissions, states, error paths, compatibility, and data/configuration requirements where specified.
- Trace each point to the fixed target revision, using targeted reads outside changed files when necessary. Check frontend/backend, existing implementation, configuration, migrations and tests; distinguish newly implemented, pre-existing and removed behavior. This is requirement-directed verification, not a whole-repository scan.
- Assign each point exactly one status: 已实现 / 部分实现 / 未实现 / 实现与需求不一致 / 无法确认 / 不适用. Record source evidence and test evidence or executable suggestions. No diff or zero search hits alone does not prove missing implementation; describe the expected implementation location and actual gap, otherwise mark 无法确认. 不适用 requires explicit scope evidence.
- For partial, missing or inconsistent implementation, describe expected versus actual behavior, missing branches, business impact and linked must-test scenarios. Missing repositories, document sections, prototypes or business definitions are evidence gaps, not automatically code defects.
- Summarize counts and answer whether all requirements are implemented. Unverified points or unread content prevent a completeness claim. Source implementation is distinct from runtime verification; never report unexecuted tests as passed. Also check changed behavior without a requirement basis for possible scope expansion.

## Scenario quality

Write each scenario as:

`[priority][type] Preconditions/data → action → expected observable result — evidence`

Good expected results name status codes, UI states, persisted values, emitted events, access decisions, or rollback behavior. Avoid vague phrases such as “normal”, “correct”, or “no issue”.
