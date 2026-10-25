"""Local, atomic development checkpoint; never starts Codex or guesses account usage."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def quota_summary(payload, now=None):
    """Accept an explicit app-tool snapshot; copy only public window counters."""
    now = now or dt.datetime.now(dt.timezone.utc)
    unknown = dict(source="unavailable", decision="checkpoint_by_stage", windows=[])
    if not isinstance(payload, dict):
        return unknown
    try:
        observed = dt.datetime.fromisoformat(payload["observed_at"])
        if observed.tzinfo is None or not -60 <= (now-observed).total_seconds() <= 900:
            return dict(unknown, source="stale_or_invalid_snapshot")
    except (KeyError, TypeError, ValueError):
        return dict(unknown, source="missing_snapshot_time")
    limits = payload.get("rateLimitsByLimitId", {}).get("codex") or payload.get("rateLimits") or {}
    windows = []
    for name in ("primary", "secondary"):
        window = limits.get(name) or {}
        used = window.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used) or not 0 <= used <= 100:
            continue
        minutes, reset = window.get("windowDurationMins"), window.get("resetsAt")
        windows.append(dict(name=name, used_percent=used, remaining_percent=100-used,
                            duration_minutes=minutes if isinstance(minutes,int) and not isinstance(minutes,bool) and minutes>0 else None,
                            resets_at_unix=reset if isinstance(reset,int) and not isinstance(reset,bool) and reset>0 else None))
    stopped = payload.get("ordinaryUsageAllowed") is False or limits.get("spendControlReached") is True or bool(limits.get("rateLimitReachedType"))
    decision = "finish_current_and_pause" if stopped or any(w["remaining_percent"] <= 10 for w in windows) else "may_start_next_stage" if windows else "checkpoint_by_stage"
    return dict(source="provided_app_tool_snapshot",observed_at=observed.isoformat(),decision=decision,windows=windows)


def git_state(root):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root).decode("utf8",errors="strict").strip()
    # Record names/status only. Never copy diffs, untracked contents or environment variables.
    return dict(branch=git("branch","--show-current"),commit=git("rev-parse","HEAD"),
                status=git("status","--short","--untracked-files=all"),
                recent_commits=git("log","-5","--format=%h %s").splitlines())


def atomic_json(path, payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix="checkpoint-",suffix=".part",dir=path.parent)
    try:
        with os.fdopen(descriptor,"w",encoding="utf8",newline="\n") as stream:
            json.dump(payload,stream,ensure_ascii=False,indent=2);stream.write("\n");stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage",required=True)
    parser.add_argument("--next",action="append",default=[],dest="next_steps")
    parser.add_argument("--report",type=Path,action="append",default=[])
    parser.add_argument("--quota-file",type=Path)
    args=parser.parse_args()
    now=dt.datetime.now(dt.timezone.utc)
    quota=json.loads(args.quota_file.read_text(encoding="utf8")) if args.quota_file else None
    reports=[]
    for path in args.report:
        path=path.resolve()
        if not path.is_file():parser.error("Report missing: "+str(path))
        with path.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
        reports.append(dict(path=str(path),bytes=path.stat().st_size,sha256=digest))
    payload=dict(version=1,created_utc=now.isoformat(),stage=args.stage,git=git_state(ROOT),
                 quota=quota_summary(quota,now),reports=reports,next_steps=args.next_steps,
                 automatic_resume_enabled=False,resume_documents=["PROJECT_PROGRESS.md","DEVELOPMENT_PLAN.md"])
    path=ROOT/".development/checkpoint.json";atomic_json(path,payload)
    print(json.dumps(dict(checkpoint=str(path),branch=payload["git"]["branch"],commit=payload["git"]["commit"],
                         quota_decision=payload["quota"]["decision"],automatic_resume_enabled=False),ensure_ascii=False,indent=2))


if __name__=="__main__":main()
