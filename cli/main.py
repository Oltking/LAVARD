"""LAVARD command-line interface (Phase 1).

    python -m cli.main submit "Research the top 3 rollups, then write a comparison blog post"
    python -m cli.main show <job_id>
    python -m cli.main submit "..." --json

Submitting a goal returns a verify-first restatement + a task graph with per-node success
criteria — the Phase 1 demo criterion.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from core.foreman import find_candidates, rank_score
from core.schemas import JobView
from core.service import get_job, submit_goal

app = typer.Typer(add_completion=False, help="LAVARD — orchestration ASP for OKX AI.")
console = Console()


@app.command()
def submit(
    goal: str = typer.Argument(..., help="Plain-language goal."),
    owner: str = typer.Option("default-owner", "--owner", help="Owner scope for memory reuse."),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Submit a goal; print the verified intake + task graph."""
    view = submit_goal(goal, owner_id=owner)
    _render(view, as_json)


@app.command()
def show(
    job_id: str = typer.Argument(..., help="Job id."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a previously submitted job."""
    view = get_job(job_id)
    if view is None:
        console.print(f"[red]No job {job_id}[/red]")
        raise typer.Exit(1)
    _render(view, as_json)


@app.command()
def candidates(
    capability: str = typer.Argument(..., help="Sub-task capability, e.g. research/content/security."),
    limit: int = typer.Option(5, "--limit", "-n"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List candidate ASPs for a capability, with onchain reputation, best-first (Phase 2)."""
    cands = find_candidates(capability, limit=limit)
    if as_json:
        console.print_json(json.dumps([c.to_dict() for c in cands]))
        return
    live = _marketplace_is_live()
    console.print(
        Panel.fit(
            f"[bold]{len(cands)}[/bold] candidate ASPs for capability "
            f"[cyan]{capability}[/cyan]  [dim]({'LIVE OKX' if live else 'mock marketplace'})[/dim]",
            title="LAVARD · marketplace",
        )
    )
    table = Table(show_lines=False)
    table.add_column("rank")
    table.add_column("agent")
    table.add_column("mode")
    table.add_column("price", justify="right")
    table.add_column("rep", justify="right")
    table.add_column("jobs", justify="right")
    table.add_column("disp%", justify="right")
    table.add_column("stake OKB", justify="right")
    table.add_column("score", justify="right")
    for i, c in enumerate(cands, 1):
        r = c.reputation
        table.add_row(
            str(i),
            f"{c.name}\n[dim]{c.agent_id}[/dim]",
            c.mode,
            f"${c.price_usd:.2f}",
            f"{r.score:.0f}",
            str(r.jobs_completed),
            f"{r.dispute_rate * 100:.1f}",
            f"{r.stake_okb:.0f}",
            f"{rank_score(c):.1f}",
        )
    console.print(table)


@app.command()
def hire(
    job_id: str = typer.Argument(..., help="Job id to staff (run `submit` first)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Necessity-test each node, vet + hire specialists via A2A escrow, assign in-room IDs (Phase 4)."""
    from core.foreman import hire_for_job

    outcomes = hire_for_job(job_id)
    if as_json:
        console.print_json(json.dumps([o.to_dict() for o in outcomes]))
        return
    icon = {"hired": "[green]✓ hired[/green]", "skipped_not_needed": "[blue]· self[/blue]",
            "skipped_memory": "[green]♻ memory[/green]",
            "escalated_low_trust": "[red]! escalate[/red]", "no_candidates": "[red]✗ none[/red]"}
    console.print(Panel.fit(f"Hiring for job [dim]{job_id}[/dim]", title="LAVARD · Foreman"))
    for o in outcomes:
        head = f"{icon.get(o.decision, o.decision)}  [bold]{o.node_key}[/bold] · {o.capability}"
        console.print(head)
        if o.decision == "hired":
            console.print(
                f"      {o.agent_name} [dim]({o.agent_id})[/dim] · room-id [magenta]{o.in_room_id}"
                f"[/magenta] · ${o.amount_usd:.2f} · trust {o.trust} {o.confidence:.0%} · "
                f"escrow [dim]{o.escrow_id}[/dim]"
            )
        else:
            console.print(f"      [dim]{o.reason}[/dim]")


@app.command()
def signoff(
    job_id: str = typer.Argument(..., help="Job id to sign off (releases escrow)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """User sign-off: release every open escrow for the job (SETTLE, Phase 4)."""
    from core.foreman import sign_off

    result = sign_off(job_id)
    if as_json:
        console.print_json(json.dumps(result.to_dict()))
        return
    console.print(Panel.fit(
        f"Released [bold green]${result.total_released_usd:.2f}[/bold green] across "
        f"[bold]{len(result.released)}[/bold] escrow(s)", title="LAVARD · sign-off"))
    for r in result.released:
        console.print(f"  ✓ {r['in_room_id']} · ${r['amount_usd']:.2f} · {r['status']} "
                      f"[dim]{r['escrow_id']}[/dim]")


@app.command()
def room(
    job_id: str = typer.Argument(..., help="Job id whose hired crew should run the room."),
    demo: bool = typer.Option(True, "--demo/--plain",
                              help="Demo scenario that exercises memory/poll/hire branches."),
    kill_after: int = typer.Option(0, "--kill-after",
                                   help="Trip the kill-switch before turn N (0 = never)."),
    budget: float = typer.Option(0.0, "--budget", help="Override room budget (0 = default)."),
    resume: bool = typer.Option(False, "--resume",
                                help="Resume from the last checkpoint after a crash/freeze."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run the controller-mediated Room + first-responder loop + referee (Phase 5).

    With --resume, continue an interrupted job from its checkpoint: completed nodes are skipped
    and the budget meter carries forward so caps hold across the restart (Phase 10)."""
    from core.room import run_room

    t = run_room(
        job_id, demo=demo,
        freeze_before_turn=(kill_after or None),
        budget_usd=(budget or None),
        resume=resume,
    )
    if t.resumed_from is not None and not as_json:
        rf = t.resumed_from
        console.print(f"[dim]↻ resumed: {len(rf['completed_nodes'])} node(s) done, "
                      f"${rf['spend_usd']:.2f} already spent[/dim]")
    if as_json:
        console.print_json(json.dumps(t.to_dict()))
        return
    status_color = {"completed": "green", "frozen": "red", "budget_exceeded": "red",
                    "turn_limit": "yellow"}.get(t.status, "white")
    console.print(Panel.fit(
        f"status [bold {status_color}]{t.status}[/bold {status_color}]  ·  spend "
        f"[bold]${t.spend_usd:.2f}[/bold]  ·  resolutions {t.resolutions}",
        title="LAVARD · Room"))
    for turn in t.turns:
        who = "[cyan]LAVARD[/cyan]" if turn.actor == "LAVARD" else f"[magenta]{turn.actor}[/magenta]"
        cost = f" [dim]${turn.cost_usd:.2f}[/dim]" if turn.cost_usd else ""
        console.print(f"  [dim]{turn.turn:>2}[/dim] {who}: {turn.action}{cost}")
        if turn.detail:
            console.print(f"       [dim]{turn.detail}[/dim]")
    if t.hired_in_room:
        console.print("\n[bold]Hired into the room:[/bold]")
        for h in t.hired_in_room:
            console.print(f"  + {h['in_room_id']} · {h['capability']} · ${h['amount_usd']:.2f}")


@app.command()
def report(
    job_id: str = typer.Argument(..., help="Job id to report on."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Per-job report: hires, cost, memory reused, savings + the immutable audit log (Phase 8)."""
    from core.governance import build_report

    r = build_report(job_id)
    if as_json:
        console.print_json(json.dumps(r))
        return
    verified = "[green]verified ✓[/green]" if r["audit_verified"] else "[red]TAMPERED ✗[/red]"
    console.print(Panel.fit(
        f"[bold]{r['goal']}[/bold]\n[dim]owner {r['owner_id']} · status {r['status']}[/dim]\n"
        f"hired {len(r['hires'])} · cost ${r['hired_cost_usd']:.2f} · released "
        f"${r['released_usd']:.2f} · memory reused {r['memory_reused_count']} "
        f"(~${r['estimated_savings_usd']:.2f} saved)\naudit log {verified}",
        title="LAVARD · job report"))
    if r["hires"]:
        table = Table(title="Hires")
        for col in ("in_room_id", "agent", "capability", "$", "trust", "status"):
            table.add_column(col)
        for h in r["hires"]:
            table.add_row(h["in_room_id"], h["agent"], h["capability"], f"{h['amount_usd']:.2f}",
                          h["trust"], h["status"])
        console.print(table)
    console.print("\n[bold]Immutable audit log[/bold]")
    for a in r["audit_log"]:
        console.print(f"  [dim]{a['seq']:>2}[/dim] [cyan]{a['kind']}[/cyan] · {a['actor']} · "
                      f"{a['detail']} [dim]{a['hash'][:8]}[/dim]")


@app.command()
def review(
    description: str = typer.Argument(..., help="The proposed action, verbatim."),
    type: str = typer.Option("spend", "--type", help="read|plan|hire|spend|grant_scope|destructive"),
    amount: float = typer.Option(0.0, "--amount"),
    target: str = typer.Option("", "--target"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Action Review: verdict approve/deny/escalate for a proposed action (Phase 8)."""
    from core.governance import Action, review_action

    v = review_action(Action(type, description, amount_usd=amount, target=target))
    if as_json:
        console.print_json(json.dumps(v.to_dict()))
        return
    vcolor = {"approve": "green", "approve-with-edits": "green", "deny": "red",
              "escalate-to-user": "yellow"}[v.verdict]
    console.print(Panel.fit(
        f"verdict [bold {vcolor}]{v.verdict.upper()}[/bold {vcolor}]  ·  tier {v.tier}  ·  "
        f"{'WILL EXECUTE' if v.will_execute else 'NOT EXECUTED'}\n"
        f"[bold]{v.action}[/bold]\nexpected: {v.expected_outcome}\ndownside: {v.downside}\n"
        f"{v.rationale}", title="LAVARD · Action Review"))


@app.command()
def distill(
    job_id: str = typer.Argument(..., help="Job id to distill into Portable Memory."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """On close: redact + store facts and a reusable Playbook into Portable Memory (Phase 7)."""
    from core.memory import distill_job

    summary = distill_job(job_id)
    if as_json:
        console.print_json(json.dumps(summary))
        return
    console.print(Panel.fit(
        f"Stored [bold]{summary['facts_stored']}[/bold] facts + 1 playbook for owner "
        f"[cyan]{summary['owner_id']}[/cyan]\nroles: {', '.join(summary['roles'])}\n"
        f"redactions: {summary['redactions'] or 'none'}", title="LAVARD · distilled to memory"))
    for p in summary["pitfalls"]:
        console.print(f"  ⚠ {p}")


@app.command()
def memory(
    owner: str = typer.Option("default-owner", "--owner"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show an owner's Portable Memory: playbooks + facts (Phase 7)."""
    from core.memory import get_memory

    mem = get_memory()
    pbs = mem.list_playbooks(owner)
    facts = mem.list_facts(owner)
    if as_json:
        console.print_json(json.dumps({"playbooks": [p.to_dict() for p in pbs],
                                       "facts": [f.to_dict() for f in facts]}))
        return
    console.print(Panel.fit(f"owner [cyan]{owner}[/cyan] · {len(pbs)} playbook(s) · "
                            f"{len(facts)} fact(s)", title="LAVARD · Portable Memory"))
    for p in pbs:
        console.print(f"[bold]playbook[/bold] uses={p.uses} · roles {p.roles}\n  [dim]{p.goal_shape}[/dim]")
    for f in facts:
        console.print(f"[bold]fact[/bold] ({f.domain}, conf {f.confidence:.2f}) {f.text[:80]}")


@app.command()
def router(as_json: bool = typer.Option(False, "--json")) -> None:
    """Prove Router economics: a cached reuse + a prevented duplicate paid call (Phase 6)."""
    from core.router.demo import run_router_demo

    log = run_router_demo()
    if as_json:
        console.print_json(json.dumps(log.to_dict()))
        return
    console.print(Panel.fit(
        f"spent [bold]${log.total_spent:.3f}[/bold]  ·  saved "
        f"[bold green]${log.total_saved:.3f}[/bold green]  ·  cache hits {log.count('cache_hit')} "
        f"·  dedup collapses {log.count('dedup_collapse')}", title="LAVARD · Router"))
    table = Table()
    for col in ("kind", "tier", "model", "est$", "alt$", "saved$", "query"):
        table.add_column(col)
    for d in log.decisions:
        kcolor = {"route": "white", "cache_hit": "green", "dedup_collapse": "cyan"}.get(d.kind, "white")
        table.add_row(f"[{kcolor}]{d.kind}[/{kcolor}]", d.tier, d.model, f"{d.est_cost:.3f}",
                      f"{d.alternative_cost:.3f}", f"{d.saved:.3f}", d.query[:40])
    console.print(table)


@app.command()
def kill(job_id: str = typer.Argument(..., help="Job id whose room to freeze instantly.")) -> None:
    """Engage the global kill-switch — freeze the room at the next turn boundary (Phase 5)."""
    from core.store import get_store

    get_store().freeze_room(job_id)
    console.print(f"[red]● KILL-SWITCH ENGAGED[/red] — room for {job_id} is frozen.")


@app.command()
def vet(
    agent_id: str = typer.Argument(..., help="Agent id / identity to vet."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Vet an agent: trust band + confidence + evidence chain + honest limits (Phase 3)."""
    from core.vetter import vet_agent

    verdict = vet_agent(agent_id)
    if as_json:
        console.print_json(json.dumps(verdict.to_dict()))
        return
    color = {"high": "green", "medium": "yellow", "low": "red"}[verdict.trust]
    console.print(
        Panel.fit(
            f"trust [bold {color}]{verdict.trust.upper()}[/bold {color}]  ·  "
            f"confidence [bold]{verdict.confidence:.0%}[/bold]  ·  "
            f"score [bold]{verdict.score:.0f}/100[/bold]\n"
            f"[dim]{verdict.agent_id}[/dim]\n{verdict.recommendation}",
            title="LAVARD · Vetter verdict",
        )
    )
    console.print("[bold]Evidence chain[/bold]")
    for e in verdict.evidence:
        sign = f"[green]+{e.effect:.0f}[/green]" if e.effect >= 0 else f"[red]{e.effect:.0f}[/red]"
        console.print(f"  {sign:>18}  [cyan]{e.signal}[/cyan] — {e.detail}")
    console.print("\n[bold yellow]Honest limits[/bold yellow]")
    for lim in verdict.limits:
        console.print(f"  ! {lim}")


@app.command()
def golive(as_json: bool = typer.Option(False, "--json")) -> None:
    """Package LAVARD's OKX.AI listing, run the internal readiness review, publish (Phase 9)."""
    from mcp import build_listing, is_live, list_tools, publish, readiness_review

    listing = build_listing()
    review = readiness_review(listing)
    result = publish()
    if as_json:
        console.print_json(json.dumps(
            {"listing": listing, "review": review, "publish": result}))
        return

    console.print(Panel.fit(
        f"[bold]{listing['name']}[/bold] · {listing['role']} · mode [cyan]{listing['mode']}[/cyan]\n"
        f"{listing['summary']}\n"
        f"MCP tools: [bold]{', '.join(listing['mcp_tools'])}[/bold]\n"
        f"budget ceiling ${listing['pricing']['job_budget_ceiling_usd']:.0f} · "
        f"dispute stake {listing['dispute']['stake_okb']:.0f} OKB",
        title="LAVARD · OKX.AI listing manifest"))

    table = Table(title="Internal readiness review")
    table.add_column("gate"); table.add_column("check"); table.add_column("", justify="center")
    for c in review["checks"]:
        mark = "[green]✓[/green]" if c["passed"] else "[red]✗[/red]"
        table.add_row(c["id"], c["label"], mark)
    console.print(table)

    vcolor = "green" if review["ready"] else "red"
    console.print(f"verdict [bold {vcolor}]{review['verdict']}[/bold {vcolor}]")
    if result["published"]:
        console.print("[green]● PUBLISHED[/green] — LAVARD is live on OKX.AI.")
    else:
        mode = result.get("mode", "blocked")
        console.print(f"[yellow]○ NOT PUBLISHED[/yellow] ({mode}) — {result['reason']}")
    console.print(f"[dim]backend: {'LIVE OKX' if is_live() else 'mock/offline'} · "
                  f"{len(list_tools())} MCP tools exposed[/dim]")


def _marketplace_is_live() -> bool:
    from onchain.marketplace import MockMarketplace
    from onchain import get_marketplace

    return not isinstance(get_marketplace(), MockMarketplace)


def _render(view: JobView, as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(view.to_dict()))
        return

    console.print(
        Panel.fit(
            f"[bold]{view.restated_goal}[/bold]\n"
            f"[dim]job {view.id} · status {view.status} · planner {view.planner}[/dim]",
            title="LAVARD · verified goal",
        )
    )
    if view.reused_playbook:
        console.print(f"[green]♻ Reusing a known-good Playbook[/green] [dim]for a goal like: "
                      f"{view.reused_playbook}[/dim]")

    if view.assumptions:
        console.print("[bold cyan]Assumptions[/bold cyan]")
        for a in view.assumptions:
            console.print(f"  • {a}")
    if view.success_criteria:
        console.print("\n[bold green]Success criteria[/bold green]")
        for c in view.success_criteria:
            console.print(f"  ✓ {c}")
    if view.open_questions:
        console.print("\n[bold yellow]Open questions[/bold yellow]")
        for q in view.open_questions:
            console.print(f"  ? {q}")

    console.print("\n[bold]Task graph[/bold]")
    tree = Tree(f"[bold]{view.goal}[/bold]")
    by_key = {n.key: n for n in view.nodes}
    for n in view.nodes:
        deps = f" [dim](after {', '.join(n.depends_on)})[/dim]" if n.depends_on else ""
        hire = "[magenta]hire[/magenta]" if n.needs_hire else "[blue]self[/blue]"
        label = f"[bold]{n.key}[/bold] {n.title} · {n.capability} · {hire}{deps}"
        branch = tree.add(label)
        for c in n.success_criteria:
            branch.add(f"[green]✓[/green] {c}")
    console.print(tree)
    _ = by_key  # reserved for future dependency rendering


if __name__ == "__main__":
    app()
