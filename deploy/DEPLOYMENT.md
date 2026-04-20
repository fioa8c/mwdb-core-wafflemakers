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

Create a `.env` file for the compose variables (Traefik domain + ACME email):

```bash
cat > .env << EOF
MWDB_DOMAIN=YOUR_DOMAIN
ACME_EMAIL=YOUR_EMAIL
EOF
```

Replace `YOUR_DOMAIN` with your actual domain (e.g., `mwdb.wafflemakers.xyz`) and `YOUR_EMAIL` with the email for Let's Encrypt notifications.

## 4. DNS setup

Point your domain to the droplet's IP address:

- **A record:** `YOUR_DOMAIN` → `YOUR_DROPLET_IP`

Wait for DNS propagation (check with `dig YOUR_DOMAIN`). Traefik will fail to get a TLS certificate if DNS isn't pointing to the droplet yet.

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

## 9. PostgreSQL backups

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

## 10. Maintenance

### Updates

```bash
cd /home/mwdb/mwdb-core-wafflemakers
git pull
docker compose -f docker-compose-prod.yml up -d --build
```

### View logs

```bash
docker compose -f docker-compose-prod.yml logs -f mwdb        # backend
docker compose -f docker-compose-prod.yml logs -f mwdb-web     # nginx
docker compose -f docker-compose-prod.yml logs -f traefik      # TLS proxy
docker compose -f docker-compose-prod.yml logs -f postgres     # database
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

## Security notes

- **Firewall:** Only ports 80 and 443 should be open. PostgreSQL (5432), Redis (6379), and gunicorn (8080) are internal-only (no `ports:` exposed to host in the compose file).
- **Admin password:** Change after first login via the SPA (Profile → Change Password). The gen_vars.sh password is for initial setup only.
- **TLS:** Managed automatically by Traefik + Let's Encrypt. Certificates auto-renew. HTTP → HTTPS redirect is configured.
- **Uploads:** Stored on a Docker volume (not exposed to host filesystem). Back up the `mwdb-uploads` volume alongside the database if you need full disaster recovery.
- **Sandbox isolation:** The PHP sandbox runs malware in a Docker container. Only start it when needed, stop when done. It has no internet access from the Docker internal network by default, but the PHP code is executed — run it on disposable infrastructure if possible.
