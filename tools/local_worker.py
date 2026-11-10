"""Hand small, well-described jobs to a local model (LM Studio) and check what comes back.

The main developer writes a jobs file: for each job the exact instruction, the code
excerpts the model needs to see, the files it may change and the checks that must
pass. This script then, for every job:

  1. sends the instruction and excerpts to the local model,
  2. applies the edit it returns (exact search/replace blocks, only in allowed files),
  3. compiles the changed files and runs the job's checks,
  4. commits the change on a work branch when everything passes - otherwise the
     model gets the error and tries again (three tries), and after that the change
     is undone and the job is marked failed.

Nothing lands on the main branch: the work branch is reviewed by the main developer
first. The report is logs/local_worker_report.md.

  python tools/local_worker.py tools/local_jobs/batch1.json
  python tools/local_worker.py jobs.json --only crash-help-token --branch local-model/try

LM Studio must be running its local server (default http://127.0.0.1:1234) with a
model loaded; --url and --model (or LOCAL_LLM_URL / LOCAL_LLM_MODEL) change them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIES = 3
SYSTEM = """You are a careful Python developer making ONE small change to an existing Windows desktop app \
(BaoBun, a game-server manager). Follow the instruction exactly and change nothing else.

Rules of this codebase:
- Follow the style of the code you are shown (naming, quoting, comments). Lines are at most 118 characters.
- Text the app shows to people is plain everyday language: what happened and what to do. No jargon, no blame.
- Never hard-code a PC-specific path, drive letter or user name. Never print or log passwords or tokens.

Reply with edit blocks ONLY - no explanation before or after. Each block is exactly:

FILE: relative/path.py
<<<<<<< SEARCH
(lines copied EXACTLY from the file as shown, including indentation, enough of them to be unique)
=======
(the lines that replace them)
>>>>>>> REPLACE

To create a new file, leave the SEARCH part empty. Several blocks are fine. Keep each SEARCH part short
(only the lines that change plus one or two unchanged lines around them)."""
BLOCK = re.compile(r"FILE:\s*(?P<path>[^\r\n]+)\r?\n<<<<<<< SEARCH\r?\n(?P<search>.*?)\r?\n?=======\r?\n"
                   r"(?P<replace>.*?)\r?\n?>>>>>>> REPLACE", re.S)


def run(cmd, timeout=600):
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def git(*args):
    code, out = run(["git"] + list(args))
    if code != 0:
        raise RuntimeError("git %s failed: %s" % (" ".join(args), out.strip()[-400:]))
    return out.strip()


def excerpt(spec: dict) -> str:
    """One piece of a file for the prompt, with a header saying where it is from."""
    path = ROOT / spec["path"]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if "lines" in spec:
        a, b = spec["lines"]
    elif "around" in spec:
        rx = re.compile(spec["around"])
        hit = next((i for i, ln in enumerate(lines) if rx.search(ln)), None)
        if hit is None:
            raise ValueError("%s: nothing matches %r" % (spec["path"], spec["around"]))
        a, b = hit + 1 - spec.get("before", 5), hit + 1 + spec.get("after", 5)
    else:
        a, b = 1, spec.get("head", 30)
    a, b = max(1, a), min(len(lines), b)
    return "--- %s (lines %d-%d of %d) ---\n%s" % (spec["path"], a, b, len(lines), "\n".join(lines[a - 1:b]))


def ask(url, model, prompt, max_tokens):
    body = json.dumps({"model": model, "temperature": 0.15, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.loads(r.read())
    text = data["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip(), data.get("usage") or {}


def apply_edits(reply: str, allowed: list) -> list:
    """Apply the model's blocks. Returns the changed paths; raises ValueError with a
    message the model can act on when a block can't be applied."""
    blocks = list(BLOCK.finditer(reply))
    if not blocks:
        raise ValueError("No edit blocks found. Reply ONLY with FILE: / <<<<<<< SEARCH / ======= / "
                         ">>>>>>> REPLACE blocks.")
    changed = []
    texts = {}
    for m in blocks:
        rel = m.group("path").strip().strip("`").replace("\\", "/")
        if rel not in allowed:
            raise ValueError("You may only change: %s (you tried %s)." % (", ".join(allowed), rel))
        path = ROOT / rel
        search, repl = m.group("search"), m.group("replace")
        if rel not in texts:
            texts[rel] = path.read_text(encoding="utf-8") if path.exists() else None
        cur = texts[rel]
        if search.strip() == "":
            if cur is not None:
                raise ValueError("%s already exists - use a SEARCH part to change it." % rel)
            texts[rel] = repl.rstrip("\n") + "\n"
        else:
            if cur is None:
                raise ValueError("%s doesn't exist." % rel)
            cur_n = cur.replace("\r\n", "\n")
            n = cur_n.count(search)
            if n != 1:
                raise ValueError("In %s the SEARCH part matches %d places (it must match exactly one). Copy the "
                                 "lines exactly as shown, with their indentation:\n%s" % (rel, n, search[:400]))
            texts[rel] = cur_n.replace(search, repl, 1)
        if rel not in changed:
            changed.append(rel)
    for rel in changed:
        p = ROOT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texts[rel], encoding="utf-8", newline="\n")
    return changed


