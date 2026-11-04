# 7. Setup Guide

How to rebuild the whole app from nothing: a new server, a new cluster, the newest backup restored,
the site answering on its address. This page is the ordered "how". The reasons behind each choice
are in architecture [3.25](03-architecture.md#325-publishing-the-images),
[3.26](03-architecture.md#326-running-on-kubernetes),
[3.27](03-architecture.md#327-running-on-the-server) and
[3.28](03-architecture.md#328-backups-and-alerts).

There are two paths. **Path A** is the one in use: k3s on an Oracle machine, kept up to date by
Argo CD. **Path B** is the fallback when Oracle is gone: Docker Compose on any small host. Both use
the same outside accounts and the same shared steps.

A step marked **(not tried)** has not been run on a rebuilt server yet. A step marked
**(rehearsed)** has not either, but it was run on the Mac against a throwaway copy
([7.7](#77-rehearsal-record)). Every other step is how the current server was built (2026-11-02
and 03).

---

## 7.1 Before you start

This guide assumes three things still exist. If one is lost, see [7.6](#76-if-a-key-is-lost).

| What | Where |
|---|---|
| The values of `app-secrets` and `backup-secrets` | The password manager, one entry per key name in `deploy/k8s/overlays/prod/app-secrets.example.yaml` and `backup-secrets.example.yaml` |
| The backup gpg key pair and its passphrase | The password manager, and `~/denial-appeal-keys/backup-private-key.asc` + `backup-public-key.asc` on the Mac |
| The Sealed Secrets key and certificate | The password manager, and `~/denial-appeal-keys/sealed-secrets-key.yaml` + `sealed-secrets-cert.pem` on the Mac |
| The repository and its images | GitHub, and GHCR (`ghcr.io/gyanendra0/denial-appeal-agent/api` and `web`) |
| The model files | `models/win_model.joblib` and `models/win_model.json` on the Mac (not in git) |

Never paste a key, a secret value or a `.env` value into a chat, an issue or a commit. This guide
names files and entries only.

**Tools on the Mac:** `ssh`, `docker` (Docker Desktop), `gh`, and `kubeseal` 0.40.0 (only to seal
a changed secret). The Mac needs no `kubectl`: cluster commands run on the server as
`sudo k3s kubectl`, since k3s keeps its login file readable by root only. It needs no `gpg` either:
the restore runs `gpg` inside the database image.

**Pinned versions,** and the file each one is kept in:

| What | Version | Kept in |
|---|---|---|
| k3s | v1.36.5+k3s1 | `.github/workflows/security.yml` (`KUBERNETES_VERSION`), checked by `manifests` |
| Argo CD | v3.5.6 | `deploy/k8s/addons/argocd/kustomization.yaml` |
| Sealed Secrets | 0.40.0 | `deploy/k8s/addons/sealed-secrets/kustomization.yaml` |
| Postgres + pgvector | `pgvector/pgvector:pg16` by digest | `deploy/k8s/base/db.yaml` (equal to `deploy/prod/compose.yml`) |
| The app's images | By digest | `deploy/k8s/overlays/prod/kustomization.yaml`, written by the deploy pull request |

---

## 7.2 Outside accounts

Each account is set up once. If it still exists, skip to the "If it already exists" line.

### Oracle (the machine and the backup bucket)

1. Sign up for an Oracle Cloud Free Tier account, home region India West (Mumbai). Stay on Always
   Free; move to Pay As You Go only if A1 keeps answering "out of capacity", and then add a small
   budget alert at once.
2. **Machine:** Compute → Instances → Create. Image Canonical Ubuntu 24.04 (aarch64), shape
   `VM.Standard.A1.Flex` with 2 OCPU and 12 GB, boot volume 100 GB, a public IPv4 address, and the
   Mac's SSH public key. Retry on "out of capacity".
3. **Firewall:** in the subnet's Security List, ingress from `0.0.0.0/0` on TCP 22, TCP 80,
   TCP 443 and UDP 443. Nothing else: 6443 (the Kubernetes API) stays closed from outside.
4. **Bucket:** Storage → Buckets, in the **root** compartment (the one the S3-compatible API sees):
   `denial-appeal-backups`, Standard tier, private, Oracle-managed encryption.
5. **Lifecycle:** an IAM policy in the root compartment,
   `Allow service objectstorage-ap-mumbai-1 to manage object-family in tenancy`, then two rules on
   the bucket: delete objects with prefix `app/` after 30 days; delete unfinished multipart uploads
   after 1 day.
6. **The server's bucket login:** an IAM group `backup-writers` with a policy that allows only
   `OBJECT_CREATE` and `OBJECT_INSPECT` on this bucket, for example (one line; this exact text was
   not copied from the console):
   ```
   Allow group backup-writers to manage objects in tenancy where all {target.bucket.name='denial-appeal-backups', any {request.permission='OBJECT_CREATE', request.permission='OBJECT_INSPECT'}}
   ```
   A user Backup Writer in that group, with only
   the "Customer secret keys" capability. Make one Customer Secret Key for it; the secret is shown
   once, so put both halves in the password manager under the `backup-secrets` key names.
7. The S3 endpoint is `https://<namespace>.compat.objectstorage.ap-mumbai-1.oci.customer-oci.com`;
   the namespace is on the bucket's page. If it differs from the one in
   `deploy/k8s/overlays/prod/backup-config.yaml`, change that file in a pull request.

**Success looks like:** the machine shows Running with a public address, and `ssh ubuntu@<address>`
logs in from the Mac. The bucket shows its two lifecycle rules.

**If it already exists:** only steps 2 and 3 are needed for a new machine. A new Customer Secret
Key means sealing `backup-secrets` again ([7.6](#76-if-a-key-is-lost)).

### DuckDNS (the name)

1. Log in at duckdns.org (the account behind `denial-appeal`).
2. Set the IP of `denial-appeal` to the machine's public address.

**Success looks like:** `dig +short denial-appeal.duckdns.org` on the Mac prints the new address.

### Google sign-in

1. Google Cloud console → APIs & Services → Credentials → the OAuth client whose ID is
   `GOOGLE_CLIENT_ID` in `deploy/k8s/overlays/prod/config.yaml`.
2. Its authorised redirect URI must be `APP_PUBLIC_URL` + `/auth/google/callback`:
   `https://denial-appeal.duckdns.org/auth/google/callback`.
3. If the client is new, put its ID in `config.yaml` (pull request) and its secret in the password
   manager as `GOOGLE_CLIENT_SECRET`.

**Success looks like:** after the site is up, "Sign in with Google" returns to the app logged in.

### Brevo (email)

1. Log in at brevo.com → SMTP & API. The SMTP login and key are `MAIL_SMTP_USERNAME` and
   `MAIL_SMTP_PASSWORD` in the password manager; the sender verified at Brevo is `MAIL_FROM`.
2. Security → Authorised IPs: add the machine's public address. A new machine has a new address,
   and without it every email fails with `SMTPAuthenticationError (code 525)` in the API log.

**Success looks like:** after the site is up, a sign-up or password reset code arrives.

### GHCR (the images)

Nothing to do on a rebuild. The images are public, so the server pulls them with no login. Each
merge to `main` publishes new ones and the bot's deploy pull request writes their digests into
`deploy/k8s/overlays/prod/kustomization.yaml`
([3.25](03-architecture.md#325-publishing-the-images)).

**Success looks like:** `docker pull ghcr.io/gyanendra0/denial-appeal-agent/api@<digest from that
file>` works on the Mac with no login.

### UptimeRobot (the down alert)

Set up last, once the site answers: [7.3.4](#734-the-uptime-monitor).

---

## 7.3 Shared steps

Both paths use these. Each is written once here.

### 7.3.1 Restore the newest backup on the Mac

The private key never goes to the server. So the dump is decrypted on the Mac into a throwaway
Postgres, and from there copied into the new server ([7.4.8](#748-load-the-data-rehearsed)).
This part was done on 2026-11-03 against production; the CI job `backup-restore` in
`.github/workflows/checks.yml` runs the same `restore.sh` on every pull request.

1. Oracle console → Buckets → `denial-appeal-backups` → `app/` → download the newest
   `app-<date>.dump.gpg` to `~/Downloads`.
2. Start a throwaway Postgres from the pinned image, on its own Docker network. The password is
   made up and only lives in this container:
   ```
   DB_IMAGE=pgvector/pgvector:pg16@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a
   docker network create restore
   docker run --detach --name restore-db --network restore \
     -e POSTGRES_USER=app -e POSTGRES_PASSWORD=throwaway -e POSTGRES_DB=app "$DB_IMAGE"
   until docker exec restore-db pg_isready -h 127.0.0.1 -U app -d app; do sleep 1; done
   ```
3. Run `deploy/k8s/base/backup/restore.sh` in the same image, from the repository's top folder.
   Keep `-it`: gpg asks for the key's passphrase on the terminal.
   ```
   docker run -it --rm --network restore \
     -e PGHOST=restore-db -e PGUSER=app -e PGPASSWORD=throwaway \
     -v "$PWD/deploy/k8s/base/backup:/scripts:ro" \
     -v "$HOME/Downloads:/backup:ro" -v "$HOME/denial-appeal-keys:/keys:ro" \
     "$DB_IMAGE" bash /scripts/restore.sh /backup/app-<date>.dump.gpg \
     /keys/backup-private-key.asc restored
   ```
4. Count the rows:
   ```
   docker exec -i restore-db psql -U app -d restored -v ON_ERROR_STOP=1 \
     --no-psqlrc --tuples-only --no-align < deploy/k8s/base/backup/row-counts.sql
   ```

**Success looks like:** `restored app-<date>.dump.gpg into restored`, then one line per table (30
tables in November 2026), with `evidence_documents` 952 and `evidence_chunks` 2,873.

When the data is in the new server, remove the decrypted copy:
`docker rm -f restore-db && docker network rm restore`, and delete the downloaded dump.

### 7.3.2 Point the name at the server

[DuckDNS](#duckdns-the-name), then Brevo's authorised address ([Brevo](#brevo-email)). Do this
before the web server first starts, or Caddy's first certificate request fails and retries later
by itself.

### 7.3.3 Check the site

```
curl -sS https://denial-appeal.duckdns.org/health
```

**Success looks like:** `{"status":"ok"}`, over a Let's Encrypt certificate (no `-k` needed).
`/health` opens a new database connection, so this one check covers Caddy, the API and the
database. Then sign in, in a browser, as an admin from `ADMIN_EMAILS`, and open the worklist: the
restored uploads are there.

### 7.3.4 The uptime monitor

UptimeRobot (free) → New monitor: HTTP(S), URL `https://denial-appeal.duckdns.org/health`, every 5
minutes, email alert to the owner. A monitor that still exists needs no change, since the address
is a name.

**Success looks like:** the monitor shows Up.

---

## 7.4 Path A: Oracle k3s

The order is fixed: each step needs the one before it.

### 7.4.1 The machine

On the machine (`ssh ubuntu@<address>`):

1. Oracle's Ubuntu image ships two iptables `REJECT` lines that break pod traffic. Open
   `/etc/iptables/rules.v4` with `sudo nano`, delete only the two lines
   `-A INPUT -j REJECT --reject-with icmp-host-prohibited` and
   `-A FORWARD -j REJECT --reject-with icmp-host-prohibited`, keep the `InstanceServices` lines,
   save and `sudo reboot`.
2. Check that password login is off: `sudo sshd -T | grep passwordauthentication` prints `no`.

**Success looks like:** after the reboot, `sudo iptables -S | grep REJECT` prints nothing (or only
`InstanceServices` lines).

### 7.4.2 k3s without Traefik

```
curl -sfL https://get.k3s.io | \
  INSTALL_K3S_VERSION=v1.36.5+k3s1 INSTALL_K3S_EXEC="--disable traefik" sh -
```

Caddy (`web`) is the only front door, so k3s's own Traefik is left out.

**Success looks like:** `sudo k3s kubectl get nodes` shows the node `Ready` on `v1.36.5+k3s1`.

### 7.4.3 Put back the Sealed Secrets key (rehearsed)

The sealed files in git open only with the key they were sealed for. Put that key into the cluster
**before** the controller first starts, so the controller finds it and makes no key of its own
(renewal is off, see 3.28).

1. From the Mac: `scp ~/denial-appeal-keys/sealed-secrets-key.yaml ubuntu@<address>:`
2. On the machine:
   ```
   sudo k3s kubectl apply -f sealed-secrets-key.yaml
   shred -u sealed-secrets-key.yaml
   ```

**Success looks like:**
`sudo k3s kubectl -n kube-system get secrets -l sealedsecrets.bitnami.com/sealed-secrets-key`
lists one key. If the controller was already started by mistake, apply the key anyway and restart
it (`sudo k3s kubectl -n kube-system rollout restart deployment/sealed-secrets-controller`): it
opens files sealed for any of its keys.

### 7.4.4 The add-ons

The upstream files are not in git. Download each one next to its `kustomization.yaml`, check it
against the sha256 in that file's first comment lines, then apply. On the machine, clone the
repository into the home folder first (`cd ~ && git clone https://github.com/gyanendra0/DENIAL-APPEAL-AGENT`):

```
cd ~/DENIAL-APPEAL-AGENT/deploy/k8s/addons/sealed-secrets
curl -fsSLO https://github.com/bitnami-labs/sealed-secrets/releases/download/v0.40.0/controller.yaml
echo "ac8aabccdd9110430d117501705818115b2ab0993700a9c118d4d42cf4015a17  controller.yaml" \
  | sha256sum -c
sudo k3s kubectl apply -k .

cd ~/DENIAL-APPEAL-AGENT/deploy/k8s/addons/argocd
curl -fsSLO https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml
echo "846c644bc81ce5557dd9048757a9c7353378f25c5eb811c77d32a9e907823599  install.yaml" \
  | sha256sum -c
sudo k3s kubectl create namespace argocd
sudo k3s kubectl apply --server-side -k .
```

Argo CD needs `--server-side`: its custom resources are too large for a plain apply.

**Success looks like:** both `sha256sum -c` lines print `OK`;
`sudo k3s kubectl -n kube-system get pods -l name=sealed-secrets-controller` and
`sudo k3s kubectl -n argocd get pods` show every pod `Running`. The controller's log
(`sudo k3s kubectl -n kube-system logs deployment/sealed-secrets-controller`) says
`registered private key secretname=sealed-secrets-key…` with the name of the key put back in
7.4.3, and nothing about generating a new key.

### 7.4.5 Namespace and model

Without the `win-model` ConfigMap the API pod waits, with the reason in `kubectl describe pod`.

1. From the Mac: `scp -r models ubuntu@<address>:`
2. On the machine:
   ```
   sudo k3s kubectl create namespace denial-appeal-agent
   sudo k3s kubectl -n denial-appeal-agent create configmap win-model --from-file="$HOME/models/"
   rm -r ~/models
   ```

**Success looks like:** `sudo k3s kubectl -n denial-appeal-agent get configmap win-model` shows
`DATA 2`.

### 7.4.6 Point the name

Do [7.3.2](#732-point-the-name-at-the-server) now, before the first sync starts `web`.

### 7.4.7 The Application and the first sync

```
sudo k3s kubectl apply -f ~/DENIAL-APPEAL-AGENT/deploy/k8s/argocd/application.yaml
```

Argo CD then syncs `deploy/k8s/overlays/prod` from `main`, one wave at a time
([3.26](03-architecture.md#order-of-a-sync)): namespace, settings, network rules and the two
sealed secrets; the database; the migration; then the API, web and the backup CronJob. The
migration builds an empty database at the newest revision.

**Success looks like:**
- `sudo k3s kubectl -n argocd get application denial-appeal-agent` shows `Synced Healthy`;
- `sudo k3s kubectl -n denial-appeal-agent get secrets` shows `app-secrets` with `DATA 10` and
  `backup-secrets` with `DATA 2` (if they are missing, the key of 7.4.3 is not the right one);
- `sudo k3s kubectl -n denial-appeal-agent get pods` shows `api`, `web` and `db-0` `1/1`.

### 7.4.8 Load the data (rehearsed)

With a backup, the restored copy of [7.3.1](#731-restore-the-newest-backup-on-the-mac) replaces
the empty database. The backup holds every table, the evidence tables too. From the Mac, with
`restore-db` still running:

```
docker exec restore-db pg_dump -U app -d restored --format=custom \
  | ssh ubuntu@<address> 'sudo k3s kubectl exec -i -n denial-appeal-agent db-0 -- pg_restore \
      -U app -d app --clean --if-exists --no-owner --single-transaction --exit-on-error'
```

`--clean --if-exists` drops what the migration made before loading the copy, and one transaction
means a failure leaves the empty database as it was. The data travels inside SSH. Then restart the
API so it reads the restored database fresh:
`sudo k3s kubectl -n denial-appeal-agent rollout restart deployment/api`.

Compare the counts with the ones of 7.3.1:

```
ssh ubuntu@<address> 'sudo k3s kubectl exec -i -n denial-appeal-agent db-0 -- psql -U app -d app \
  -v ON_ERROR_STOP=1 --no-psqlrc --tuples-only --no-align' < deploy/k8s/base/backup/row-counts.sql
```

**Success looks like:** every table has the same count as on the Mac. Then remove the decrypted
copy (end of 7.3.1).

**With no backup at all** (an empty start): copy only the three public evidence tables from the
development database, as the first server was built
([3.27](03-architecture.md#first-start-in-order)):

```
docker compose exec -T db pg_dump -U app -d app --data-only \
  -t evidence_documents -t evidence_chunks -t evidence_chunk_embeddings \
  | gzip | ssh ubuntu@<address> \
  'gunzip | sudo k3s kubectl exec -i -n denial-appeal-agent db-0 -- psql -v ON_ERROR_STOP=1 -1 -U app -d app'
```

and check 952 / 2,873 / 2,873 rows.

### 7.4.9 Check, then watch

1. [7.3.3](#733-check-the-site): `/health`, then a browser sign-in.
2. A backup on the new server (not tried). Run one only if no dump for today's UTC date exists in
   the bucket: a second one is refused as an overwrite.
   `sudo k3s kubectl -n denial-appeal-agent create job --from=cronjob/backup backup-now`, then
   `sudo k3s kubectl -n denial-appeal-agent get jobs` shows it `Complete`, and the bucket holds a
   new `app-<date>.dump.gpg`. Otherwise wait for the 03:00 UTC run.
3. [7.3.4](#734-the-uptime-monitor): the uptime monitor shows Up.
4. On the Mac, the closed ports stay closed: `nc -vz -w 5 <address> 6443` and `... 10250` get no
   answer.

**Success looks like:** all four. The rebuild is done; from now on a merged deploy pull request is
a deploy.

---

## 7.5 Path B: Compose fallback

For when the Oracle machine is gone: the same images, run by `deploy/prod/compose.yml` on any small
Linux host (a 1 GB machine such as Google's `e2-micro` is enough; it is too small for Kubernetes).
The whole compose file ran on the Mac on 2026-10-28
([3.23](03-architecture.md#323-running-in-containers)); on a remote host, every step of this path
is **(not tried)**.

### 7.5.1 The host (not tried)

1. Create the machine: Ubuntu 24.04, a public IPv4 address, the Mac's SSH public key. In the
   provider's firewall allow TCP 22, TCP 80, TCP 443 and UDP 443 from anywhere, nothing else.
2. On the machine, install Docker with its Compose plugin and let the login user run it:
   ```
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker "$USER"
   ```
   Log out and in again.
3. On 1 GB, add 2 GB of swap so Postgres and the API both fit:
   ```
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
   sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
4. Clone the repository into the home folder:
   `cd ~ && git clone https://github.com/gyanendra0/DENIAL-APPEAL-AGENT`

**Success looks like:** `docker compose version` prints a version without `sudo`, and `free -h`
shows the swap.

### 7.5.2 The images (not tried)

The images are pulled, not built: building the screens and the Python packages needs more memory
than a 1 GB host has. They are the exact images the cluster runs, taken by digest from
`deploy/k8s/overlays/prod/kustomization.yaml`, then named the way `compose.yml` names them. On the
machine:

```
cd ~/DENIAL-APPEAL-AGENT
PINS=deploy/k8s/overlays/prod/kustomization.yaml
API=ghcr.io/gyanendra0/denial-appeal-agent/api@$(awk '/agent\/api$/{getline; print $2}' $PINS)
WEB=ghcr.io/gyanendra0/denial-appeal-agent/web@$(awk '/agent\/web$/{getline; print $2}' $PINS)
docker pull "$API" && docker tag "$API" denial-appeal-agent-api
docker pull "$WEB" && docker tag "$WEB" denial-appeal-agent-web
```

**Success looks like:** `docker images` lists `denial-appeal-agent-api` and
`denial-appeal-agent-web`, each with the image ID of its pulled digest.

### 7.5.3 The settings file (not tried)

Compose reads `deploy/prod/.env` on the machine (never in git). Make it with `nano`, one `KEY=value`
per line, without quotes:

| Key | Value from |
|---|---|
| `POSTGRES_PASSWORD`, `AUTH_TOKEN_SECRET`, `LLM_PRIMARY_API_KEY`, `LLM_FALLBACK_API_KEY`, `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD`, `MAIL_FROM`, `GOOGLE_CLIENT_SECRET`, `ADMIN_EMAILS`, `SIGNUP_ALLOWED_EMAILS` | The password manager, the `app-secrets` entries of the same names |
| `SITE_ADDRESS`, `APP_PUBLIC_URL`, `SHARED_ACCOUNT_NAME`, `MAIL_SMTP_HOST`, `GOOGLE_CLIENT_ID`, and every key there starting with LLM | `deploy/k8s/overlays/prod/config.yaml`, copied as they are |
| `MAIL_BACKEND` | `smtp`, as in `deploy/k8s/base/config.yaml` |
| `INDIVIDUAL_SIGNUP_ENABLED` | `false`: this path has no nightly backup (7.5.9) |

Leave out `FORWARDED_ALLOW_IPS` (the prod `config.yaml` value is the cluster's pod range;
`compose.yml` sets the web container's address itself), `APP_ENV` and `DATABASE_URL` (also set by
`compose.yml`). Then `chmod 600 deploy/prod/.env`.

**Success looks like:** `docker compose -f deploy/prod/compose.yml config --quiet` prints nothing
(a missing `POSTGRES_PASSWORD` or `SITE_ADDRESS` is named in an error).

### 7.5.4 The model files (not tried)

`compose.yml` reads them from the clone's `models/` folder. From the Mac:
`scp -r models <user>@<address>:DENIAL-APPEAL-AGENT/`

**Success looks like:** `ls ~/DENIAL-APPEAL-AGENT/models` on the machine shows `win_model.joblib`
and `win_model.json`.

### 7.5.5 Point the name

Do [7.3.2](#732-point-the-name-at-the-server) now, before `web` first starts. If the host has an
IPv6 address too, authorise both at Brevo: one alone did not take effect in the Mac run.

### 7.5.6 Start (not tried)

```
cd ~/DENIAL-APPEAL-AGENT
docker compose -f deploy/prod/compose.yml up -d --no-build
docker compose -f deploy/prod/compose.yml ps -a
```

`--no-build` makes Compose use the images of 7.5.2. The migration builds an empty database at the
newest revision, then the API and web start.

**Success looks like:** `db`, `api` and `web` show `healthy`, and `migrate` shows `Exited (0)`. If
`migrate` failed, `docker compose -f deploy/prod/compose.yml logs migrate` has the reason.

### 7.5.7 Load the data (not tried)

As in [7.4.8](#748-load-the-data-rehearsed), only into the Compose database. From the Mac, with
`restore-db` of [7.3.1](#731-restore-the-newest-backup-on-the-mac) still running:

```
docker exec restore-db pg_dump -U app -d restored --format=custom \
  | ssh <user>@<address> 'cd DENIAL-APPEAL-AGENT && docker compose -f deploy/prod/compose.yml \
      exec -T db pg_restore -U app -d app --clean --if-exists --no-owner --single-transaction \
      --exit-on-error'
ssh <user>@<address> 'cd DENIAL-APPEAL-AGENT && docker compose -f deploy/prod/compose.yml restart api'
```

Then compare the counts with the ones of 7.3.1:

```
ssh <user>@<address> 'cd DENIAL-APPEAL-AGENT && docker compose -f deploy/prod/compose.yml \
  exec -T db psql -U app -d app -v ON_ERROR_STOP=1 --no-psqlrc --tuples-only --no-align' \
  < deploy/k8s/base/backup/row-counts.sql
```

**Success looks like:** every table has the same count as on the Mac. Then remove the decrypted
copy (end of 7.3.1).

**With no backup at all:** copy only the evidence tables, with the command of 7.4.8 whose last line
is `docker compose -f deploy/prod/compose.yml exec -T db psql -v ON_ERROR_STOP=1 -1 -U app -d app`
(run in `~/DENIAL-APPEAL-AGENT`) in place of the `kubectl exec` line.

### 7.5.8 Check, then watch

[7.3.3](#733-check-the-site) (`/health`, then a browser sign-in) and
[7.3.4](#734-the-uptime-monitor) (the uptime monitor shows Up).

**Success looks like:** both. The site is back, without the things in 7.5.9.

### 7.5.9 What is lost without the cluster

| Lost | Instead, on this path |
|---|---|
| Argo CD deploys each merged deploy pull request | By hand: `git pull`, 7.5.2 again, then `docker compose -f deploy/prod/compose.yml up -d --no-build` (the migration runs again, then the containers whose image changed are replaced) |
| The nightly encrypted backup to the bucket | A backup by hand, below |
| Network policies (only `web` may reach `api`, only `api`, `migrate` and `backup` may reach `db`) | Only `web` publishes ports; inside the Compose network every container can reach every other |
| Sealed Secrets (the secrets in git, encrypted) | The plain `deploy/prod/.env`, `chmod 600`, on the machine only |
| Every visitor seen with their own address by the login limit | On an IPv6 host, IPv6 visitors share one address and one login count ([3.23](03-architecture.md#known-limits-15)) |

**A backup by hand (not tried).** On the machine, in `~/DENIAL-APPEAL-AGENT`. The dump is made
inside `db` and encrypted to the committed public key in a second container, so no password is
typed and the private key stays on the Mac:

```
set -o pipefail
DB_IMAGE=pgvector/pgvector:pg16@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a
docker compose -f deploy/prod/compose.yml exec -T db pg_dump -U app -d app --format=custom --no-owner \
  | docker run -i --rm -v "$PWD/deploy/k8s/base/backup:/scripts:ro" "$DB_IMAGE" \
    bash -c 'export GNUPGHOME=$(mktemp -d) && gpg --batch --no-tty --trust-model always \
      --recipient-file /scripts/backup-public-key.asc --compress-algo none --encrypt' \
  > ~/app-$(date -u +%Y-%m-%d).dump.gpg && echo backup written
```

Without `backup written` at the end, the file is broken: delete it and run again. Copy it to the
Mac (`scp <user>@<address>:app-<date>.dump.gpg ~/Downloads/`), keep it there and in the bucket if
the bucket still exists, then delete it from the machine.

**Success looks like:** 7.3.1 restores the copied file on the Mac with every table counted.

## 7.6 If a key is lost

Each key below has two copies (password manager and `~/denial-appeal-keys`). These steps are for
when both are gone. Every step in this section is **(not tried)**.

**The Sealed Secrets key, server still running.** Export it again from the server:

```
ssh ubuntu@<address> 'sudo k3s kubectl -n kube-system get secret \
  -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml' > ~/denial-appeal-keys/sealed-secrets-key.yaml
chmod 600 ~/denial-appeal-keys/sealed-secrets-key.yaml
```

Then put the file in the password manager too.

**The Sealed Secrets key, server gone.** The committed sealed files can no longer be opened. On the
new server, skip 7.4.3: the controller makes a new key when it first starts. Then:

1. Fetch its certificate over SSH (6443 stays closed):
   ```
   ssh ubuntu@<address> 'sudo k3s kubectl -n kube-system get secret \
     -l sealedsecrets.bitnami.com/sealed-secrets-key -o jsonpath="{.items[0].data.tls\.crt}"' \
     | base64 -d > ~/denial-appeal-keys/sealed-secrets-cert.pem
   ```
2. For each of `app-secrets.example.yaml` and `backup-secrets.example.yaml` in
   `deploy/k8s/overlays/prod/`: copy it outside the repository, fill it from the password manager,
   seal it with
   `kubeseal --cert ~/denial-appeal-keys/sealed-secrets-cert.pem --format yaml < filled.yaml`
   into the matching `.sealed.yaml`, put back the `argocd.argoproj.io/sync-wave: "-2"`
   annotation, and delete the filled copy.
3. Merge the two sealed files in a pull request; Argo CD then opens them.
4. Export the new key as above, into both places.

**A value inside a secret.** With the Sealed Secrets key, every value can be read back on the Mac
without the cluster (`kubeseal --recovery-unseal --recovery-private-key
~/denial-appeal-keys/sealed-secrets-key.yaml < deploy/k8s/overlays/prod/app-secrets.sealed.yaml`;
it prints the values, so never in a shared terminal). If the value is gone everywhere, make a new
one: a new API key from the LLM provider, Brevo or Google; `openssl rand -hex 32` for
`AUTH_TOKEN_SECRET` (everyone is logged out). `POSTGRES_PASSWORD` is also stored inside the
database, so a new one on an existing database needs `ALTER USER app PASSWORD '…'` in `db-0`
first. Then seal again as in step 2 above.

**The bucket's Customer Secret Key.** Oracle console → Identity → the Backup Writer user → Customer
secret keys: delete the old key, make a new one, put both halves in the password manager, and seal
`backup-secrets` again as in step 2 above.

**The backup gpg private key or its passphrase.** The dumps in the bucket can never be opened again;
they go by themselves after 30 days. Make a new key pair in the pinned database image, with no
network (`DB_IMAGE` as in 7.3.1), after moving any old files in `~/denial-appeal-keys` aside:

```
docker run -it --rm --network none -v "$HOME/denial-appeal-keys:/keys" "$DB_IMAGE" bash -c '
  export GNUPGHOME=$(mktemp -d)
  gpg --pinentry-mode loopback --quick-generate-key "denial-appeal backup" rsa3072 encrypt never
  gpg --armor --export > /keys/backup-public-key.asc
  gpg --pinentry-mode loopback --armor --export-secret-keys > /keys/backup-private-key.asc'
```

Put the private key and its passphrase in the password manager. Copy the new public key over
`deploy/k8s/base/backup/backup-public-key.asc` and merge it in a pull request (update the
fingerprint in [3.28](03-architecture.md#328-backups-and-alerts)). Then run one backup by hand
(7.4.9 step 2) and restore it with 7.3.1: until that works there is no usable backup.

## 7.7 Rehearsal record

Run on the Mac on 2026-11-03, by the owner, with throwaway copies only. Nothing on the server was
changed; production was only read (one row count).

| What | How | Result |
|---|---|---|
| Restore the newest backup (7.3.1) | `app-2026-11-03.dump.gpg` (19,704,056 bytes) from the bucket, `restore.sh` into `restored` | 30 tables; row counts identical to production's (`diff` empty), e.g. `evidence_chunks` 2,873, `uploads` 1, `llm_calls` 4 |
| Load over a migrated database (7.4.8) | New database `target`, `alembic upgrade head` (0024, only the `alembic_version` row), then `pg_dump restored` piped into `pg_restore --clean --if-exists --no-owner --single-transaction --exit-on-error` | Exit 0; the 30 counts identical to production's; still revision 0024 and `vector` 0.8.7 |
| Put back the Sealed Secrets key (7.4.3, 7.4.4, 7.4.7) | Fresh `kind` cluster on CI's node image (`kindest/node:v1.36.4`); the exported key applied **before** the pinned controller; then both committed sealed files | Controller: `registered private key secretname=sealed-secrets-key6nl2j`, no new key; both files `Unsealed`; `app-secrets` 10 keys, `backup-secrets` 2, the same names as the templates |

Not rehearsed: the SSH and `kubectl exec` legs of 7.4.8 (they only carry the bytes), anything on a
real new machine, all of Path B, and all of 7.6. The `kind` cluster and the decrypted copy were
deleted afterwards. The SealedSecret list in 0.40.0 shows only a name and an age, so the check is
the Secret's `DATA` count (7.4.7), not that list.

## 7.8 Stage 6 done-condition record

Stage 6 is done when the app is served continuously for under $5 a month. Read by the owner on
2026-11-04, on the current server; nothing was changed there.

| What | How | Result |
|---|---|---|
| The nightly backup runs by itself | `kubectl get jobs` and `kubectl logs job/backup-29896020 --all-containers` | The 2026-11-04 03:00 UTC run, the first scheduled one after the `wait-for-db` fix: `Complete 1/1` in 14 s; `wait-for-db` answered at 03:00:01; `app-2026-11-04.dump.gpg` written, 19,704,056 bytes; rclone `Copied (new)`, 18.791 MiB in 0.5 s |
| The bucket keeps them | Oracle console, `denial-appeal-backups` → `app/` | 3 backups: 2026-11-02, 03 and 04 |
| Served continuously | UptimeRobot, the `/health` monitor | 99.783% since the monitor was made. The missing 0.217% matches the planned alert test of 2026-11-03 ([3.28](03-architecture.md#328-backups-and-alerts)), about 5 minutes of `web` at 0 replicas |
| Under $5 a month | Oracle Cost analysis; `sum(cost_usd)` of `llm_calls` by month through `kubectl exec` on `db-0` | Oracle $0.00 for November so far; model $0.001617 (4 calls), no rows before November |
| Every gate required | Ruleset `protect-main`, read back with `gh api` | 9 required checks: `checks`, `zizmor`, `frontend`, `trivy`, `lock-tests`, `image-scan`, `backup-restore`, `manifests`, `smoke` |

The done condition is met: the app has been served from the Oracle machine since 2026-11-02, the
backup now runs each night without help, and the month so far cost $0.001617.
