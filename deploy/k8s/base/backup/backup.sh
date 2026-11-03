#!/bin/bash
# Dumps the database and encrypts the dump to the backup public key, into one file named after the
# UTC date: <folder>/app-YYYY-MM-DD.dump.gpg. Only the private key, kept off the server, opens it.
# The CronJob `backup` and the CI job `backup-restore` run this same file.
#
# Usage: backup.sh <folder> <public key file>
# Reads PGHOST, PGPORT, PGUSER, PGPASSWORD and PGDATABASE, as pg_dump does.
#
# bash, not sh: the image's sh has no pipefail, and without it a failed pg_dump would still leave
# a well-formed encrypted file holding a broken dump.
set -euo pipefail

folder="$1"
public_key="$2"
out="$folder/app-$(date -u +%Y-%m-%d).dump.gpg"

# gpg needs a home it can write to; the root is read-only.
GNUPGHOME="$(mktemp -d)"
export GNUPGHOME

# A failed run leaves no file behind, so the upload never sends half a dump.
trap 'rm -f "$out.part"' ERR

pg_dump --format=custom --no-owner \
  | gpg --batch --no-tty --trust-model always --recipient-file "$public_key" \
    --compress-algo none --output "$out.part" --encrypt
mv "$out.part" "$out"
echo "backup written: $(basename "$out"), $(wc -c < "$out") bytes"
