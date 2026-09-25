# IRL Corpus

Annotation pipeline for Twitch IRL recordings. Manages recordings, segments, and ELAN (`.eaf`) annotation tasks across a team of annotators.

---

## Installation

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e .
```

`ffmpeg` must also be on your `PATH`:

```bash
sudo apt install ffmpeg   # Ubuntu/Debian
```

---

## Configuration

Create `irl.toml` in your working directory (never committed — it's gitignored):

```toml
[storage]
backend = "local"
root = "/mnt/secure_hdd/twitch"

[database]
url = "sqlite:///./corpus.db"

[annotator]
name = "yourname"

[services]
transcribe_url = "https://your-transcription-service/transcribe"
```

For OVH Object Storage + PostgreSQL:

```toml
[storage]
backend = "s3"
bucket = "twitch-corpus"
endpoint = "https://s3.gra.io.cloud.ovh.net"
# access_key and secret_key via env vars (see below)

[database]
url = "postgresql://user:pass@host/dbname"

[annotator]
name = "yourname"
```

For an annotator machine reaching the corpus over SSH (see `irl task fetch`):

```toml
[storage]
backend = "sftp"
host = "central.tailnet-name.ts.net"
username = "irl"
key_filename = "~/.ssh/irl_corpus"
root = "/mnt/secure_hdd/twitch"   # the host [storage] root
# port = 22

[database]
url = "postgresql://user:pass@host/dbname"

[annotator]
name = "yourname"
```

Sensitive values can be set via environment variables instead of the config file:

```bash
export IRL_STORAGE_ACCESS_KEY="..."
export IRL_STORAGE_SECRET_KEY="..."
export IRL_STORAGE_HOST="central.tailnet-name.ts.net"
export IRL_STORAGE_USER="irl"
export IRL_STORAGE_SSH_KEY="~/.ssh/irl_corpus"
export IRL_STORAGE_PASSWORD="..."
export IRL_DB_URL="postgresql://..."
export IRL_ANNOTATOR_NAME="yourname"
export IRL_TRANSCRIBE_URL="https://..."
```

`irl annotate` requires `backend = "local"`, so auto-annotation runs on the machine
holding the recordings, not on annotator laptops.

---

## PostgreSQL setup (multiple machines)

On the database host:

```bash
sudo apt install postgresql

sudo -u postgres PGDB_PASSWORD='choose-a-password' ./scripts/setup_postgres.sh
#   PGDB_USER=irl          override the role name
#   PGDB_NAME=irl_corpus   override the database name

sudo -u postgres psql -c \
    "ALTER SYSTEM SET listen_addresses = 'localhost,$(tailscale ip -4)';"

echo "host  irl_corpus  irl  100.64.0.0/10  scram-sha-256" \
    | sudo tee -a /etc/postgresql/16/main/pg_hba.conf

sudo systemctl restart postgresql
```

On each machine using the corpus set the db url as:

```bash
"postgresql://irl:choose-a-password@<tailscale-host>:5432/irl_corpus"
```

---

## SSH setup (multiple machines)

Annotator machines fetch corpus files over SFTP, so the machine holding them needs
an SSH server.

On the file host, to create the server:

```bash
sudo CORPUS_ROOT=/mnt/secure_hdd/twitch ./scripts/ssh-server-setup.sh
#   SSH_USER=irl   override the account name
```

On each annotator machine:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/irl_corpus -C "yourname@irl-corpus"
cat ~/.ssh/irl_corpus.pub
```

Back on the file host, one line per annotator:

```bash
printf 'from="100.64.0.0/10",restrict %s\n' '<pasted public key>' \
    | sudo -u irl tee -a /home/irl/.ssh/authorized_keys
```

Then on the annotator machine, record the host key — paramiko refuses to connect to
hosts missing from `known_hosts`:

```bash
sftp -i ~/.ssh/irl_corpus irl@<tailscale-host>      # accept the fingerprint
```

