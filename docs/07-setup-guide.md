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

A step marked **(not tried)** has not been run on a rebuilt server yet. Every other step is how the
current server was built (2026-11-02 and 03).

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
Postgres, and from there copied into the new server ([7.4.8](#748-load-the-data-not-tried)).
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

### 7.4.3 Put back the Sealed Secrets key (not tried)

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
(`sudo k3s kubectl -n kube-system logs deployment/sealed-secrets-controller`) does not say it
generated a new key.

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

### 7.4.8 Load the data (not tried)

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

Written in the next step of this branch.

## 7.6 If a key is lost

Written in a later step of this branch.

## 7.7 Rehearsal record

Written in a later step of this branch.

## 7.8 Stage 6 done-condition record

Written in a later step of this branch.
