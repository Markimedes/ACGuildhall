# Deploying Guildhall in Docker (behind nginx on the 'edge' network)

Guildhall runs as a container on your existing **`edge`** network, and the nginx
already running on that network reverse-proxies to it by name (`guildhall:5000`).
MySQL stays on the host; the container reaches it via `host.docker.internal`.

> I can't run Docker (it needs sudo here), so the `sudo` commands below are for you
> to run.

## 1. Host prerequisite: a DB user the container can use

MySQL already listens on all interfaces (`bind_address = *`), so nothing to change
there. But the existing `guildhall` account is `@localhost`; container connections
arrive from the `edge` subnet **172.19.0.0/16**, so add an account for that range
(same password as your `.env`):

```bash
sudo mysql <<'SQL'
CREATE USER IF NOT EXISTS 'guildhall'@'172.19.%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';
GRANT SELECT (id, username, salt, verifier) ON acore_auth.account TO 'guildhall'@'172.19.%';
GRANT UPDATE (salt, verifier)               ON acore_auth.account TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.characters       TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_skills  TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_spell   TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.guild             TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.guild_member      TO 'guildhall'@'172.19.%';
-- The app's own tables live in their own database (forum, invites, demand cache,
-- news desk -- see schema.sql). One grant covers them all:
GRANT SELECT, INSERT, UPDATE, DELETE ON guildhall.* TO 'guildhall'@'172.19.%';
-- Auction House tab (held items + soulbound detection):
GRANT SELECT (guid, owner_guid, itemEntry, count, flags, randomPropertyId) ON acore_characters.item_instance TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_characters.character_inventory TO 'guildhall'@'172.19.%';
-- Auction House tab (live pricing reads world game data):
GRANT SELECT ON acore_world.item_template TO 'guildhall'@'172.19.%';
GRANT SELECT ON acore_world.npc_vendor    TO 'guildhall'@'172.19.%';
FLUSH PRIVILEGES;
SQL
```

(If the `edge` subnet ever changes, confirm it with
`sudo docker network inspect edge -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}'`
and adjust the `172.19.%` host accordingly.)

## 2. Create the .env file

```bash
cd guildhall
cp .env.example .env
python3 -c "import secrets; print('GUILDHALL_SECRET_KEY='+secrets.token_hex(32))"
# edit .env: paste the secret key, set GUILDHALL_DB_PASSWORD to the guildhall user's password
```

`.env` is gitignored. All config comes from these vars (TLS/proxy flags are already
set to true for running behind nginx).

## 3. Build and start the container

```bash
cd guildhall
sudo docker compose up -d --build
sudo docker logs -f guildhall      # watch startup; Ctrl-C to stop following
```

This builds the image, joins `edge`, and maps `host.docker.internal`
to the host gateway. No host port is published — traffic enters only through nginx.

`docker compose up` also starts the **`guildhall-news`** sidecar: a single-process
scheduler (same image, runs `news_scheduler.py`) that pre-generates the AI news
edition once a day, just after the AHPricingService rolls the daily market event.
It warms today's edition on startup, then runs at `GUILDHALL_NEWS_HOUR:MINUTE`
(default 00:05, container-local). Set `TZ` in `.env` to match the AHPricingService
so both agree on midnight. Watch it with `sudo docker logs -f guildhall-news`. If
it's ever down, the web app still falls back to generating on first page view.

Sanity-check DB connectivity from inside the container:

```bash
sudo docker exec guildhall python -c "import db; from app import load_config; db.init_pool(load_config()['database']); print('db ok:', db.get_account_by_id(1) is not None)"
```

## 4. Point nginx at it

A sample server block is in [deploy/nginx.guildhall.conf](deploy/nginx.guildhall.conf).
Put it where your nginx includes configs (often a mounted `conf.d/`),
set `server_name` and the TLS cert paths, then reload nginx:

```bash
sudo docker exec <nginx-container> nginx -t      # test config
sudo docker exec <nginx-container> nginx -s reload
```

Because guildhall is on the same network, `proxy_pass http://guildhall:5000` resolves
by container name. nginx terminates TLS and forwards `X-Forwarded-Proto`/`-For`, which
the app trusts (ProxyFix) for Secure cookies and correct client IPs in rate limiting.

## 4b. Shared downloads (optional)

The `/downloads` page lists files from a directory and lets logged-in players
download them. Large files (e.g. a multi-GB client zip) are streamed by **nginx**
via `X-Accel-Redirect` — Flask only checks the login, so a download never ties up
a Python worker and clients get Range/resume support.

Two mounts of the **same** host directory are needed:

1. **guildhall container** (to *list* files) — already added in
   `docker-compose.yml`:

   ```yaml
   volumes:
     - /media/plex/downloads:/media/plex/downloads:ro
   ```

