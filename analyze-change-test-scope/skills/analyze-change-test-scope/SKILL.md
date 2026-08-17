---
name: analyze-change-test-scope
description: Prepare safe GitHub PR workspaces, scan all supported source files in a complete repository, build a lightweight caller-to-callee graph, analyze frontend and backend Git changes with symbol and API references, infer direct and transitive business impact, and produce a prioritized, evidence-backed test scope. Use when Codex needs to review a working tree, staged changes, commit, branch, revision range, PR URL, PR diff, or patch and answer what changed, what may break, which callers and consumers are affected, and what should be tested. Supports change-only and requirement-alignment analysis across API contracts, databases, permissions, integrations, configuration, scheduled jobs, queues, regression scope, and uncertainty tracking.
---

# Analyze Change Test Scope

Turn code changes into a reviewable test plan. Separate observed facts from inferred impact and never claim that a diff fully represents the product requirement.

## Enterprise agent tools

When the `change-analysis` MCP tools are available, use them instead of assuming
direct filesystem or shell access:

1. For a mail-triggered task, call `get_mail_analysis_task` with the supplied
   `task_id` and `agent_access_token` before drawing conclusions.
2. Call `prepare_change_workspace` with each configured project and target branch.
3. Call `collect_change_context` and `collect_repository_impact` with the returned
   `analysis_id`.
4. Validate important heuristic edges with `search_repository` and
   `read_source_evidence` before presenting them as confirmed calls.
5. Call `release_change_workspace` after the report is complete.
6. For a mail-triggered task, call `complete_mail_analysis_task` with the final
   structured report so the result is auditable outside the conversation.

Never invent project names, repository paths, branches, source content, or MCP
results. Ask for the project or branch only when it cannot be determined from the
conversation. MCP evidence warnings and incomplete scans must remain visible in
the final report.

## Select the mode

- Use **Mode A — change-only** when no requirement is supplied. Infer the likely intent, label it as inference, and focus on impact and tests.
- Use **Mode B — requirement alignment** when a requirement, acceptance criteria, ticket, or design is supplied. Trace every requirement to code and tests, and identify missing, conflicting, or extra implementation.
- If the user does not name a Git range, analyze tracked staged and unstaged changes plus relevant untracked files. State that scope explicitly.
- If repository context cannot answer a material question, continue with the analysis and list the question under `待确认`; do not block unnecessarily.

## Collect evidence

1. Read repository instructions such as `AGENTS.md` and identify the frontend, backend, tests, migrations, and configuration layout.
2. Determine the requested source: working tree, staged changes, one commit, a range, branch comparison, PR patch, or supplied diff.
3. Prepare repository context before drawing impact conclusions. A PR patch alone is insufficient to identify all callers, consumers, tests, and indirect business flows.
4. Run `scripts/collect_change_context.py` for local Git sources. Use `--help` for options. It is read-only and emits structured JSON with revision and changed-file metadata.
5. Run `scripts/collect_repository_impact.py --repo <workspace> --range <merge-base>...<head>` when a complete repository is available. Require it to scan all supported source files, inspect `repository_scan.scan_complete`, and use `lightweight_call_graph.edges` and `impact_paths` to follow direct and transitive callers. Treat heuristic edges as leads that require inspection, not a complete semantic call graph.
6. Inspect relevant changed files and evidence-backed callers, callees, routes, API clients, schemas, tests, migrations, configuration, jobs, and message consumers.
7. For untracked files, inspect only files relevant to the change. Do not read secrets, generated output, dependency directories, or large binaries.
8. When the diff or search result is large, prioritize high-risk boundaries and disclose truncation and files not inspected in depth.

Do not modify code or run the test suite unless the user also asks. Safe read-only inspection is part of this workflow.

## Prepare repository context

When the user supplies a GitHub PR URL:

1. Run `scripts/prepare_pr_workspace.py --pr-url <url>`, optionally with `--repo <candidate>` and `--cache-dir <path>`.
2. Prefer `gh` PR metadata. Allow the script's public GitHub API fallback when `gh` is absent or unauthenticated.
3. Use an existing matching local repository only as the fetch source. Never run `git pull`, `git reset`, `git checkout`, or force-switch its branch.
4. Let the script perform only safe `git fetch` operations and create a detached temporary worktree. When no matching repository exists, use its independent cached clone.
5. Pass the returned workspace and range to both evidence scripts. Search the complete repository for callers, callees, routes, API consumers, schemas, tests, migrations, configuration, scheduled jobs, and message consumers.
6. Verify and report the returned base SHA, head SHA, merge base, PR metadata source, and repository source.

If preparation fails, continue with available patch/diff evidence and label the report exactly `Diff-only analysis`. State: `未获取完整仓库，无法确认所有间接调用方和完整回归范围。` Never imply that indirect impact is complete. When preparation and repository search succeed, label it `Full repository context analysis`.

## Analyze the impact graph

Build the smallest useful, evidence-backed chain from user entry point to persistent or external effects:

`page/entry → frontend component/state → API client → controller → service → shared method → model/database → external system`

Start from changed nodes in `lightweight_call_graph`. Trace reverse `caller → callee` paths up to user or system entry points, then validate important edges against source evidence. Report candidate/scanned source counts and never claim full repository coverage when `repository_scan.scan_complete` is false.

Check each applicable boundary:

- Frontend routes, views, components, validation, state, feature flags, error/loading/empty states, accessibility, and browser behavior.
- Request method/path, parameters, headers, serialization, response shape, status codes, and backward compatibility.
- Backend routing, authentication, authorization, validation, business rules, transactions, idempotency, concurrency, caching, queues, and scheduled jobs.
- Models, migrations, constraints, defaults, historical data, rollback, and database compatibility.
- External services, timeouts, retries, partial failure, observability, configuration, and deployment order.
- Existing tests that changed, should change, or may now encode stale behavior.

Raise risk for shared methods/components with multiple consumers, authentication, money, payments, order-state transitions, schema migrations, and configuration changes. For each broad shared change, report the direct change, direct callers, indirect business scenarios, recommended regression modules, related tests, evidence, and confidence. Distinguish searched references from confirmed runtime calls.

Use [references/impact-and-test-guide.md](references/impact-and-test-guide.md) for risk rules and test dimensions.

## Mode A — change-only

1. Summarize observable behavior changes with file and symbol evidence.
2. Infer the likely business intent and mark confidence as high, medium, or low.
3. Identify direct impact, transitive regression areas, and unchanged boundaries that provide evidence of compatibility.
4. Generate tests from changed behavior, failure paths, boundary values, state transitions, and integration contracts.
5. Put ambiguous product behavior in `待确认`; do not silently invent acceptance criteria.

## Mode B — requirement alignment

1. Extract numbered requirements and acceptance criteria before analyzing the diff.
2. Create a trace for each item: `requirement → implementation evidence → test evidence → status`.
3. Use only these statuses: `已覆盖`, `部分覆盖`, `未发现实现`, `实现与需求冲突`, `需确认`.
4. List code behavior with no matching requirement as `疑似超范围实现`; distinguish necessary technical support from user-visible scope expansion.
5. Test both requirement behavior and implementation-derived risks. Missing implementation still needs a proposed acceptance test.

## Prioritize tests

Assign every test item:

- Priority: `P0` blocks release or protects critical data/security; `P1` covers core changed behavior; `P2` covers secondary regression or lower-probability edges.
- Type: unit, API/contract, integration, UI/component, E2E, migration/data, security, compatibility, performance/reliability, or observability.
- Evidence: changed file/symbol, requirement ID, or impact path.
- Expected result: specific observable outcome, not “works correctly”.

Prefer precise test scenarios over generic advice. Include negative paths and state cleanup. Recommend existing test files/commands when discovered, but do not invent commands.

## Produce the report

Follow [references/report-template.md](references/report-template.md). Keep sections concise, omit inapplicable dimensions, and include:

1. Analysis scope and mode
2. Change and likely requirement summary
3. Evidence-backed impact map
4. Risk assessment
5. Prioritized test scenarios
6. Regression scope
7. Requirement traceability for Mode B
8. Exclusions, uncertainty, and questions

Never present inferred intent as fact. Cite repository-relative paths and line numbers when available.