`from="100.64.0.0/10"` limits each key to Tailscale addresses, and `restrict`
disables everything except file transfer. Revoke an annotator by deleting their line
from `authorized_keys`.

---

## First-time setup

```bash
irl admin init                                        # create the database schema
irl schema import annotations/annotations-v0.1.0-20260510.json   # import annotation schema
irl user add --name yourname --role admin             # add yourself
irl user add --name alice                             # add other annotators
```

---

## CLI reference

### `irl recording`

```bash
irl recording add <file> --title "Stream 2026-05-01"
#   --duration-ms <ms>     set duration if known (otherwise set it separately)
#   --auto-annotate        run visual + ASR + translation immediately (default: off)
#   --lang fr   --translate-lang ""   (only relevant with --auto-annotate)

irl recording list
irl recording show <recording_id>

irl recording split <recording_id>
#   --duration-ms 1800000   segment length (default 30 min)
#   --no-tasks              skip auto-creating annotation tasks
```

### `irl segment`

```bash
irl segment list <recording_id>
#   --removed   also show removed segments

irl segment show <segment_id>

irl segment split <segment_id> --at-ms <ms>
#   --gap-to-ms <ms>     create an explicit gap between the two halves
#   --gap-notes "..."    note on the gap

irl segment remove <segment_id>
#   --reason "private content"
#   --no-gap             stitch neighbours together instead of inserting a gap
```

### `irl task`

```bash
# Browse
irl task list
#   --type annotation|validation
#   --status pending|in_progress|done|approved|rejected
#   --assignee alice
#   --recording <id>  --segment <id>
#   --mine            only tasks assigned to me

irl task show <task_id>   # details + save history + chain

# Annotator workflow
irl task fetch <task_id>                 # download EAF + recording for ELAN
#   --dest ~/irl_work    local working directory
#   --force              re-download even if the media is already present
irl task take <task_id>                   # assign to self + soft lock
irl task working <task_id>                # mark as currently open (already assigned)
irl task release <task_id>               # clear working-on lock (stepping away)
irl task save <task_id> <file.eaf>       # save partial work
#   --notes "..."
irl task finish <task_id> <file.eaf>     # submit final EAF → auto-creates validation task
#   --notes "..."

# Validator workflow
irl task approve <task_id>               # segment becomes validated
irl task reject <task_id> <file.eaf>     # save annotated EAF with notes → creates round 2
#   --notes "..."

# Admin / schema change
irl task assign <task_id> --to alice
irl task reannotate <segment_id>         # supersede existing tasks, start fresh
#   --schema 0.2.0                       use a specific schema version
```

### `irl schema`

```bash
irl schema import <file.json>    # import schema (sets as current by default)
#   --no-current                 import without setting as current

irl schema list
irl schema set-current <id|version>
```

### `irl user`

```bash
irl user add --name alice
#   --role admin|annotator   (default: annotator)

irl user list
```

### `irl admin`

```bash
irl admin init          # create the database schema (run on first setup)
irl admin stats         # corpus-wide progress overview
irl admin locks         # show all active working-on soft locks
irl admin lock-clear <task_id>   # force-clear a stuck lock
```

### `irl annotate`

Auto-annotation for a task's segment. Requires `ffmpeg` on PATH.

```bash
irl annotate visual <task_id>
#   --model gemini-2.5-flash   --fps 2.0   --replace/--no-replace
#   requires GEMINI_API_KEY

irl annotate transcribe <task_id>
#   --engine legacy|elevenlabs   (default: legacy)
#   --lang fr   --translate-lang ""
#   --api-url <url>   --chunk-seconds 240   (legacy engine)
#   --num-speakers <n>   (elevenlabs engine)
#   --clear/--no-clear
#   legacy requires transcribe_url configured; elevenlabs requires ELEVENLABS_API_KEY
#   --translate-lang works with either engine when OPENAI_API_KEY is set
#   (a standalone GPT pass runs after ASR); without it, only the legacy
#   engine's own bundled translation applies

irl annotate translate <task_id> --target-lang en
#   --source-lang ""   --model gpt-5-nano   --clear/--no-clear
#   standalone GPT translation of whatever is already in the 'transcript'
#   tier, regardless of which ASR engine wrote it; requires OPENAI_API_KEY

irl annotate auto <recording_id>
#   --lang fr   --translate-lang ""   --force
#   runs visual + ASR + translation for every annotation task in the recording;
#   skips steps already marked done unless --force
```

