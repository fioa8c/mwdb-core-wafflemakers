# Deploying mwdb-core-wafflemakers to Digital Ocean

## Recommended droplet

- **Size:** 4 GB RAM / 2 vCPU / 80 GB SSD ($24/mo, Basic Droplet)
- **Image:** Ubuntu 24.04 LTS
- **Region:** closest to your team
- **Auth:** SSH key (not password)

Memory budget: PostgreSQL ~512 MB, gunicorn (2 workers) ~200 MB, Redis ~50 MB, nginx ~50 MB, Traefik ~50 MB, OS/Docker ~500 MB. ~1.4 GB active, 2.6 GB headroom. The PHP sandbox (when started for analysis) adds ~512 MB temporarily.

## 1. Initial server setup

SSH into the droplet:

```bash
ssh root@YOUR_DROPLET_IP
```

Install Docker:

```bash
curl -fsSL https://get.docker.com | sh
```

Create a non-root user for running the app:

```bash
adduser --disabled-password mwdb
usermod -aG docker mwdb
su - mwdb
```

## 2. Clone the repository

```bash
git clone git@github.com:fioa8c/mwdb-core-wafflemakers.git
cd mwdb-core-wafflemakers
```

If the PHP sandbox is needed for analysis, also clone:

```bash
cd ..
git clone git@github.com:fioa8c/waffle-makers-tooling.git
cd mwdb-core-wafflemakers
```

## 3. Configure environment

Generate secrets:

```bash
./gen_vars.sh
```

Save the printed admin password — it's only shown once.

Edit `mwdb-vars.env` to set production values:

```bash
# Required: set your domain as the base URL
sed -i "s|MWDB_BASE_URL=.*|MWDB_BASE_URL=https://YOUR_DOMAIN|" mwdb-vars.env

# Optional: enable rate limiting (recommended for internet-facing)
echo "MWDB_ENABLE_RATE_LIMIT=1" >> mwdb-vars.env

# Optional: disable public registration
echo "MWDB_ENABLE_REGISTRATION=0" >> mwdb-vars.env
```

Create the `.env` file for the compose variables from the template:

```bash
cp .env.example .env
nano .env   # replace every placeholder
```