def undo(changed):
    for rel in changed:
        code, _ = run(["git", "ls-files", "--error-unmatch", rel])
        if code == 0:
            run(["git", "checkout", "--", rel])
        else:
            try:
                (ROOT / rel).unlink()
            except OSError:
                pass


def check(job, changed):
    """"" when everything passes, else what failed (for the model and the report)."""
    for rel in changed:
        if rel.endswith(".py"):
            code, out = run([sys.executable, "-m", "py_compile", rel])
            if code != 0:
                return "%s doesn't compile:\n%s" % (rel, out.strip()[-800:])
        if rel.endswith(".json"):
            try:
                json.loads((ROOT / rel).read_text(encoding="utf-8"))
            except ValueError as e:
                return "%s is not valid JSON any more: %s" % (rel, e)
    for must in job.get("must_contain") or []:
        text = (ROOT / must["path"]).read_text(encoding="utf-8", errors="replace")
        if not re.search(must["regex"], text):
            return "%s: expected to find /%s/ after your change (%s)." % (must["path"], must["regex"],
                                                                         must.get("why", "the job asks for it"))
    for mustnt in job.get("must_not_contain") or []:
        text = (ROOT / mustnt["path"]).read_text(encoding="utf-8", errors="replace")
        if re.search(mustnt["regex"], text):
            return "%s: still contains /%s/ (%s)." % (mustnt["path"], mustnt["regex"],
                                                     mustnt.get("why", "the job asks to remove it"))
    for cmd in job.get("checks") or []:
        code, out = run([sys.executable if c == "{python}" else c for c in cmd], timeout=900)
        if code != 0:
            tail = "\n".join(out.strip().splitlines()[-25:])
            return "Check failed: %s\n%s" % (" ".join(cmd[1:]), tail[-1800:])
    return ""