---

## Typical annotator workflow

```bash
irl task list --status pending          # find available tasks
irl task take <task_id>                 # claim it (assigns + locks)
irl task fetch <task_id>                # download the EAF + recording locally
# open the fetched EAF in ELAN, annotate…
irl task save <task_id> my_work.eaf    # save partial progress anytime
# …keep annotating…
irl task finish <task_id> my_work.eaf  # submit — validation task is auto-created
```

## Typical validator workflow

```bash
irl task list --type validation --status pending
irl task take <task_id>
# review EAF in ELAN, add notes in the "notes" tier if corrections needed…
irl task approve <task_id>                        # all good
irl task reject <task_id> reviewed.eaf            # needs rework — annotator gets a round 2
```

---

## Auto-annotation scripts

These scripts run independently and pre-populate EAF tiers before manual annotation.

### Visual scene annotation (Gemini)

Requires `GEMINI_API_KEY` env var and `ffmpeg` on PATH.

```bash
python scripts/annotate_visual.py data/clip.mp4 -o annotations.json
# --fps 1   --model gemini-2.5-flash

python scripts/add_visual_annotations_to_eaf.py annotations.json data/clip.eaf
# If tiers already have data: [r]eplace / [a]dd / [s]kip
```

### ASR + diarization (ElevenLabs Scribe v2, batch)

Requires `ELEVENLABS_API_KEY` env var and `ffmpeg` on PATH. Scribe v2 does
transcription, non-speech filtering, and speaker diarization in a single
batch call, populating the `transcript` and `speaker_turn` tiers.

```bash
irl annotate transcribe <task_id> --engine elevenlabs
#   --num-speakers <n>   cap the number of speakers to detect
```

`irl annotate auto` and `irl recording add` use this engine automatically
when `ELEVENLABS_API_KEY` is set (falling back to `transcribe_url` otherwise).

### Translation (GPT)

Requires `OPENAI_API_KEY` env var. Standalone step: translates whatever is
currently in the `transcript` tier into the `translation` tier, regardless
of which ASR engine wrote it — so it works the same way after either the
legacy or elevenlabs engine.

```bash
irl annotate translate <task_id> --target-lang en
#   --source-lang ""   --model gpt-5-nano   --clear/--no-clear
```

`irl annotate transcribe`, `irl annotate auto`, and `irl recording add` all
chain this in automatically whenever `--translate-lang` is set and
`OPENAI_API_KEY` is configured. Without an OpenAI key, the legacy engine's
own bundled translation (via `transcribe_url`) is the only translation
available, and the elevenlabs engine gets none.

The system prompt sent to the model is a template at
[`config/translate_system_prompt.txt`](config/translate_system_prompt.txt) —
edit it directly to tune translation behavior; no code change needed. It's
rendered with `{target_lang}`, `{source_hint}`, and `{count}` placeholders.

Since the model is asked to preserve an exact line count per batch and
sometimes doesn't (merging/dropping a line), a failed batch raises instead
of silently misaligning timestamps. When that happens, the full input batch
and the model's raw response are appended as a JSON line to
`logs/gpt_translate.jsonl` (gitignored — may contain real transcript
content) for debugging.

Category definitions: [`annotations-v0.1.0-20260510.json`](annotations/annotations-v0.1.0-20260510.json)