`.env.example` documents each variable: `MWDB_DOMAIN` (Traefik host) and `ACME_EMAIL` (Let's Encrypt contact), plus the Karton stack's `VPC_BIND_IP` (this droplet's private/VPC address), `REDIS_PASSWORD`, `SILO_ACCESS_KEY` / `SILO_SECRET_KEY`, `KARTON_MWDB_API_KEY` and the optional `MWDB_MALLEUS_SET_GROUPS` / `MWDB_MALLEUS_READ_GROUPS` / `MWDB_MALLEUS_REPORTER_LOGINS`. Generate the secrets with `openssl rand -hex 24` (alphanumeric only: the Redis password is embedded in a `redis://` URI). The compose file refuses to start while a required variable is unset, so Redis and S3 can never end up published on all interfaces.

Replace `MWDB_DOMAIN` with your actual domain (e.g., `mwdb.wafflemakers.xyz`) and `ACME_EMAIL` with the email for Let's Encrypt notifications. Find the VPC address with `ip -4 addr show eth1` (DigitalOcean's private interface; the address is also shown in the control panel under Networking).

Create `karton.ini` from the template. It holds no secrets (those come from `.env`), so normally it needs no edits:

```bash
cp deploy/karton.ini.example karton.ini
```

`KARTON_MWDB_API_KEY` is only known after you create the `karton` user (section 9). Put any non-empty placeholder in `.env` for now; the stack starts, and the reporter fails authentication until you replace it.

## 4. DNS and firewall

Point your domain to the droplet's IP address:

- **A record:** `YOUR_DOMAIN` → `YOUR_DROPLET_IP`

Wait for DNS propagation (check with `dig YOUR_DOMAIN`). Traefik will fail to get a TLS certificate if DNS isn't pointing to the droplet yet.

**Firewall:** ports 80 and 443 must be reachable from the public internet
(`0.0.0.0/0`), not just your office/VPN. Let's Encrypt validates the
TLS-ALPN-01 challenge by connecting back to the droplet on 443; if its
validators are blocked, certificate issuance fails with `Timeout during
connect (likely firewall problem)` and traefik keeps serving its self-signed
default cert. If you use a Digital Ocean cloud firewall, allow 80/443 from
`Anywhere (IPv4 + IPv6)`.

Redis (6379) and S3 (9000) are the opposite case: reachable from the worker droplet's VPC address only. Rules are in section 9.

## 5. Build and start

```bash
docker compose -f docker-compose-prod.yml up -d --build
```

First run takes 5-10 minutes (building images from source, compiling py-tlsh, building the React frontend). Subsequent starts are fast (cached layers).

Watch the logs to confirm everything starts:

```bash
docker compose -f docker-compose-prod.yml logs -f
```

Look for:
- `mwdb-1`: `Configuring mwdb-core instance` → `Listening at: http://0.0.0.0:8080`
- `traefik-1`: `Obtaining certificate...` → `Certificate obtained successfully`
- `mwdb-web-1`: nginx startup message
- `karton-system-router-1`: starts once Redis and S3 (`silo`) are healthy

Once you see all three, the app is live at `https://YOUR_DOMAIN`.

## 6. Verify

```bash
# Health check
curl -sf https://YOUR_DOMAIN/api/ping
# Expected: {"status":"ok"}

# Login
curl -sf -X POST https://YOUR_DOMAIN/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"login":"admin","password":"YOUR_ADMIN_PASSWORD"}'
# Expected: {"login":"admin","token":"..."}
```

Open `https://YOUR_DOMAIN` in a browser and log in with admin credentials.

## 7. Import the threat library (optional)

If you have the threat library on the server:

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-prod.yml run --rm \
  -v /path/to/jetpack-threat-library:/import \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core import-jetpack-threat-library /import
```

## 8. Run PHP normalization (optional)

Start the sandbox service temporarily:

```bash
docker compose -f docker-compose-prod.yml -f docker-compose-sandbox.yml up -d sandbox
```

Run normalization:

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-prod.yml -f docker-compose-sandbox.yml run --rm \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core normalize-php http://sandbox
```

Run evalhook analysis:

```bash
MWDB_ENABLE_HOOKS=0 docker compose -f docker-compose-prod.yml -f docker-compose-sandbox.yml run --rm \
  --entrypoint "" mwdb /app/.venv/bin/mwdb-core evalhook-php http://sandbox
```

Stop the sandbox when done:

```bash
docker compose -f docker-compose-prod.yml -f docker-compose-sandbox.yml stop sandbox
```

## 9. Karton and the worker droplet

The prod stack runs a Karton pipeline: MWDB hands every upload to Karton (`karton-system-router`, `karton-classifier`), and two Malleus consumers on a second droplet in the same VPC analyse it: `malleus-static` (rules, deobfuscation, LLM read) and `malleus-detonate` (runs PHP that needs runtime evidence in KadathSandbox). Results go back through `karton-mwdb-reporter`, which writes attributes and tags to MWDB. Redis (6379) and S3/silo (9000), which Karton uses as message bus and resource store, are published on this droplet's VPC address only so the worker can reach them.

The depth of analysis is decided from the tags present on the object when the Karton task is spawned (at upload), so feeders must tag at upload; a tag added later has no effect until the object is reanalysed.

### Before deploying

Take a database backup first (the manual backup in section 10), because `mwdb-core configure` swallows upgrade failures: a migration that fails during the deploy does not stop the deploy, so the only way back is the dump.

```bash
docker compose -f docker-compose-prod.yml exec -T postgres \
  pg_dump -U mwdb -d mwdb --format=custom > mwdb_pre_detonate_$(date +%Y%m%d).dump
```

Keep the dump off the droplet. After the deploy (step 1 of "Rollout order"), check `docker compose -f docker-compose-prod.yml logs mwdb | grep -i -e malleus -e alembic -e error` for failures; the restore is in section 10.

### Configuration files

Done in section 3: `.env` from `.env.example` (including `VPC_BIND_IP`, `REDIS_PASSWORD`, `SILO_ACCESS_KEY`, `SILO_SECRET_KEY`) and `karton.ini` from `deploy/karton.ini.example`. Secrets are injected as karton-core environment overrides (`KARTON_REDIS_PASSWORD`, `KARTON_S3_ACCESS_KEY`, ...) and never written to `karton.ini`. Start the stack (section 5) before continuing.

The `mwdb-core` CLI in this repo has no user or API-key commands, so the accounts below are created in the web UI as the admin user.

### The `karton` reporter account

1. Settings → Users → Create user. Login `karton`, any e-mail, turn off "Send e-mail with set password link". The account is used through its API key only and never logs in.
2. Open the user → Capabilities (a user's capabilities are those of its private group, also named `karton`) and add: `access_all_objects`, `adding_tags`, `adding_comments`, `adding_parents`, `adding_files`, `adding_blobs`, `karton_assign`, and `unlimited_requests` if `MWDB_ENABLE_RATE_LIMIT=1` (section 3 recommends it). `karton_assign` is required: the reporter uploads every layer and LLM report with the analysis id of the sample's task, and MWDB answers 403 to that without it. The sample itself still gets its tags and attributes, so a missing `karton_assign` shows up only as missing children (step 4 of "Verify the pipeline").
3. Open the user → Manage API keys, create a key and copy it. Put it in `.env` as `KARTON_MWDB_API_KEY`, then apply it:

```bash
docker compose -f docker-compose-prod.yml up -d karton-mwdb-reporter
```

The reporter authenticates with this key only. It has no admin credentials and does not read `mwdb-vars.env`.

Attribute rights (read and set on the Malleus attributes) are not capabilities. The `malleus` plugin creates the attribute definitions and the `AttributePermission` rows at every MWDB start (`mwdb-core configure`) for the groups named in `MWDB_MALLEUS_SET_GROUPS` (default `karton`) and `MWDB_MALLEUS_READ_GROUPS` (default `jetpack-scan,wpcloud-scan,elderward`); groups that do not exist yet are skipped. Those two variables are the source of truth: the flags are rewritten on every start, and removing a group from them does not revoke its rows (delete them under Settings → Attributes → the key → Permissions). `MWDB_MALLEUS_REPORTER_LOGINS` (default `karton`) names the reporter login; a `source:` tag it creates is removed, and when it creates a new analysis's `malleus_analysis` value or `verdict:*` tag the plugin removes the previous run's `malleus_*` / `sandbox_*` values and `verdict:*` tags, so a reanalysis replaces the verdict instead of adding a second one. The reporter's login must therefore be listed there. Create all accounts in this section first, then restart MWDB once:

```bash
docker compose -f docker-compose-prod.yml restart mwdb
docker compose -f docker-compose-prod.yml logs mwdb | grep -i malleus
```

### Scanner accounts

Create one account per scanner, named exactly `jetpack-scan`, `wpcloud-scan` and `elderward` (same steps as above: no e-mail, API key only). Give each the capability `adding_tags` and nothing else. The plugin gives them read access to the Malleus attributes and write access to `context`, and rejects `source:` tags that do not carry the account's own login. Hand each scanner its own API key.

Feeder contract: send `context` and the `source:<scanner>` tag in the upload options (`POST /api/file` with the `options` form field carrying `attributes` and `tags`). Attributes and tags added after the upload never reach the Karton consumer unless the object is reanalysed, so a `context` added later is only stripped and stored, not used by the analysis. For `context`: an upload whose `context` attribute holds keys other than `scanner`, `scanner_version`, `rules`, `path` and `engine` is rejected with HTTP 400 (`context attribute has disallowed keys: ...`), because MWDB hands the upload's attributes to Karton before any hook could strip them. A `context` added to an existing object later is stripped to the allowed keys and the object is tagged `wm:context-stripped`. A foreign `source:` tag is removed after the upload, but the Karton task already carries it, so a scanner key that tags `source:researcher` at upload still gets researcher depth for that task. This is an accepted residual: scanner keys belong to trusted internal systems.

### Researchers

Give the researchers' group the capabilities `reading_all_attributes` (to read the Malleus attributes) and `karton_reanalyze` (to resend a sample to Karton with its current tags), under Settings → Groups → the group → Capabilities. Scanner accounts keep `adding_tags` only, and the `karton` user does not need `karton_reanalyze`.

### Keep `MWDB_SANDBOX_URL` unset

Do not set `MWDB_SANDBOX_URL` on `mwdb` in `.env`, `mwdb-vars.env` or the compose file. When it is set, the synchronous PHP normalize hook runs on every upload and adds a `normalized-php` child per sample next to the layers the pipeline produces. Batch normalization stays available through the CLI in section 8.

### Worker droplet

The account that owns the droplet is the operator's decision. Put it in the same region and VPC as the MWDB droplet, so Redis and S3 stay on private addresses. Find both with `doctl` (or the control panel):

```bash
doctl compute droplet get <mwdb-droplet-id> --format VPCUUID,Region,PrivateIPv4
```

```bash
doctl compute droplet create malleus-worker \
  --size c-8 \
  --image ubuntu-24-04-x64 \
  --region <MWDB droplet's region> \
  --vpc-uuid <MWDB droplet's VPC UUID> \
  --tag-name malleus-worker \
  --ssh-keys <key ids or fingerprints>
```

`c-8` is the CPU-Optimized plan with 8 vCPUs and 16 GB RAM, $168/month. It runs the LLM (Ollama, CPU only), the `cx` deobfuscation container and Kadath's containers side by side, so do not shrink it without measuring. The `malleus-worker` tag is what both firewalls below match on.

### Firewalls

Redis (6379) and S3/silo (9000) must be reachable from the worker droplet only, never from the public internet. Compose already publishes both on `VPC_BIND_IP` only. Docker-published ports bypass `ufw`'s rules, so the DigitalOcean cloud firewall is the control that counts and `ufw` is the second layer.

1. **Worker cloud firewall** `malleus-worker-fw`, applied to the tag `malleus-worker`: inbound TCP 22 from the operator's address only and nothing else; outbound everything. A DigitalOcean cloud firewall with no outbound rules permits no outbound traffic, so the outbound allow-all is written out per protocol (it also carries the worker's Redis/S3 traffic to the MWDB droplet and Kadath's egress through its gateway):

   ```bash
   doctl compute firewall create --name malleus-worker-fw \
     --tag-names malleus-worker \
     --inbound-rules "protocol:tcp,ports:22,address:<operator address>/32" \
     --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0 protocol:udp,ports:all,address:0.0.0.0/0 protocol:icmp,address:0.0.0.0/0"
   ```

   The rules cover IPv4 only; leave IPv6 off on the worker droplet.

2. **MWDB droplet cloud firewall.** Add inbound TCP 6379 and TCP 9000 with the source tag `malleus-worker`, and nothing from `Anywhere`. Use the ID of the firewall the MWDB droplet already has (`doctl compute firewall list`):

   ```bash
   doctl compute firewall add-rules <mwdb-firewall-id> \
     --inbound-rules "protocol:tcp,ports:6379,tag:malleus-worker protocol:tcp,ports:9000,tag:malleus-worker"
   ```

3. **`ufw` on the MWDB droplet**, with `WORKER_VPC_IP` the worker's private address (`doctl compute droplet get <worker-id> --format PrivateIPv4`):

   ```bash
   ufw allow from WORKER_VPC_IP to any port 6379 proto tcp
   ufw allow from WORKER_VPC_IP to any port 9000 proto tcp
   ```

4. **Verify both directions.** From a machine outside the VPC, both must fail:

   ```bash
   nc -vz YOUR_DROPLET_IP 6379
   nc -vz YOUR_DROPLET_IP 9000
   ```

   From the worker, both must succeed:

   ```bash
   nc -vz VPC_BIND_IP 6379
   nc -vz VPC_BIND_IP 9000
   ```

   `VPC_BIND_IP` is the MWDB droplet's private address (the value in its `.env`).

### Worker install

Install and configure the worker following `deploy/worker/README.md` in the Malleus repository: the host packages, users and directories, config and units (Part 1), and its Kadath section (Docker CE, the pinned KadathSandbox checkout, `make up`, `make selftest`, the manual offering). Its `karton.ini` points at `VPC_BIND_IP`: Redis `host` with the `REDIS_PASSWORD`, S3 `address = http://VPC_BIND_IP:9000` with `SILO_ACCESS_KEY` / `SILO_SECRET_KEY`, bucket `karton`. Secrets go in `/etc/malleus/env`, never into `karton.ini`.

### Rollout order

Follow this order; steps 5 and 6 repeat the worker README's rollout order, and detonate must be enabled before static is allowed to hand it work.

1. Fork deploy on the MWDB droplet: `git pull`, `docker compose -f docker-compose-prod.yml up -d --build` (section 11, Updates). `configure` creates the new attribute definitions (`malleus_analysis`, `sandbox_*`) at start; check the logs as described under "Before deploying".
2. On the worker, start `malleus-static` with `sandbox_mode = never` in `/etc/karton/karton.ini`.
3. In `/opt/KadathSandbox`: `make up`, `make selftest` and the manual offering (worker README, Kadath step 4). Then run the containment check below, while no service is using Kadath.
4. `systemd-analyze verify /etc/systemd/system/malleus-*.service`.
5. Enable `malleus-detonate` (`systemctl enable --now malleus-detonate`) and check `systemctl status malleus-detonate`.
6. Only once the containment check has passed: set `sandbox_mode = auto` in `/etc/karton/karton.ini` and `systemctl restart malleus-static`.
7. Verify the pipeline (below).

To roll back, set `sandbox_mode = never` and restart `malleus-static` first, then stop `malleus-detonate`.

### Containment check before real samples

Run this once, as part of step 3: after the manual offering succeeds and before `malleus-detonate` is enabled in step 5, so that no real sample goes through Kadath first and the worker's Kadath is not in use (the worker README reserves it for the service once that runs). The probe is Malleus `tests/fixtures/kadath/probe.php`, a benign file that tries `fsockopen(MWDB_VPC_IP, 6379)`, `fsockopen(MWDB_VPC_IP, 9000)` and `file_get_contents("http://169.254.169.254/metadata/v1/id")` (the probe throws a `RuntimeException` instead of probing if `MWDB_VPC_IP` was not replaced). On the worker:

```bash
cp /opt/malleus/tests/fixtures/kadath/probe.php /tmp/probe.php
sed -i 's/MWDB_VPC_IP/<MWDB droplet VPC address>/' /tmp/probe.php
cd /opt/KadathSandbox
sudo -u malleus -H python3 bin/kadath offer --json --stub-missing --adopt-wp /tmp/probe.php
rm /tmp/probe.php
```

The `sed` also rewrites the probe's comment lines and its guard message, which is harmless.

Kadath records only the HTTP status of the trigger and `offer --json` prints only the path of `summary.json`, so the probe's own output is never shown. Read the result from that `summary.json` alone. The check has passed only if all three hold:

- `run.fatals` does not contain `PLACEHOLDER NOT REPLACED` (if it does, the substitution was skipped and nothing was tested).
- `network.dropped` lists the MWDB VPC address on port 6379 and on port 9000.
- `network.flows` has no entry for the MWDB VPC address or for `169.254.169.254`.

If `network.dropped` does not list both VPC ports, the check has NOT passed, even if no flow shows up: investigate before any real sample runs (check `GATEWAY_BLOCKED_DESTS` in Kadath's `.env` and the default in `gateway/entrypoint.sh`, which must cover the VPC range and the metadata address). Do not enable `sandbox_mode = auto` until it passes. Never commit the real address or the substituted copy.

### Verify the pipeline

1. Check that every Karton service is up here, and on the worker that `systemctl status malleus-static malleus-detonate` shows both active:

   ```bash
   docker compose -f docker-compose-prod.yml ps
   ```

2. Upload the harmless PHP test fixture `tests/fixtures/malleus/weak.php` from this checkout with the tag `source:researcher` set at upload time (the task payload carries the tags present when the file is created). It has to be PHP that reaches the LLM: Malleus answers anything that is not PHP, JS, HTML or `.htaccess` with `not-applicable` and creates no children, so step 4 cannot be checked with an arbitrary file. MWDB spawns no task for a file it already has, so on a later run append a line such as `$verify = '2026-11-10';` to a copy to get a new hash. Either:

   - Through the API, which accepts tags in the upload options (the web upload form does not). With `mwdblib`:

     ```python
     from mwdblib import MWDB
     mwdb = MWDB(api_url="https://YOUR_DOMAIN/api/", api_key="YOUR_API_KEY")
     content = open("tests/fixtures/malleus/weak.php", "rb").read()
     mwdb.upload_file("weak.php", content, tags=["source:researcher"])
     ```

     or the equivalent `curl` (`POST /api/file`, multipart with the JSON `options` field):

     ```bash
     curl -sf -X POST https://YOUR_DOMAIN/api/file \
       -H "Authorization: Bearer YOUR_API_KEY" \
       -F "file=@tests/fixtures/malleus/weak.php" \
       -F 'options={"tags":[{"tag":"source:researcher"}]}'
     ```

   - Or through the web UI: upload the file, add the tag `source:researcher` on the sample page, then press Reanalyze on the sample's Karton panel. A tag added after upload does nothing until Reanalyze resends the task with the current tags; the button needs the `karton_reanalyze` capability.

3. Watch the reporter:

   ```bash
   docker compose -f docker-compose-prod.yml logs -f karton-mwdb-reporter
   ```

4. After a minute or two, the sample should carry a `verdict:*` tag and the Malleus attributes, and it must have children (Relations tab): at least one `llm-read` blob named `<sha256 prefix>-llm.json` (the LLM report) and the `deobfuscated-php` blobs, one per peeled layer (`<sha256 prefix>-c.php`, `-co.php`, ...). The reporter log must show no `403` / `Forbidden`. A sample with its verdict but without children means the `karton` account lacks `karton_assign` (step 2 of "The `karton` reporter account"). If nothing arrives at all, check the worker's logs and that the worker can reach `VPC_BIND_IP:6379` and `:9000`.

5. Run the smoke script against prod from a checkout of this repository on a machine with `mwdblib` installed, using a researcher account's API key (create it under Settings → Users → the user → Manage API keys). The account needs `adding_files`, `adding_tags`, `reading_all_attributes` and `karton_reanalyze`; a scanner login does not work, because its `source:researcher` tag is stripped. The script uploads `weak.php` tagged `source:researcher` (full depth), so the sample lands in prod; `--salt` gives it a hash no earlier run used:

   ```bash
   export MWDB_URL=https://YOUR_DOMAIN/api
   export MWDB_API_KEY=YOUR_RESEARCHER_API_KEY
   python3 dev/malleus-smoke.py --expect-sandbox --reanalyze --timeout 1200 --salt "$(date +%F)"
   ```

   `--expect-sandbox` waits for the detonation: a `*-kadath-summary.json` child of type `sandbox-report`, a `sandbox_level` attribute and a sandbox stage with status `ok` in `malleus_stages`. `--reanalyze` resends the sample and checks that exactly one verdict tag and one value of each single-valued `malleus_*` attribute remain. It only passes when the prod reporter's login is listed in `MWDB_MALLEUS_REPORTER_LOGINS` (`karton` by default, so leave the reporter logging in with its `karton` API key); under any other login the cleanup does not run and old and new values pile up. If the account cannot see the reporter's children, add `--skip-children`.

6. A scanner upload: send a PHP file with `context` and the `source:<scanner>` tag in the upload options, as a scanner account would (auto depth). Which fixture gives which model read is not fixed, so judge each upload by its outcome: a weak hit that the static stages did not settle, where the model read is green or amber, detonates (the object gets a `sandbox-report` child and `sandbox_*` attributes); one where the model read is red does not (its verdict is settled and there is no `sandbox-report`). Full depth (`source:researcher`) always detonates, which is what step 5 covers.

### Recovery

- `malleus-detonate` (or `malleus-static`) is not running or keeps restarting: both units run a preflight (`malleus-preflight`) before starting and systemd retries a failing one with a growing delay (30 s up to 600 s). There is no start limit (`StartLimitIntervalSec=0`), so the unit is never left permanently failed and recovers on its own once the cause is fixed. Read `journalctl -u malleus-detonate` for the preflight's problem lines (a missing binary or checkout, Docker or Compose not usable, Kadath not set up) and fix the cause. `systemctl restart malleus-detonate` retries immediately instead of waiting out the delay.
- An object has no verdict after a hard kill (SIGKILL or the out-of-memory killer, which the consumers' crash handler cannot catch): open the sample in MWDB and press Reanalyze on the Karton panel (needs `karton_reanalyze`). The reanalysis replaces whatever the interrupted run left behind.
- Do not restart a crashed Malleus task from a Karton dashboard to recover it: that re-posts the same `malleus_analysis` value, so the reanalysis cleanup does not fire and the old run's attributes and tags stay. Use Reanalyze on the sample page instead (production deploys no dashboard).
- A sample ended `undetermined` with a reason starting `sandbox: error` (Kadath busy or timed out) or `analysis crashed:`: the same, press Reanalyze after fixing the cause in the worker's journal.

## 10. PostgreSQL backups

### Automated daily backup

Create a backup script:

```bash
cat > /home/mwdb/backup-db.sh << 'SCRIPT'
#!/bin/bash
BACKUP_DIR=/home/mwdb/backups
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

docker compose -f /home/mwdb/mwdb-core-wafflemakers/docker-compose-prod.yml \
  exec -T postgres pg_dump -U mwdb -d mwdb --format=custom \
  > "$BACKUP_DIR/mwdb_$TIMESTAMP.dump"

# Keep only the last 7 daily backups
ls -t "$BACKUP_DIR"/mwdb_*.dump | tail -n +8 | xargs -r rm
SCRIPT
chmod +x /home/mwdb/backup-db.sh
```

Add to crontab (runs daily at 3 AM):

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * /home/mwdb/backup-db.sh") | crontab -
```

### Manual backup

```bash
docker compose -f docker-compose-prod.yml exec -T postgres \
  pg_dump -U mwdb -d mwdb --format=custom > mwdb_backup.dump
```

### Restore from backup

```bash
docker compose -f docker-compose-prod.yml exec -T postgres \
  pg_restore -U mwdb -d mwdb --clean --if-exists < mwdb_backup.dump
```

### Restoring onto a fresh server (cross-host migration)

The postgres image's `POSTGRES_PASSWORD` only takes effect on first init of an
empty data directory. If you populate the volume from another host's dump, the
restored `mwdb` role keeps its **old** password hash, and the new `gen_vars.sh`
password in `mwdb-vars.env` won't authenticate — `start.sh` will loop on
`Waiting for postgres` forever.

After the restore, sync the role's password to the value in `mwdb-vars.env`
(local socket connections in the postgres image are `trust`, so no password is
needed for this step):

```bash
NEW_PW=$(grep '^MWDB_POSTGRES_URI' mwdb-vars.env | sed 's|.*://mwdb:\([^@]*\)@.*|\1|')
docker compose -f docker-compose-prod.yml exec postgres \
  psql -U mwdb -d mwdb -c "ALTER USER mwdb WITH PASSWORD '$NEW_PW';"
docker compose -f docker-compose-prod.yml restart mwdb
```

The matching `mwdb-uploads` volume should also be transferred — see the
"Uploads" note under Security.

## 11. Maintenance

### Updates

```bash
cd /home/mwdb/mwdb-core-wafflemakers
git pull
docker compose -f docker-compose-prod.yml pull   # Karton, silo and Redis images
docker compose -f docker-compose-prod.yml up -d --build
```

Changes to `karton.ini` or `.env` need the affected services recreated (`up -d` does this for `.env`; `restart` the Karton services after editing `karton.ini`). Update the worker droplet separately (see its README).

### Recomputing file types

`file_type` is computed once, at upload, by libmagic plus the PHP refinement
in `mwdb/core/filetype.py` (a PHP open tag anywhere in the file makes it
`PHP script, ...`). Stored values do not change on their own, so re-run this
after deploying an image with a newer libmagic, or after changing that
refinement:

```bash
docker compose -f docker-compose-prod.yml run --rm -e MWDB_ENABLE_HOOKS=0 mwdb mwdb-core recompute-file-types
```

Add `--dry-run` first to list what would change without writing anything, and
`--limit N` to examine only the first N files. It reads every stored file, so
expect it to take a while on a large instance; it is safe to re-run.

### View logs

```bash
docker compose -f docker-compose-prod.yml logs -f mwdb        # backend
docker compose -f docker-compose-prod.yml logs -f mwdb-web     # nginx
docker compose -f docker-compose-prod.yml logs -f traefik      # TLS proxy
docker compose -f docker-compose-prod.yml logs -f postgres     # database
docker compose -f docker-compose-prod.yml logs -f karton-mwdb-reporter  # Karton results into MWDB
```

### Restart a single service

```bash
docker compose -f docker-compose-prod.yml restart mwdb
```

### Check disk usage

```bash
docker system df                                    # Docker disk usage
du -sh /var/lib/docker/volumes/                     # Volume sizes
docker compose -f docker-compose-prod.yml exec postgres \
  psql -U mwdb -d mwdb -c "SELECT pg_size_pretty(pg_database_size('mwdb'));"
```

## Threat library sync rollout

1. Create a deploy key with write access to `Automattic/jetpack-threat-library`
   on github.a8c.com and pin the host key. The sidecar runs as `nobody`
   (uid 65534), so the key must be owned by that uid:

   ```bash
   mkdir -p ./secrets
   # place the private key at ./secrets/threatlib_deploy_key, then:
   sudo chown 65534:65534 ./secrets/threatlib_deploy_key
   sudo chmod 600 ./secrets/threatlib_deploy_key
   ssh-keyscan github.a8c.com > ./secrets/threatlib_known_hosts
   ```

   Both files stay out of git. With `MWDB_THREATLIB_KNOWN_HOSTS` set (the
   prod compose does) ssh refuses any host key not in that file.
2. `docker compose -f docker-compose-prod.yml up -d --build threatlib-sync`
   with `MWDB_THREATLIB_PUSH=0` (the default). The sync acts as the admin
   user (`MWDB_THREATLIB_USER` overrides it; the container exits with code 2
   if the user does not exist). The first run ingests every repo path under
   the four owned dirs: files already in MWDB from the April import are
   de-duplicated by sha256 and re-linked under their exact repo paths, new
   files are uploaded, and the export is written into the `threatlib-repo`
   volume without pushing. It does not read the April import's comments; the
   READMEs come from the repo's `README.md` files.

   **Sharing:** every ingested sample, including April-imported ones
   re-matched by sha256, is shared with `MWDB_THREATLIB_SHARE_WITH`
   (default `public`, i.e. every logged-in researcher). Set it to an empty
   string to share with nobody (admin-only) before the first run if that is
   not wanted.
3. Review the diff. With `PUSH=0` the sync commits locally, so compare the
   local commit with the remote:

   ```bash
   docker compose -f docker-compose-prod.yml exec threatlib-sync \
     git -C /data/repo diff --stat origin/trunk HEAD
   ```

   Expected: only `.mwdb-threatlib.json` added. Investigate anything else
   before continuing. Also check the logs for `preserved` warnings: those
   paths could not be ingested and are kept in the repo as they are.

   While `PUSH=0` the remote has no manifest, so every run is a first run:
   anything unlinked in MWDB in the meantime is re-imported from the repo on
   the next run. Keep this window short.
4. `MWDB_THREATLIB_PUSH=1 docker compose -f docker-compose-prod.yml up -d threatlib-sync`.
   The first pushed commit adds only the manifest.
5. Announce that MWDB is canonical for `threats/`, `for-later-review/`,
   `webshells/`, `escalated_issues_samples/`. Hand-made PRs still merge safely
   (ingest runs first); after a quiet period mark those dirs as generated in
   the repo README.

## Security notes

- **Firewall:** Only ports 80 and 443 should be open to the public. PostgreSQL (5432) and gunicorn (8080) are internal-only (no `ports:` exposed to host in the compose file). Redis (6379) and S3/silo (9000) are published on the VPC address only and must be reachable from the worker droplet alone (section 9). Redis requires a password; `karton-dashboard` is not deployed because it has no authentication.
- **Admin password:** Change after first login via the SPA (Profile → Change Password). The gen_vars.sh password is for initial setup only.
- **TLS:** Managed automatically by Traefik + Let's Encrypt. Certificates auto-renew. HTTP → HTTPS redirect is configured.
- **Uploads:** Stored on a Docker volume (not exposed to host filesystem). Back up the `mwdb-uploads` volume alongside the database if you need full disaster recovery.
- **Sandbox isolation:** The PHP sandbox runs malware in a Docker container. Only start it when needed, stop when done. It has no internet access from the Docker internal network by default, but the PHP code is executed — run it on disposable infrastructure if possible.
