# Publishing to GitHub (sanitized)

How proxmox-ai gets published to the public GitHub repo
(`300cpilot/PM-Ai-interface`) without leaking private IPs, API keys,
hostnames, or usernames. **Your local working copy is never modified** —
everything happens in a staging copy.

proxmox-ai is a standalone project at `~/Projects/proxmox-ai` with its own
local git repo (real content, no remote — for local history/diff safety
only). The staging copy described below is a **separate** repo that is the
only thing actually connected to GitHub.

## The one-command path

```bash
cd ~/Projects/proxmox-ai
./github-push.sh "your commit message"
```

That's it for normal use. The rest of this doc explains what the script does
and how to do it manually / troubleshoot.

## What the script does

`github-push.sh` performs five steps:

### 1. Sync to staging

```bash
rsync -a --delete \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
    --exclude='github-push.sh' \
    ~/Projects/proxmox-ai/ ~/.cache/pm-ai-interface/
```

- `~/.cache/pm-ai-interface` is a **full copy** of the project, kept separate
  so sanitization edits never touch the real files. It defaults to a durable
  path under your home directory — **not `/tmp`** — because `/tmp` can be
  wiped on reboot, and losing this directory's git history once already
  caused a disconnected-history push failure (see step 4 and
  Troubleshooting).
- `--delete` removes files in staging that you deleted locally.
- `github-push.sh` excludes itself (it contains the patterns it searches
  for, which would trip its own safety check).

### 2. Sanitize

Three substitutions, applied only to the staging copy:

| Pattern | Replacement | Files |
|---------|-------------|-------|
| vLLM API key (`--api-key <hex>`, `VLLM_KEY:-<hex>`) | `CHANGE-ME-vllm-api-key` / empty | `install.sh`, `install-remote.sh` |
| Private IPs `10.80.x.x` | `192.168.1.XXX` | installers, `docs/*.md`, `README.md`, `task-list-01.md`, `PLAN.md`, `tests/*.py` |
| Real dev-environment hostname/domain + username (see `github-push.sh` step 2 for the literal patterns \u2014 not reproduced here on purpose) | generic placeholders | `README.md`, `PLAN.md`, `task-list-01.md`, `docs/*.md`, `backend/*.py`, `tests/*.py` |

### 3. Safety check (aborts on failure)

Before committing, the script greps the **entire** staging copy (all file
types, `.git` excluded) for:

- any 48-character hex string (API-key-shaped) that isn't `CHANGE-ME`
- any remaining private IP octet
- the real dev-environment hostname/domain and username (see the literal
  patterns inside `github-push.sh` step 3 — not reproduced here on purpose,
  so this doc doesn't trip its own safety check)

**If anything matches, the script exits without pushing.** The match is
printed so you can add it to the sanitization rules. This check is
intentionally broader than the sed passes above (whole-tree, not just the
listed files) so a leak in a file the sed rules don't target still gets
caught rather than silently slipping through.

### 4. Commit

If `.git` is missing in staging, the script does **not** assume this is the
first-ever publish — it checks whether the GitHub repo already has commits
(`git ls-remote`). If it does, it reconnects to that real history (`git
fetch` + `git reset` — moves the branch pointer without touching your
freshly-synced files) instead of starting a disconnected orphan commit. Only
if the remote truly has no history yet does it start fresh with a new
`.gitignore` (`__pycache__`, `*.pyc`, `.env`, `*.key`, `id_ed25519*`,
`config.json`).

Later runs just `git add -A` and commit with your message. If nothing
changed, it exits cleanly without an empty commit.

Commits are authored as `300cpilot <300cpilot@users.noreply.github.com>`.

### 5. Push

`git push origin main`. Prompts for GitHub username + token the first time
(or whenever the cached credential expires) — use a PAT, not your account
password (see below).

## GitHub credentials

Pushing needs a **Personal Access Token** — GitHub passwords don't work for
git over HTTPS.

- **Classic token** (https://github.com/settings/tokens): check the
  top-level **`repo`** scope (required because the repo is private).
- **Fine-grained token**: select the `PM-Ai-interface` repository and set
  **Contents: Read and write**.

To cache it so you're not prompted every time:

```bash
git config --global credential.helper store
```

(Stored in plaintext at `~/.git-credentials` — fine on a single-user
machine; use `cache` instead if you prefer in-memory only.)

If a push fails with `403 Write access to repository not granted` after
you've fixed the token, clear the stale cached credential:

```bash
git credential erase <<EOF
protocol=https
host=github.com
EOF
```

## Rules for keeping the repo clean

1. **Never commit secrets locally in the first place.** The script is a
   safety net, not a lifestyle. API keys belong in env vars or config files
   that are gitignored — see task 7.8 for why.
2. **If you add a new secret/IP/hostname pattern**, add a matching `sed`
   rule to the sanitize step in `github-push.sh` — and to its safety check.
3. **Never push from the real working directory.** The staging repo is the
   only thing connected to GitHub. The local project's own git repo
   (`~/Projects/proxmox-ai/.git`) has no remote configured.
4. **The safety check failing is a stop sign, not a suggestion.** Don't
   bypass it; fix the pattern.

## Manual fallback (if the script breaks)

```bash
# 1. fresh staging copy
rm -rf ~/.cache/pm-ai-interface
mkdir -p ~/.cache/pm-ai-interface
cp -a ~/Projects/proxmox-ai/. ~/.cache/pm-ai-interface/
rm -rf ~/.cache/pm-ai-interface/backend/__pycache__
rm -rf ~/.cache/pm-ai-interface/.git   # do NOT keep a stale/disconnected .git

# 2. sanitize — all three passes, matching github-push.sh step 2 exactly
# (copy the actual sed commands from github-push.sh rather than retyping
# them here — retyping the real patterns in this doc is what tripped the
# safety check the first time this section was written)
cd ~/.cache/pm-ai-interface

# 3. verify clean — must print nothing (same 3 patterns as github-push.sh step 3)
grep -rInE '10\.80\.[0-9]|[a-f0-9]{48}' . --exclude-dir=.git | grep -v CHANGE-ME

# 4. reconnect to the real remote history instead of starting an orphan,
#    then commit + push
git init -q -b main
git remote add origin https://github.com/300cpilot/PM-Ai-interface.git
git fetch -q origin main
git reset -q origin/main   # moves the branch pointer only; working tree unchanged
git add -A
git commit -m "message"
git push origin main
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `403 Write access not granted` | Token lacks `repo` scope (classic) or Contents:write (fine-grained); then erase the cached credential and retry |
| `unable to read askpass` / `wish: not found` | Broken GUI askpass — run `unset GIT_ASKPASS SSH_ASKPASS` in that shell, or push with the token in the URL once: `git push https://300cpilot:TOKEN@github.com/300cpilot/PM-Ai-interface.git main` |
| `no upstream branch` | `git push --set-upstream origin main` (once) |
| Safety check flags a false positive | Narrow the pattern in the script, or whitelist the specific string with a `grep -v` |
| `! [rejected] main -> main (fetch first)` / unrelated histories | Staging's `.git` got wiped (old default was `/tmp`, cleared on reboot) and a prior run started a disconnected orphan commit. `github-push.sh` now detects this (step 4) and reconnects automatically; if you still hit it from an old staging dir, `rm -rf` it and re-run — the script will reconnect to the real remote history instead of orphaning again |
| GitHub shows a Mermaid render error | Node labels with spaces/special chars need quotes; subgraphs need explicit IDs to be edge targets (fixed in README — keep that style) |
