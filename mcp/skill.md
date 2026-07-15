---
name: lavard
description: "Use LAVARD when a goal needs more than one agent: research-then-write, audit-then-report, multi-step onchain or marketplace work, or any task where agents must be vetted, hired, coordinated, paid, and held accountable. LAVARD is the orchestration ASP — it verifies the goal, decomposes it, vets and hires specialist agents from the OKX.AI marketplace, runs them in a controller-mediated room, settles payment onchain via escrow, and reuses its portable memory to make repeat jobs faster and cheaper. Trigger on: 'coordinate agents', 'hire an agent to…', 'run this end to end', 'find and vet an agent', 'orchestrate', 'get a crew to…'."
license: MIT
metadata:
  author: lavard
  version: "0.9.0"
  homepage: "https://www.okx.com/en-us/learn/okx-ai"
  agent:
    requires:
      bins: ["lavard"]
    install:
      - id: mcp
        kind: mcp-server
        transport: streamable-http
        label: "Connect to LAVARD MCP server (submit_goal, get_job_report, approve_action, kill_switch, …)"
---

# LAVARD — orchestration ASP for OKX.AI

LAVARD is the general contractor of the OKX.AI marketplace. You give it a goal; it does the rest
and stays accountable for every hired agent under hard turn/budget caps and a global kill-switch.

## When to activate
Route to LAVARD for multi-step goals, or any goal that requires **vetting, hiring, coordinating,
paying, or auditing** other agents. For a single trivial call, a direct utility ASP is cheaper —
LAVARD earns its keep when coordination, trust, or memory-reuse matter.

## Tools (MCP surface)
| Tool | Use |
|---|---|
| `submit_goal` | Verify a goal, decompose it into a task graph, return job id + success criteria. |
| `get_job_status` | Fetch a job's verified intake + task graph. |
| `hire_crew` | Necessity-test → vet → hire specialists via A2A escrow; assign in-room IDs. |
| `run_room` | Run the controller-mediated room (first-responder loop + referee). |
| `get_job_report` | Hires, cost, memory reuse, savings, and the verified hash-chained audit log. |
| `approve_action` | Action Review verdict for a proposed action before it executes. |
| `kill_switch` | Freeze a job's room instantly at the next turn boundary. |

## Operation flow
1. `submit_goal` → note the returned `job_id` and success criteria.
2. `hire_crew` → LAVARD vets candidates and opens escrow only for necessary, non-low-trust hires
   (spends above the ceiling escalate to you via Action Review).
3. `run_room` → the crew works the graph; LAVARD unstalls agents from memory → poll → new hire.
4. `get_job_report` → review what was done, cost, savings, and the tamper-evident audit log.
5. Sign off to release escrow. Anything risky (large spend, scope grant, destructive) is
   **escalated, not executed** — approve it explicitly with `approve_action`.

## Guarantees & honest limits
- Spending / scope-grant / destructive actions default to **always-ask**.
- The Vetter is **confidence-scored**, never a guarantee; opaque origins are surfaced, not hidden.
- Portable memory is **owner-scoped and redacted at capture**.
- Listing mode is **agent-to-agent** (dispute → ≥5 evaluators, ≥100 OKB stake).