def do_job(job, url, model, log):
    parts = [excerpt(s) for s in job.get("excerpts") or []]
    base = "JOB: %s\n\n%s\n\nYou may change only: %s\n\n%s" % (
        job["title"], job["instruction"].strip(), ", ".join(job["allowed"]), "\n\n".join(parts))
    feedback, record = "", {"id": job["id"], "title": job["title"], "tries": []}
    for attempt in range(1, TRIES + 1):
        prompt = base + (("\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED:\n%s\nFix that and reply with the complete "
                          "edit blocks again." % feedback) if feedback else "")
        t0 = time.time()
        try:
            reply, usage = ask(url, model, prompt, job.get("max_tokens", 2200))
        except Exception as e:
            record["tries"].append({"error": "model call failed: %s" % e})
            break
        changed, problem = [], ""
        try:
            changed = apply_edits(reply, job["allowed"])
            problem = check(job, changed)
        except ValueError as e:
            problem = str(e)
        record["tries"].append({"seconds": round(time.time() - t0), "tokens": usage.get("total_tokens"),
                                "changed": changed, "problem": problem})
        log("   try %d: %s" % (attempt, "ok" if not problem else problem.splitlines()[0][:110]))
        if not problem:
            git("add", "--", *changed)
            git("commit", "-q", "-m", "[local model] %s\n\nJob %s, drafted by %s on try %d. Not yet reviewed."
                % (job["title"], job["id"], model, attempt))
            record.update(result="committed", commit=git("rev-parse", "--short", "HEAD"), files=changed)
            return record
        undo(changed)
        feedback = problem[:1800]
    record["result"] = "failed"
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs")
    ap.add_argument("--branch", default="local-model/work")
    ap.add_argument("--only", help="comma-separated job ids")
    ap.add_argument("--url", default=os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:1234"))
    ap.add_argument("--model", default=os.environ.get("LOCAL_LLM_MODEL", ""))
    a = ap.parse_args()
    jobs = json.loads(Path(a.jobs).read_text(encoding="utf-8"))
    if a.only:
        jobs = [j for j in jobs if j["id"] in a.only.split(",")]
    if git("status", "--porcelain"):
        sys.exit("Commit or put away your own changes first: the worker needs a clean folder.")
    model = a.model
    if not model:
        with urllib.request.urlopen(a.url.rstrip("/") + "/v1/models", timeout=10) as r:
            model = json.loads(r.read())["data"][0]["id"]
    start = git("rev-parse", "--abbrev-ref", "HEAD")
    code, _ = run(["git", "rev-parse", "--verify", a.branch])
    git("checkout", "-q", a.branch) if code == 0 else git("checkout", "-q", "-b", a.branch)
    print("Working on branch %s (from %s) with %s - %d job(s)" % (a.branch, start, model, len(jobs)), flush=True)
    results = []
    try:
        for i, job in enumerate(jobs, 1):
            print("[%d/%d] %s" % (i, len(jobs), job["title"]), flush=True)
            try:
                results.append(do_job(job, a.url, model, lambda m: print(m, flush=True)))
            except Exception as e:
                results.append({"id": job["id"], "title": job["title"], "result": "error", "error": str(e)})
                run(["git", "checkout", "--", "."])
            print("   -> %s" % results[-1]["result"], flush=True)
    finally:
        git("checkout", "-q", start)
    lines = ["# Local model work - %s" % time.strftime("%Y-%m-%d %H:%M"), "",
             "Model: `%s`. Branch: `%s` (nothing is on `%s` until it has been reviewed)." % (model, a.branch, start),
             "", "| Job | Result | Tries | Commit | Files |", "|---|---|---|---|---|"]
    for r in results:
        lines.append("| %s | %s | %d | %s | %s |" % (r["title"], r["result"], len(r.get("tries") or []),
                                                    r.get("commit", "-"), ", ".join(r.get("files") or []) or "-"))
    for r in results:
        if r["result"] != "committed":
            lines += ["", "## %s - %s" % (r["title"], r["result"])]
            for n, t in enumerate(r.get("tries") or [], 1):
                lines += ["", "Try %d:" % n, "```", str(t.get("problem") or t.get("error"))[:1500], "```"]
            if r.get("error"):
                lines += ["", "```", r["error"][:1500], "```"]
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "local_worker_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (ROOT / "logs" / "local_worker_report.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    done = sum(1 for r in results if r["result"] == "committed")
    print("\n%d of %d job(s) committed on %s. Report: logs/local_worker_report.md" % (done, len(results), a.branch))


if __name__ == "__main__":
    main()
