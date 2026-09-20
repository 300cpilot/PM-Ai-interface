# Browser TLS Setup (required once per browser)

The PVE web UI is served over HTTPS on `:8006`, while the proxmox-ai backend is
reached through a TLS-terminating socat proxy on `:9443` (using the PVE host's
own certificate). Because the UI page and the backend are **different origins**,
every request the AI panel makes is a **cross-origin (CORS) request** — and
browsers apply strict rules to those.

## Symptom

In Firefox, the AI panel shows:

```
Connection error: TypeError: NetworkError when attempting to fetch resource
```

and the console shows:

```
Cross-Origin Request Blocked: ... (Reason: CORS request did not succeed). Status code: (null)
```

The Settings → Providers grid stays empty because `GET /config` never completes.

## Cause

The backend's TLS certificate is issued by the self-signed **PVE Cluster
Manager CA**. If the browser doesn't trust that CA:

- The CORS **preflight** (`OPTIONS`) request fails at the TLS layer, before any
  CORS headers are read — hence "CORS request did not succeed" with status
  `(null)`.
- Per-site certificate exceptions ("Accept the Risk") are **per host:port** and
  are **not reliably honored for CORS preflights** from a different origin
  (known Firefox behavior). Accepting the `:8006` cert does not cover `:9443`,
  and accepting `:9443` directly may still not fix preflights.
- Cert stores are per-browser: accepting the cert in Chromium/VS Code's browser
  does nothing for Firefox, and vice versa.

## Fix: trust the PVE Cluster CA (recommended)

Importing the cluster CA once makes `:8006` and `:9443` fully trusted — no
exceptions, no warnings, CORS preflights succeed.

1. Copy the CA from the PVE host (also kept in this repo at
   `../../pve-root-ca.pem`):

   ```bash
   scp root@<pve-host>:/etc/pve/pve-root-ca.pem .
   ```

2. **Firefox**: Settings → Privacy & Security → Certificates →
   **View Certificates…** → **Authorities** tab → **Import…** → select
   `pve-root-ca.pem` → check **"Trust this CA to identify websites"** → OK.

3. **Chromium/Chrome**: Settings → Privacy and security → Security →
   **Manage certificates** → **Authorities** → **Import** → select
   `pve-root-ca.pem` → check **"Trust this certificate for identifying
   websites"**.

   On Linux, Chromium uses the NSS shared DB (`~/.pki/nssdb`); the GUI import
   above is sufficient. VS Code's embedded browser follows Chromium's store.

4. Hard-refresh the PVE UI (**Ctrl+Shift+R**). The AI button appears and
   Settings → Providers loads.

Repeat per browser profile / machine that accesses the UI.

## Alternative: per-session cert exception (fragile)

Visiting `https://<pve-host>:9443/health` directly and clicking
**Advanced → Accept the Risk** works in Chromium, and in Firefox *sometimes* —
but Firefox may still block the CORS preflight despite the stored exception.
Use the CA import above instead.

## Verifying

```bash
# from any machine, after CA import — no -k needed:
curl https://<pve-host>:9443/health
# {"ok":true,"kill_switch":false}
```

In the browser console on the PVE page:

```js
PVE_AI_BACKEND   // "https://<pve-host>:9443"
fetch(PVE_AI_BACKEND + '/health').then(r => r.json()).then(console.log)
// {ok: true, kill_switch: false}
```

## Notes for installers

- `install.sh` writes the backend URL to `/opt/proxmox-ai/backend-url` and
  bakes it into `pvemanagerlib.js` via `frontend/patch.sh`. The dpkg re-patch
  hook (`frontend/99proxmox-ai-repatch`) re-reads that file after every
  `pve-manager` upgrade, so the URL survives updates.
- If you change `TLS_PORT` or the host IP, update
  `/opt/proxmox-ai/backend-url` and re-run `frontend/patch.sh`, then
  hard-refresh the UI.
