# Publishing to GitHub (sanitized)

How proxmox-ai gets published to the public GitHub repo
(`300cpilot/PM-Ai-interface`) without leaking private IPs, API keys, or
credentials. **Your local working copy is never modified** — everything
happens in a staging copy.

## The one-command path

```bash
cd ~/Projects/Proxmox-2026/proxmox-ai
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
    ~/Projects/Proxmox-2026/proxmox-ai/ /tmp/pm-ai-interface/
```

- `/tmp/pm-ai-interface` is a **full copy** of the project, kept separate so
  sanitization edits never touch the real files.
- `--delete` removes files in staging that you deleted locally.
- `github-push.sh` excludes itself (it contains the patterns it searches
  for, which would trip its own safety check).

### 2. Sanitize

Two substitutions, applied only to the staging copy:

| Pattern | Replacement | Files |
|---------|-------------|-------|
| vLLM API key (`--api-key <hex>`, `VLLM_KEY:-<hex>`) | `CHANGE-ME-vllm-api-key` / empty | `install.sh`, `install-remote.sh` |
| Private IPs `10.80.x.x` | `192.168.1.XXX` | installers, `docs/*.md`, `README.md`, `task-list-01.md`, `PLAN.md` |

### 3. Safety check (aborts on failure)

Before committing, the script greps the staging copy for:

- any 48-character hex string (API-key-shaped) that isn't `CHANGE-ME`
- any remaining `10.80.` address

**If anything matches, the script exits without pushing.** The match is
printed so you can add it to the sanitization rules.

### 4. Commit

First run initializes a fresh git repo in staging (`main` branch, remote
`origin` → the GitHub repo, `.gitignore` for `__pycache__`, `*.pyc`,
`.env`, `*.key`, `id_ed25519*`, `config.json`). Later runs just `git add -A`
and commit with your message. If nothing changed, it exits cleanly without
an empty commit.

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
   only thing connected to GitHub. The local project has no git remote.
4. **The safety check failing is a stop sign, not a suggestion.** Don't
   bypass it; fix the pattern.

## Manual fallback (if the script breaks)

```bash
# 1. fresh staging copy
rm -rf /tmp/pm-ai-interface
mkdir -p /tmp/pm-ai-interface
cp -a ~/Projects/Proxmox-2026/proxmox-ai/. /tmp/pm-ai-interface/
rm -rf /tmp/pm-ai-interface/backend/__pycache__

# 2. sanitize
cd /tmp/pm-ai-interface
sed -i -E 's/--api-key [a-f0-9]{32,}/--api-key CHANGE-ME-vllm-api-key/g' install.sh install-remote.sh
sed -i -E 's/10\.80\.[0-9]+\.[0-9]+/192.168.1.XXX/g' install.sh install-remote.sh docs/*.md README.md task-list-01.md PLAN.md

# 3. verify clean — must print nothing
grep -rnE '10\.80\.|[a-f0-9]{48}' . | grep -v CHANGE-ME

# 4. commit + push
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
| GitHub shows a Mermaid render error | Node labels with spaces/special chars need quotes; subgraphs need explicit IDs to be edge targets (fixed in README — keep that style) |
