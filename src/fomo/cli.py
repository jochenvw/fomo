"""`fomo` command line. Also works without Copilot.

Write semantics (every write command):
  default / --plan / --dry-run  show the itemized plan, change nothing
  --check                       show current state only
  --apply                       verify az context, show plan, ask (or --yes), apply, re-check
Exit codes: 0 ok, 1 error, 2 blocked or not approved.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import ENV_FILE, WORKSPACE, Environment
from .errors import ApprovalDeclined, Blocked, WorkshopError
from .naming import ParticipantNames
from .plan import Plan, confirm, mode_from_flags, verify_context
from .telemetry.art import banner
from .telemetry.logging import RunLogger

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED = 0, 1, 2


# ---- argument parsing ------------------------------------------------------------------------
def _common(p: argparse.ArgumentParser, *, writes: bool = False) -> None:
    g = p.add_argument_group("common")
    g.add_argument("--participant", help="hackername (default: saved in .workshop/participant.json)")
    g.add_argument("--subscription", help="override subscription id")
    g.add_argument("--resource-group", help="override resource group")
    g.add_argument("--project", help="override Foundry project name")
    g.add_argument("--config", type=Path, help="YAML config (see examples/config.example.yaml)")
    g.add_argument("--output", choices=("text", "json"), default="text")
    g.add_argument("--verbose", "-v", action="store_true")
    if writes:
        m = p.add_argument_group("mode (default: --plan)")
        m.add_argument("--check", action="store_true", help="show current state only")
        m.add_argument("--plan", action="store_true", help="show the itemized plan; change nothing")
        m.add_argument("--dry-run", action="store_true", help="alias of --plan")
        m.add_argument("--apply", action="store_true", help="apply after confirmation")
        m.add_argument("--yes", "-y", action="store_true", help="approve the displayed plan non-interactively")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fomo", description="FOMO metadata-agent workshop: preflight, "
                                 "provision, cheatcode and friends.")
    ap.add_argument("--version", action="version", version=f"fomo {__version__}")
    sub = ap.add_subparsers(dest="command", required=True, metavar="command")

    p = sub.add_parser("preflight", help="readiness report (read-only unless --fix --apply)")
    _common(p)
    p.add_argument("--participants", type=int, default=40, help="planned participant count (capacity checks)")
    p.add_argument("--smoke", action="store_true", help="opt-in: 3 tiny paid model calls (chat, embedding, decision)")
    p.add_argument("--local-only", action="store_true", help="only local tooling checks (no Azure)")
    p.add_argument("--init-participant", action="store_true",
                   help="reuse your saved hackername or check a random one against Search/Blob and save it locally")
    p.add_argument("--fix", action="store_true", help="plan remediation for fixable findings")
    p.add_argument("--plan", action="store_true", help="with --fix: show the plan (default)")
    p.add_argument("--apply", action="store_true", help="with --fix: apply after confirmation, then re-check")
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--report-dir", type=Path, default=WORKSPACE / "preflight")

    p = sub.add_parser("provision", help="participant baseline: container, docs, raw index, Simple RAG, starter")
    _common(p, writes=True)
    p.add_argument("--no-smoke", action="store_true", help="skip the Simple RAG smoke question")
    p.add_argument("--step", choices=("all", "upload", "index", "agent"), default="all",
                   help="guided mode: upload documents, build the raw index, or create Simple RAG (default: all)")

    p = sub.add_parser("next", help="where am I in the workshop and what is the next step (read-only)")
    _common(p)

    p = sub.add_parser("miner", help="hosted Metadata Miner: deploy / run")
    msub = p.add_subparsers(dest="miner_command", required=True, metavar="action")
    q = msub.add_parser("deploy", help="deploy the hosted agent (source ZIP) and grant its identity")
    _common(q, writes=True)
    q.add_argument("--classifier", type=Path, help="your classifier.yaml (default: reference)")
    q.add_argument("--force", action="store_true", help="new version even if the code is unchanged")
    q = msub.add_parser("run", help="classify blobs and write metadata")
    _common(q, writes=True)
    where = q.add_mutually_exclusive_group()
    where.add_argument("--hosted", action="store_true", default=True, help="invoke the Foundry hosted agent (default)")
    where.add_argument("--local", action="store_true", help="LOCAL developer mode (not the hosted requirement)")
    q.add_argument("--mode", choices=("delta", "all"), default="delta")
    q.add_argument("--only", nargs="+", metavar="DOC", help="doc ids or blob names")
    q.add_argument("--limit", type=int)
    q.add_argument("--classifier", type=Path, help="local mode only: classifier.yaml")
    q.add_argument("--no-write", action="store_true", help="classify but do not write metadata")

    p = sub.add_parser("smart", help="enable + run the smart indexer, create Smart RAG")
    _common(p, writes=True)
    p.add_argument("--reset", action="store_true", help="reset the smart indexer first (full re-index)")
    p.add_argument("--no-smoke", action="store_true")
    p.add_argument("--classifier", type=Path,
                   help="classifier.yaml whose labels/definitions Smart RAG routes on (default: reference)")

    p = sub.add_parser("eval", help="side-by-side evaluation (read-only; agent calls cost a little)")
    _common(p)
    p.add_argument("--agents", choices=("none", "canonical", "all"), default="canonical")
    p.add_argument("--k", type=int)
    p.add_argument("--queries", nargs="+", metavar="QID")

    p = sub.add_parser("eval-foundry", help="paired cloud evaluations of Simple/Smart RAG using the Foundry SDK")
    _common(p, writes=True)
    p.add_argument("--suite", choices=("smoke", "full"), default="smoke")
    p.add_argument("--judge", help="existing chat deployment for all judges (default: workshop chat model)")
    p.add_argument("--workspace", type=Path, default=Path(".foundry"))
    p.add_argument("--resume", type=Path, help="resume/download a previously submitted evaluation manifest")

    p = sub.add_parser("eval-ladder",
                       help="five-variant RAG comparison: Decision 1.1 routing, repeats, objective metrics, CIs")
    _common(p, writes=True)
    p.add_argument("--split", choices=("dev", "test", "all"), default="dev",
                   help="dev = pipeline check; test = pre-registered headline result")
    p.add_argument("--repeats", type=int, help="answers per question and variant (default: 1 dev, 3 test/all)")
    p.add_argument("--judge", default="gpt-5.4", help="judge deployment; must run a different model than the agents")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--workspace", type=Path, default=Path(".foundry"))
    p.add_argument("--resume", type=Path, help="continue an existing ladder manifest.json (plans remaining work)")
    p.add_argument("--report-only", action="store_true", help="with --resume: rebuild the report, no Azure calls")

    p = sub.add_parser("cheatcode", help="complete the demo end to end (same code paths, same approvals)")
    _common(p, writes=True)
    p.add_argument("--classifier", type=Path, help="classifier.yaml (default: reference)")
    p.add_argument("--force-deploy", action="store_true")

    p = sub.add_parser("status", help="what exists for this participant (read-only)")
    _common(p)
    p.add_argument("--no-agents", action="store_true")

    p = sub.add_parser("reset-participant", help="undo classification and/or rebuild indexes (asks DELETE)")
    _common(p, writes=True)
    p.add_argument("--scope", choices=("metadata", "indexes", "all"), required=True)

    p = sub.add_parser("cleanup", help="delete this participant's resources (asks DELETE)")
    _common(p, writes=True)
    p.add_argument("--local", action="store_true", help="also delete .workshop/runs/<hackername>/")

    p = sub.add_parser("capacity", help="Search object/cost math for N participants (offline)")
    p.add_argument("--participants", type=int, default=40)
    p.add_argument("--sku", default="basic")
    p.add_argument("--replicas", type=int, default=1)
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--output", choices=("text", "json"), default="text")

    p = sub.add_parser("env", help="workshop environment file")
    esub = p.add_subparsers(dest="env_command", required=True, metavar="action")
    q = esub.add_parser("pull", help="write .workshop/environment.json from a Bicep deployment's outputs")
    _common(q)
    q.add_argument("--deployment", help="deployment name (default: latest with workshop outputs)")
    q = esub.add_parser("show", help="print the resolved environment")
    _common(q)
    q = esub.add_parser("select", help="remember a confirmed target, optionally create an empty resource group")
    _common(q, writes=True)
    q.add_argument("--tenant", required=True, help="confirmed directory (tenant) id")
    q.add_argument("--create-resource-group", action="store_true", help="create only the named empty resource group")
    q.add_argument("--location", help="required when creating a resource group")
    q = esub.add_parser("discover", help="adopt Foundry/Storage/Search already in the confirmed resource group")
    _common(q, writes=True)
    q.add_argument("--foundry-account", help="choose when the group has several Foundry accounts")
    q.add_argument("--storage-account", help="choose when the group has several storage accounts")
    q.add_argument("--search-service", help="choose when the group has several Search services")

    p = sub.add_parser("agents", help="talk to your prompt agents")
    asub = p.add_subparsers(dest="agents_command", required=True, metavar="action")
    q = asub.add_parser("ask", help="ask Simple RAG or Smart RAG a question")
    _common(q)
    q.add_argument("which", choices=("simple", "smart"))
    q.add_argument("question", nargs="?", help="default: the canonical contracts question")

    p = sub.add_parser("infra", help="facilitator: shared infrastructure via Bicep")
    isub = p.add_subparsers(dest="infra_command", required=True, metavar="action")
    for name, text in (("what-if", "az deployment group what-if (read-only)"),
                       ("deploy", "what-if, ask, deploy, then env pull")):
        q = isub.add_parser(name, help=text)
        _common(q, writes=name == "deploy")
        q.add_argument("--parameters", type=Path, required=True, help="infra/parameters/*.bicepparam")
        q.add_argument("--deployment", default="fomo")
        q.add_argument("--location", default="swedencentral", help="for creating the RG if missing")

    p = sub.add_parser("facilitator", help="facilitator views")
    fsub = p.add_subparsers(dest="facilitator_command", required=True, metavar="action")
    q = fsub.add_parser("dashboard", help="all participants: stage, capacity, readiness (read-only)")
    _common(q)
    q.add_argument("--readiness-dir", type=Path, help="folder of collected readiness JSON files")
    q.add_argument("--no-agents", action="store_true", help="faster: skip agent lookups")
    return ap


# ---- shared plumbing -------------------------------------------------------------------------
_OPEN_LOGS: list[RunLogger] = []


class Ctx:
    def __init__(self, args: argparse.Namespace, operation: str) -> None:
        self.args = args
        self.json = getattr(args, "output", "text") == "json"
        overrides = {k: v for k, v in (("subscription_id", getattr(args, "subscription", None)),
                                       ("resource_group", getattr(args, "resource_group", None)),
                                       ("project_name", getattr(args, "project", None))) if v}
        self.env = Environment.load(config_file=getattr(args, "config", None), overrides=overrides)
        self.operation = operation
        self.log = RunLogger(operation, verbose=getattr(args, "verbose", False), quiet=self.json)
        _OPEN_LOGS.append(self.log)

    def require_env(self) -> None:
        missing = self.env.missing()
        if missing:
            raise WorkshopError(f"workshop environment incomplete: {', '.join(missing)}",
                                fix="run `uv run fomo preflight`: it inspects the confirmed resource group and "
                                "names the next step (`env discover`, `env pull` or a request to IT)")

    def names(self, *, allow_new: bool = False) -> ParticipantNames:
        from .provision.participant import resolve_participant

        names = resolve_participant(self.env, getattr(self.args, "participant", None), allow_new=allow_new)
        self.log.participant = names.hackername
        self.log.attach_file(Path(names.run_dir) / "logs")
        return names

    def mode(self) -> str:
        a = self.args
        return mode_from_flags(getattr(a, "check", False), getattr(a, "plan", False), getattr(a, "apply", False),
                               getattr(a, "dry_run", False))

    def out(self, text: str = "", data: Any = None) -> None:
        if self.json:
            if data is not None:
                print(json.dumps(data, indent=2, default=str))
        elif text:
            print(text)


def write_flow(ctx: Ctx, plan: Plan, apply_fn: Callable[[], Any], *, after: Callable[[Any], str] | None = None,
               check: Callable[[], Any] | None = None) -> int:
    mode = ctx.mode()
    if mode == "check":
        data = check() if check else plan.to_dict()
        ctx.out(json.dumps(data, indent=2, default=str) if not isinstance(data, str) else data, data)
        return EXIT_OK
    if mode == "plan" or not plan.writes:
        if ctx.json:
            ctx.out(data={"mode": "plan", "plan": plan.to_dict()})
        else:
            print(plan.render())
            print("\nnothing changed. Re-run with --apply to execute (you will be asked to confirm)." if plan.writes
                  else "\nnothing to do: everything in the plan already exists.")
        return EXIT_OK
    ident = verify_context(ctx.env)
    ctx.log.info(f"context: {ident['user']} on {ident['subscription_name']} ({ident['subscription']}), "
                 f"RG {ctx.env.resource_group}")
    if not ctx.json:
        print(plan.render(), file=sys.stderr)
    confirm(plan, yes=getattr(ctx.args, "yes", False))
    result = apply_fn()
    if ctx.json:
        ctx.out(data={"mode": "apply", "plan": plan.to_dict(), "result": result})
    elif after:
        print(after(result))
    if isinstance(result, dict) and result.get("failed"):
        return EXIT_ERROR
    return EXIT_OK


# ---- commands --------------------------------------------------------------------------------
def cmd_preflight(args: argparse.Namespace) -> int:
    from .preflight import BLOCKED, render, run_preflight, scrub

    ctx = Ctx(args, "preflight")
    if not ctx.json:
        print(banner("robot", "🔎 FOMO Preflight"), file=sys.stderr)
    from .config import load_participant

    participant = args.participant or load_participant()
    if args.init_participant:
        if args.local_only or args.participant or args.fix:
            raise WorkshopError("--init-participant cannot be combined with --local-only, --participant or --fix")
        ctx.require_env()
        from .provision.participant import remember, resolve_participant

        names = resolve_participant(ctx.env, None, allow_new=True)
        if not participant:
            remember(names)
        participant = names.hackername
        if not ctx.json:
            print(f"Your hackername: {participant}", file=sys.stderr)
    report = run_preflight(ctx.env, participants=args.participants, participant=participant, smoke=args.smoke,
                           local_only=args.local_only)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    full = args.report_dir / f"readiness-{stamp}.json"
    share = args.report_dir / f"readiness-{stamp}.shareable.json"
    full.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    share.write_text(json.dumps(scrub(report.to_dict(), report.sensitive), indent=2, default=str), encoding="utf-8")
    ctx.out(render(report) + f"\nreport: {full}\nshareable (ids/users masked): {share}", report.to_dict())
    if args.fix:
        rc = _preflight_fix(ctx, report)
        if rc != EXIT_OK:
            return rc
    return EXIT_BLOCKED if report.verdict == BLOCKED else EXIT_OK


def _preflight_fix(ctx: Ctx, report: Any) -> int:
    """Only harmless, participant-scoped fixes are automated; everything else becomes a request."""
    from .azclient import az

    plan = Plan("preflight --fix", None, ctx.env.subscription_id, ctx.env.resource_group)
    actions: list[Callable[[], Any]] = []
    blocked = [c for c in report.checks if c.status in ("BLOCKED", "WARNING")]
    discovery = next((c for c in report.checks if c.id == "azure.discovery"), None)
    for c in blocked:
        if c.id == "azure.environment" and ctx.env.resource_group and discovery and discovery.data.get("deployment"):
            plan.add("write", str(ENV_FILE), f"env pull from resource group {ctx.env.resource_group}")
            actions.append(lambda: _env_pull(ctx, None))
        elif c.id.startswith("providers.") and c.status == "BLOCKED":
            ns = c.id.split(".", 1)[1]
            plan.add("update", f"resource provider {ns}", "register (subscription-level)", owner="admin")
            actions.append(lambda ns=ns: az("provider", "register", "-n", ns, "--wait", check=True))  # type: ignore[misc]
        else:
            plan.notes.append(f"{c.id}: {c.fix or c.evidence} (owner: {c.owner}; not automated)")
    if not plan.writes:
        print(plan.render() if not ctx.json else "")
        return EXIT_OK
    if not ctx.args.apply:
        print(plan.render() + "\n\nnothing changed. Re-run `fomo preflight --fix --apply` to apply, then the "
              "checks run again.")
        return EXIT_OK
    verify_context(ctx.env)
    print(plan.render(), file=sys.stderr)
    confirm(plan, yes=ctx.args.yes)
    for fn in actions:
        fn()
    ctx.log.info("re-checking after remediation")
    from .preflight import render, run_preflight

    again = run_preflight(Environment.load(), participants=report.participants)
    print(render(again))
    return EXIT_OK


def cmd_provision(args: argparse.Namespace) -> int:
    from .provision.ops import (
        PROVISION_NEXT,
        apply_provision,
        check_provision_prerequisites,
        links,
        plan_provision,
    )
    from .provision.participant import render_status, status

    ctx = Ctx(args, "provision")
    ctx.require_env()
    names = ctx.names(allow_new=True)
    if not ctx.json:
        print(banner("robot", f"🛠️ FOMO Provision for {names.hackername}"), file=sys.stderr)
    state = status(ctx.env, names)
    step = args.step
    if ctx.mode() != "check":
        check_provision_prerequisites(state, step)
    plan = plan_provision(ctx.env, names, state, smoke=not args.no_smoke, step=step)

    def after(result: dict[str, Any]) -> str:
        tail = (PROVISION_NEXT.format(run_dir=names.run_dir) if step in ("all", "agent")
                else "next: uv run fomo next")
        return (render_status(result["status"]) + "\n\n" + "\n".join(links(ctx.env, names)) + "\n\n" + tail)
    return write_flow(ctx, plan, lambda: apply_provision(ctx.env, names, ctx.log, smoke=not args.no_smoke,
                                                         step=step),
                      after=after, check=lambda: render_status(state) if not ctx.json else state)


def cmd_next(args: argparse.Namespace) -> int:
    from . import guide
    from .config import load_participant

    ctx = Ctx(args, "next")
    values: dict[str, str] = {}
    missing = ctx.env.missing()
    participant = args.participant or load_participant()
    if missing or not participant:
        reason = (f"workshop environment incomplete: {', '.join(missing)}" if missing
                  else "no hackername saved yet")
        result = guide.build("preflight", values, reason=reason)
    else:
        from .data.corpus import load_manifest
        from .provision.participant import status

        names = ctx.names()
        values = {"container": names.container, "raw_index": names.raw_index, "raw_indexer": names.raw_indexer,
                  "smart_index": names.smart_index, "simple_rag": names.simple_rag_agent,
                  "metadata_miner": names.metadata_miner_agent, "run_dir": names.run_dir}
        state = status(ctx.env, names)
        result = guide.next_checkpoint(state, values, expected_docs=len(load_manifest()[1]),
                                       classifier=Path(names.run_dir) / "miner" / "classifier.yaml")
    data = result.to_dict() | {"participant": participant}
    ctx.out((f"participant {participant}\n" if participant else "") + guide.render(result), data)
    return EXIT_OK


def cmd_miner(args: argparse.Namespace) -> int:
    from .provision import ops

    ctx = Ctx(args, f"miner-{args.miner_command}")
    ctx.require_env()
    names = ctx.names()
    if args.miner_command == "deploy":
        if not ctx.json:
            print(banner("robot", f"Metadata Miner for {names.hackername}"), file=sys.stderr)
        plan = ops.plan_miner_deploy(ctx.env, names, args.classifier)
        return write_flow(ctx, plan, lambda: ops.apply_miner_deploy(ctx.env, names, ctx.log, args.classifier,
                                                                     force=args.force),
                          after=lambda r: f"hosted agent {r['name']} v{r['version']} {r['status']}; "
                          f"identity {r['principal_id']}\nnext: uv run fomo miner run --hosted --apply")
    hosted = not args.local
    plan = ops.plan_miner_run(ctx.env, names, hosted=hosted, mode=args.mode, only=args.only, limit=args.limit,
                              dry_run=args.no_write)

    def after(r: dict[str, Any]) -> str:
        return (f"{r.get('runtime')}: {r.get('counts')}\nlabels: {r.get('labels')}\ntrace: {r.get('trace')}\n"
                + ("next: uv run fomo smart --apply" if not args.no_write else ""))
    return write_flow(ctx, plan, lambda: ops.apply_miner_run(ctx.env, names, ctx.log, hosted=hosted,
                                                             mode=args.mode, only=args.only, limit=args.limit,
                                                             dry_run=args.no_write, classifier=args.classifier),
                      after=after)


def cmd_smart(args: argparse.Namespace) -> int:
    from .provision import ops
    from .provision.participant import status

    ctx = Ctx(args, "smart")
    ctx.require_env()
    names = ctx.names()
    state = status(ctx.env, names)
    plan = ops.plan_smart(ctx.env, names, state, smoke=not args.no_smoke, reset=args.reset,
                          classifier=args.classifier)

    def after(r: dict[str, Any]) -> str:
        s = r["smart"]
        return (f"{names.smart_index}: document_type facets {s['facets']}\n"
                f"Smart RAG: {r['smart_rag']['name']} v{r['smart_rag']['version']} via knowledge base "
                f"{names.knowledge_base}; filter derived per question from {', '.join(s['scopes'])}"
                f"\nnext: uv run fomo eval")
    return write_flow(ctx, plan, lambda: ops.apply_smart(ctx.env, names, ctx.log, smoke=not args.no_smoke,
                                                         reset=args.reset, classifier=args.classifier),
                      after=after)


def cmd_eval(args: argparse.Namespace) -> int:
    from .provision.ops import run_eval

    ctx = Ctx(args, "eval")
    ctx.require_env()
    names = ctx.names()
    report = run_eval(ctx.env, names, ctx.log, agents=args.agents, k=args.k, query_ids=args.queries)
    ctx.out(report["table"] + f"\n\nreport: {report['paths']['markdown']}", report)
    return EXIT_OK


def cmd_eval_foundry(args: argparse.Namespace) -> int:
    from azure.ai.projects import AIProjectClient

    from .azclient import credential
    from .evaluation import foundry

    ctx = Ctx(args, "eval-foundry")
    ctx.require_env()
    if args.resume:
        with (
            AIProjectClient(endpoint=ctx.env.resolved_project_endpoint(), credential=credential()) as project,
            project.get_openai_client() as client,
        ):
            report = foundry.collect(client, ctx.env, args.resume)
        ctx.out(foundry.render(report) + f"\nreport: {report['paths']['markdown']}", report)
        return EXIT_OK if report["complete"] else EXIT_BLOCKED
    names = ctx.names()
    rows = foundry.load_dataset(suite=args.suite)
    judge = args.judge or ctx.env.chat_deployment
    with AIProjectClient(endpoint=ctx.env.resolved_project_endpoint(), credential=credential()) as project:
        context = foundry.inspect_targets(project, ctx.env, names, rows, judge)
    plan = foundry.evaluation_plan(ctx.env, names, rows, args.suite, context, judge)
    result: dict[str, Any] = {}

    def apply() -> dict[str, Any]:
        report = foundry.execute(ctx.env, names, suite=args.suite, judge=judge, root=args.workspace,
                                rows=rows, context=context)
        result.update(report)
        return report

    code = write_flow(ctx, plan, apply,
                      after=lambda r: foundry.render(r) + f"\nreport: {r['paths']['markdown']}")
    return EXIT_BLOCKED if result and not result["complete"] else code


def cmd_eval_ladder(args: argparse.Namespace) -> int:
    from .evaluation import ladder

    ctx = Ctx(args, "eval-ladder")
    if args.resume and args.report_only:
        report = ladder.finalize(args.resume.parent)
        ctx.out(ladder.render(report) + f"\nreport: {report['paths']['markdown']}", report)
        return EXIT_OK if report["complete"] else EXIT_BLOCKED
    ctx.require_env()
    names = ctx.names()
    done: dict[str, int] = {}
    split, repeats, judge = args.split, args.repeats, args.judge
    if args.resume:
        state = json.loads(args.resume.read_text(encoding="utf-8"))
        split, repeats, judge = state["split"], state["repeats"], state["judge"]
        saved = args.resume.parent / "responses.jsonl"
        done = {"routes": int("routes" in state),
                "responses": len(saved.read_text(encoding="utf-8").splitlines()) if saved.exists() else 0,
                "retrieval": int((args.resume.parent / "retrieval.json").exists()),
                "judge": int(bool((state.get("judge_eval") or {}).get("runs")))}
    repeats = repeats or (1 if split == "dev" else 3)
    if repeats < 1:
        raise WorkshopError("--repeats must be at least 1")
    rows = ladder.load_dataset(split=split)
    if args.resume:
        rows = [r for r in rows if r["id"] in set(state["question_ids"])]
    context = ladder.inspect(ctx.env, names, rows, judge)
    plan = ladder.build_plan(ctx.env, names, rows, context, split=split, repeats=repeats, judge=judge, done=done)

    result: dict[str, Any] = {}

    def apply() -> dict[str, Any]:
        report = ladder.execute(ctx.env, names, rows, context, split=split, repeats=repeats, judge=judge,
                                root=args.workspace, workers=args.workers, state_path=args.resume,
                                log=ctx.log.info)
        result.update(report)
        return report

    code = write_flow(ctx, plan, apply,
                      after=lambda r: ladder.render(r) + f"\nreport: {r['paths']['markdown']}")
    return EXIT_BLOCKED if result and not result["complete"] else code


def cmd_cheatcode(args: argparse.Namespace) -> int:
    from .provision import ops
    from .provision.participant import render_status, status

    ctx = Ctx(args, "cheatcode")
    ctx.require_env()
    names = ctx.names(allow_new=True)
    if not ctx.json:
        print(banner("robot", f"🚀 FOMO Cheatcode for {names.hackername}"), file=sys.stderr)
    state = status(ctx.env, names)
    plan = ops.plan_cheatcode(ctx.env, names, state, args.classifier)

    def after(r: dict[str, Any]) -> str:
        t = r["timings_s"]
        return (r["evaluation"]["table"] + "\n\n" + render_status(r["status"]) + "\n\n"
                + "\n".join(ops.links(ctx.env, names))
                + "\ntimings: " + ", ".join(f"{k} {v}s" for k, v in t.items())
                + f"\nevaluation report: {r['evaluation']['paths']['markdown']}"
                + "\nresources are left in place; `fomo cleanup --plan` shows what cleanup would delete")
    return write_flow(ctx, plan, lambda: ops.apply_cheatcode(ctx.env, names, ctx.log, state, args.classifier,
                                                             force_deploy=args.force_deploy), after=after)


def cmd_status(args: argparse.Namespace) -> int:
    from .provision.participant import render_status, status, write_manifest

    ctx = Ctx(args, "status")
    ctx.require_env()
    names = ctx.names()
    state = status(ctx.env, names, agents=not args.no_agents)
    path = write_manifest(ctx.env, names, state)
    ctx.out(render_status(state) + f"\nmanifest: {path}", state)
    return EXIT_OK


def cmd_reset(args: argparse.Namespace) -> int:
    from .cleanup import apply_reset, plan_reset

    ctx = Ctx(args, "reset-participant")
    ctx.require_env()
    names = ctx.names()
    plan = plan_reset(ctx.env, names, args.scope)
    return write_flow(ctx, plan, lambda: apply_reset(ctx.env, names, ctx.log, args.scope),
                      after=lambda r: f"reset done: {r}\nnext: `fomo status`")


def cmd_cleanup(args: argparse.Namespace) -> int:
    from .cleanup import apply_cleanup, inventory, plan_cleanup

    ctx = Ctx(args, "cleanup")
    ctx.require_env()
    names = ctx.names()
    inv = inventory(ctx.env, names)
    plan = plan_cleanup(ctx.env, names, inv, local=args.local)
    return write_flow(ctx, plan, lambda: apply_cleanup(ctx.env, names, ctx.log, inv, local=args.local),
                      after=lambda r: f"deleted {len(r['deleted'])}, failed {len(r['failed'])}"
                      + (f": {r['failed']}" if r["failed"] else ""), check=lambda: inv)


def cmd_capacity(args: argparse.Namespace) -> int:
    from . import capacity

    rep = capacity.capacity_report(args.participants, args.sku, replicas=args.replicas, hours=args.hours)
    if args.output == "json":
        print(json.dumps(rep.to_dict(), indent=2))
    else:
        print(capacity.render(rep))
    return EXIT_OK if rep.fits else EXIT_BLOCKED


def _env_select(ctx: Ctx) -> int:
    from .azclient import az

    args = ctx.args
    if not args.subscription or not args.resource_group:
        raise WorkshopError("env select requires explicit --subscription and --resource-group")
    if args.create_resource_group and not args.location:
        raise WorkshopError("--location is required with --create-resource-group")
    target = {"tenant_id": args.tenant, "subscription_id": args.subscription, "resource_group": args.resource_group}
    ctx.env = Environment(**target)
    verify_context(ctx.env)
    scope = ["--subscription", args.subscription, "--name", args.resource_group]

    def check_group() -> bool:
        exists = az("group", "exists", *scope)
        if not isinstance(exists, bool):
            raise Blocked("resource group existence could not be verified",
                          fix="rerun the read-only group check; do not assume the group is absent")
        if exists and args.create_resource_group:
            raise Blocked("resource group already exists; choose the existing-group branch",
                          fix="rerun env select without --create-resource-group")
        if not exists and not args.create_resource_group:
            raise Blocked("selected resource group does not exist",
                          fix="select an existing group or review a plan with "
                          "--create-resource-group --location <region>")
        return exists

    exists = check_group()
    plan = Plan("env select", None, args.subscription, args.resource_group)
    plan.notes.append(f"confirmed directory (tenant): {args.tenant}")
    if not exists:
        plan.add("create", f"resource group {args.resource_group}", f"empty group in {args.location}",
                 cost="no resources deployed; the resource group itself has no charge")
        plan.notes.append("requires Microsoft.Resources/subscriptions/resourceGroups/write at subscription scope; "
                          "no roles are granted")
    saved = Environment.load(environ={})
    same_target = not args.create_resource_group and all(getattr(saved, key) == value for key, value in target.items())
    if not same_target:
        plan.notes.append("changing target clears saved resource endpoints; participant hackername is unchanged")
    plan.add("write", str(ENV_FILE), "remember confirmed tenant, subscription and resource group")
    plan.notes.append("an empty or selected resource group does not establish workshop readiness")

    def apply() -> dict[str, Any]:
        check_group()
        if args.create_resource_group:
            az("group", "create", *scope, "--location", args.location)
        group = az("group", "show", *scope)
        expected_id = f"/subscriptions/{args.subscription}/resourceGroups/{args.resource_group}"
        if str(group.get("id", "")).lower() != expected_id.lower():
            raise Blocked("resource group verification returned a different target; context was not saved")
        selected = saved if same_target else Environment(**target)
        if args.create_resource_group:
            selected.location = args.location
        selected.extra["confirmed_azure_context"] = target
        path = selected.save()
        ctx.env = selected
        return {"path": str(path), "target": target, "created_resource_group": args.create_resource_group,
                "missing": selected.missing()}

    return write_flow(ctx, plan, apply, after=lambda r: f"saved confirmed Azure target to {r['path']}; "
                      "workshop resources still require a separate readiness check/deployment")


def _env_pull(ctx: Ctx, deployment: str | None) -> dict[str, Any]:
    from .azclient import az

    env = ctx.env
    if not env.resource_group:
        raise WorkshopError("--resource-group is required", fix="the facilitator shares the RG name")
    acct = az("account", "show")
    sub = env.subscription_id or acct["id"]
    target = az("account", "show", "--subscription", sub)
    if deployment is None:
        deps = az("deployment", "group", "list", "-g", env.resource_group, "--subscription", sub, "-o", "json")
        deps = [d for d in deps if (d.get("properties") or {}).get("provisioningState") == "Succeeded"
                and "foundry_account_name" in {k.lower() for k in ((d.get("properties") or {}).get("outputs") or {})}]
        if not deps:
            raise WorkshopError(f"no successful workshop deployment in {env.resource_group}",
                                fix="facilitator: `fomo infra deploy --parameters ... --apply`",
                                owner="facilitator")
        deps.sort(key=lambda d: d["properties"].get("timestamp", ""), reverse=True)
        deployment = deps[0]["name"]
    dep = az("deployment", "group", "show", "-g", env.resource_group, "-n", deployment, "--subscription", sub)
    pulled = Environment.from_bicep_outputs(dep["properties"].get("outputs") or {}, subscription_id=sub,
                                            tenant_id=target["tenantId"], resource_group=env.resource_group,
                                            deployment_name=deployment)
    confirmed = env.extra.get("confirmed_azure_context")
    if confirmed == {key: getattr(pulled, key) for key in ("tenant_id", "subscription_id", "resource_group")}:
        pulled.extra["confirmed_azure_context"] = confirmed
    path = pulled.save()
    ctx.env = pulled
    return {"path": str(path), "deployment": deployment, "missing": pulled.missing()}


def _env_discover(ctx: Ctx) -> int:
    from .discover import adopt_deployments, advise, inventory, resolve

    args, env = ctx.args, ctx.env
    target = {"tenant_id": env.tenant_id, "subscription_id": env.subscription_id,
              "resource_group": env.resource_group}
    if not all(target.values()) or env.extra.get("confirmed_azure_context") != target:
        raise Blocked("no confirmed Azure target for discovery",
                      fix="confirm tenant, subscription and resource group with `uv run fomo env select ... --plan`")
    choices = {"foundry": args.foundry_account, "project": args.project, "storage": args.storage_account,
               "search": args.search_service}

    def discover() -> tuple[Any, dict[str, Any], dict[str, str]]:
        inv = inventory(env.subscription_id, env.resource_group)
        fields, problems = resolve(inv, choices)
        return inv, fields, problems

    inv, fields, problems = discover()
    if inv.deployment or problems:
        fix, owner = advise(env, inv, problems, None)
        detail = "; ".join(f"{r}: {p}" for r, p in problems.items()) or f"FOMO deployment {inv.deployment}"
        raise Blocked(f"{inv.summary()} -> {detail}", fix=fix, owner=owner)
    notes = adopt_deployments(env, fields)
    plan = Plan("env discover", None, env.subscription_id, env.resource_group)
    plan.notes += [f"found: {inv.summary()}", *notes,
                   "read-only towards Azure: nothing is created, changed or granted"]
    adopted = ", ".join(f"{k}={v}" for k, v in fields.items() if k.endswith(("_name", "_deployment")))
    plan.add("write", str(ENV_FILE), adopted)

    def apply() -> dict[str, Any]:
        _, again, still = discover()
        names = {k: v for k, v in fields.items() if k.endswith("_name")}
        if still or any(again.get(k) != v for k, v in names.items()):
            raise Blocked("resource group changed since the plan; nothing saved", fix="rerun with --plan")
        saved = Environment.load(environ={})
        for key, value in fields.items():
            setattr(saved, key, value)
        path = saved.save()
        ctx.env = saved
        return {"path": str(path), "adopted": fields, "missing": saved.missing()}

    return write_flow(ctx, plan, apply, after=lambda r: f"saved discovered services to {r['path']}\n"
                      "next: uv run fomo preflight --participants 1 --participant <hackername>")


def cmd_env(args: argparse.Namespace) -> int:
    ctx = Ctx(args, "env")
    if args.env_command == "select":
        return _env_select(ctx)
    if args.env_command == "discover":
        return _env_discover(ctx)
    if args.env_command == "pull":
        r = _env_pull(ctx, args.deployment)
        ctx.out(f"wrote {r['path']} from deployment {r['deployment']}"
                + (f"; still missing {r['missing']}" if r["missing"] else "; complete"), r)
        return EXIT_OK if not r["missing"] else EXIT_BLOCKED
    data = ctx.env.to_dict()
    ctx.out("\n".join(f"{k}: {v}" for k, v in data.items() if v not in ("", None, {})), data)
    return EXIT_OK


def cmd_agents(args: argparse.Namespace) -> int:
    from .agents.prompt import PromptAgents
    from .provision.ops import canonical_question

    ctx = Ctx(args, "agents-ask")
    ctx.require_env()
    names = ctx.names()
    agent = names.simple_rag_agent if args.which == "simple" else names.smart_rag_agent
    question = args.question or canonical_question()
    ans = PromptAgents(ctx.env).ask(agent, question)
    data = {"agent": agent, "question": question, "answer": ans.text, "cited": ans.cited_doc_ids,
            "document_types": ans.document_types, "latency_ms": ans.latency_ms, "tool_calls": ans.tool_calls}
    routed = ("\nretrieved document types: " + ", ".join("/".join(t) or "none" for t in ans.document_types)
              if ans.document_types else "")
    ctx.out(f"{agent} ({ans.latency_ms} ms, {ans.tool_calls} search call(s))\nQ: {question}\n\n{ans.text}\n\n"
            f"cited: {', '.join(ans.cited_doc_ids) or 'nothing'}{routed}", data)
    return EXIT_OK


def cmd_infra(args: argparse.Namespace) -> int:
    from .azclient import az

    ctx = Ctx(args, f"infra-{args.infra_command}")
    rg = ctx.env.resource_group
    if not rg:
        raise WorkshopError("--resource-group is required")
    acct = az("account", "show")
    sub = ctx.env.subscription_id or acct["id"]
    ctx.env.subscription_id = sub
    exists = az("group", "exists", "-n", rg, "--subscription", sub)
    base = ["-g", rg, "-n", args.deployment, "-p", str(args.parameters), "--subscription", sub]
    if exists:
        ctx.log.info(f"what-if for {args.parameters} in {rg} ({acct['name']})")
        whatif = az("deployment", "group", "what-if", *base, "--no-pretty-print", "-o", "json", timeout=600)
        changes = [(c.get("changeType"), c.get("resourceId", "").split("/providers/", 1)[-1],
                    ", ".join(d.get("path", "") for d in (c.get("delta") or [])[:4]))
                   for c in (whatif or {}).get("changes", [])]
    else:
        changes = [("Create", f"resource group {rg} ({args.location})", "")]
    unsupported = sum(1 for t, _, _ in changes if t == "Unsupported")
    interesting = [c for c in changes if c[0] not in ("NoChange", "Ignore", "Unsupported")]
    plan = Plan(f"infra {args.infra_command}", None, sub, rg)
    for t, r, delta in interesting:
        verb = {"Create": "create", "Delete": "delete", "Modify": "update", "Deploy": "update"}.get(t, "update")
        plan.add(verb, r, f"{t}: {delta}" if delta else t, owner="facilitator")
    plan.notes.append(f"{len(changes) - len(interesting) - unsupported} resource(s) unchanged")
    if unsupported:
        plan.notes.append(f"{unsupported} role assignment(s) named from runtime principal IDs: what-if cannot "
                          "evaluate them (ARM limitation); deployment is idempotent for them")
    plan.notes.append("Bicep is incremental: resources not in the template are never deleted")
    if args.infra_command == "what-if":
        ctx.out(plan.render(), plan.to_dict())
        return EXIT_OK

    def apply() -> dict[str, Any]:
        if not exists:
            az("group", "create", "-n", rg, "-l", args.location, "--subscription", sub,
               "--tags", "workshop=fomo")
        ctx.log.info("deploying Bicep (5-15 minutes on a clean subscription)")
        az("deployment", "group", "create", *base, "-o", "none", timeout=3600)
        return _env_pull(ctx, args.deployment)
    return write_flow(ctx, plan, apply, after=lambda r: f"deployed; wrote {r['path']}\nnext: uv run fomo "
                      "preflight --participants <N>")


def cmd_facilitator(args: argparse.Namespace) -> int:
    from .facilitator import dashboard, render

    ctx = Ctx(args, "facilitator")
    ctx.require_env()
    d = dashboard(ctx.env, readiness_dir=args.readiness_dir, agents=not args.no_agents)
    ctx.out(render(d), d)
    return EXIT_OK


COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "preflight": cmd_preflight, "provision": cmd_provision, "next": cmd_next, "miner": cmd_miner, "smart": cmd_smart,
    "eval": cmd_eval, "eval-foundry": cmd_eval_foundry, "eval-ladder": cmd_eval_ladder,
    "cheatcode": cmd_cheatcode, "status": cmd_status,
    "reset-participant": cmd_reset,
    "cleanup": cmd_cleanup, "capacity": cmd_capacity, "env": cmd_env, "agents": cmd_agents, "infra": cmd_infra,
    "facilitator": cmd_facilitator,
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except ApprovalDeclined as exc:
        print(f"NOT APPROVED: {exc}" + (f"\n  fix: {exc.fix}" if exc.fix else ""), file=sys.stderr)
        return EXIT_BLOCKED
    except Blocked as exc:
        print(f"BLOCKED: {exc}\n  who: {exc.owner}" + (f"\n  next: {exc.fix}" if exc.fix else ""), file=sys.stderr)
        return EXIT_BLOCKED
    except WorkshopError as exc:
        print(f"ERROR ({type(exc).__name__}): {exc}\n  who: {exc.owner}" + (f"\n  fix: {exc.fix}" if exc.fix else ""),
              file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted; rerun the same command, every step is idempotent", file=sys.stderr)
        return EXIT_ERROR
    finally:
        # Release log files so in-process callers (tests, cleanup --local) can delete run folders.
        while _OPEN_LOGS:
            _OPEN_LOGS.pop().close()


if __name__ == "__main__":
    raise SystemExit(main())
