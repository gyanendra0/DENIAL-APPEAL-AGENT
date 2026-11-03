#!/bin/bash
# Decrypts a dump made by backup.sh and restores it into a NEW database. createdb refuses a name
# that exists, so this never writes over `app` or any other database. Not part of the server's
# ConfigMap: it runs on the Mac (in the pinned db image, which has gpg) and in the CI job
# `backup-restore`.
#
# Usage: restore.sh <dump.gpg file> <private key file> <new database>
# Reads PGHOST, PGPORT, PGUSER and PGPASSWORD for the server to restore into.
set -euo pipefail

dump="$1"
private_key="$2"
database="$3"

# A plain name only, so it cannot be read as an option or a connection string.
if [[ ! "$database" =~ ^[a-z_][a-z0-9_]*$ ]]; then
  echo "refused: database name must match ^[a-z_][a-z0-9_]*\$" >&2
  exit 2
fi

# The key is imported into a home of its own, removed when the script ends.
GNUPGHOME="$(mktemp -d)"
export GNUPGHOME
trap 'rm -rf "$GNUPGHOME"' EXIT
gpg --batch --quiet --import "$private_key"

createdb "$database"

# Loopback asks for the key's passphrase on this terminal (the image has no pinentry). The
# decrypted dump goes through the pipe only, never to disk. One transaction: a failed restore
# leaves an empty database, not half of one.
gpg --quiet --pinentry-mode loopback --decrypt "$dump" \
  | pg_restore --no-owner --single-transaction --exit-on-error --dbname "$database"
echo "restored $(basename "$dump") into $database"