2. **nginx container** (to *serve* the bytes) — add the same read-only mount to
   the nginx service, and the `internal` location is already in
   [deploy/nginx.guildhall.conf](deploy/nginx.guildhall.conf):

   ```nginx
   location /protected-downloads/ {
       internal;
       alias /media/plex/downloads/;
   }
   ```

Then set in `.env`:

```ini
GUILDHALL_DOWNLOADS_DIR=/media/plex/downloads
GUILDHALL_DOWNLOADS_INTERNAL_PREFIX=/protected-downloads
```

Drop files into the host directory and they appear on the page automatically
(top-level regular files only; dotfiles are skipped). If you leave
`GUILDHALL_DOWNLOADS_INTERNAL_PREFIX` empty, Flask streams the files itself —
fine for the dev server or small files, not for multi-GB ones.

## 5. Updating later

```bash
cd guildhall
git pull                      # or otherwise update files
sudo docker compose up -d --build
```

Stop with `sudo docker compose down`. (You can also stop the old native dev server
on port 5000 once you've cut over — the container doesn't use that port.)

## 6. Auction House tab (priced by the AHPricingService)

The Auction House tab gets **every price from the `AHPricingService`** — the same
service the worldserver uses. The panel does no item-value math of its own: for each
held item it sends the entry id + rolled random property to the service and shows
what comes back. So retuning `.ahbot` (and reloading the service) is reflected with
no guildhall change.

- **Service**: reached by container name on the shared `edge` network —
  `GUILDHALL_AHPRICING_URL` (in `.env`) defaults to
  `http://ahpricingservice:8089/price`. Ensure that service is `up` on `edge`; if
  it's down, items just show as "Not priced" and the page still renders.
- **Grants** (step 1): the `guildhall` user needs `SELECT` on
  `acore_world.item_template` for **display only** (name/icon/quality/item level of
  held items). It no longer needs `npc_vendor` or the `mod_ahbot.conf` mount.
- **Soulbound** items (per-instance `item_instance.flags`) are still marked
  un-auctionable and excluded from the total.

The only baked file is `item_icons.json` (item icons live in the client DBCs, not
the DB, and never change). Re-run `python3 build_item_icons.py` only if the client
DBCs ever change.

## 7. Selling from the web (SOAP + mod-guildhall)

Players can list held items on the AH straight from the Auction House tab. The panel
does **not** write the game DB directly; it asks the worldserver to create the
auction through the real code path, over SOAP. This avoids auction-id collisions and
needs no `.reload auctions`.

Pieces:

1. **Build the module.** `modules/mod-guildhall` adds the console command
   `guildhall list ...`. Re-run CMake (so the new module is picked up) and rebuild
   the worldserver, e.g. from `build/`:
   `cmake .. && make -j2 && make install` (keep `-j` low — full parallelism has
   frozen this box). Restart the worldserver.
2. **Enable SOAP** in `worldserver.conf` (already set in `env/dist/etc`):
   `SOAP.Enabled = 1`, `SOAP.IP = "0.0.0.0"` so the container can reach it via the
   Docker host-gateway, and a **free** `SOAP.Port`. NB: AzerothCore's default 7878 is
   Radarr's port and is taken on this host, so we use **7880** — a busy SOAP port
   fails to bind and *aborts the worldserver* on startup. **Firewall the SOAP port off
   the public internet** — anyone reaching it with an admin account can run any
   server command.
3. **Create a dedicated admin account** for the panel and give it gmlevel 3:
   ```
   # at the worldserver console:
   account create GUILDHALL_SOAP <strong-password>
   account set gmlevel GUILDHALL_SOAP 3 -1
   ```
4. **Point the panel at SOAP** in `.env` (see `.env.example`):
   `GUILDHALL_SOAP_URL=http://host.docker.internal:7880/` (match `SOAP.Port`),
   `GUILDHALL_SOAP_USER=GUILDHALL_SOAP`, `GUILDHALL_SOAP_PASS=<that password>`.
   Leave `GUILDHALL_SOAP_URL` empty to hide the sell feature entirely.

Behaviour:

- Listing works whether the character is **online or offline** (the tab shows a
  badge either way). Online, the item leaves their bags immediately via the live
  `Player`; offline, the worldserver edits the DB directly.
- The worldserver charges the exact deposit. `GUILDHALL_AH_DEPOSIT_PERCENT` (5 for the
  faction AHs) and `GUILDHALL_AH_DEPOSIT_RATE` only drive the on-page **estimate** and
  should match `AuctionHouse.dbc` / `Rate.Auction.Deposit`.
- No extra DB grants are needed: the worldserver performs all writes; the panel only
  reads `characters` (online/money/race), `character_inventory`, `item_instance` and
  `item_template`, which the `guildhall` user can already read.
