# kzkitty

Discord bot for the CS:GO KZ global API.

Available commands:

- `/pb` - show a personal best run for a given map/mode/player
- `/latest` - show the most recent run
- `/profile` - show current rank, points, point average
- `/map` - show map info and world record times
- `/register` - register a steam profile url and default mode with the bot
- `/mode` - change the default mode to use for commands

<img alt="screenshot of /pb, /map, /profile" src="screenshot.png" width="672">

## Development

To set up a dev environment (with `uv`):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv ~/envs/kzkitty
source ~/envs/kzkitty/bin/activate
git clone https://github.com/catsymint/kzkitty.git ~/src/kzkitty
cd ~/src/kzkitty
uv pip install -r requirements.txt
```

Then, to run locally:

```sh
source ~/envs/kzkitty/bin/activate
KZKITTY_DB=kzkitty.db KZKITTY_DISCORD_TOKEN=... python -O -m kzkitty
```

Optionally set the `KZKITTY_INITIAL_PLAYERS` environment variable to point to
a CSV file in the following format:

```csv
user_id,server_id,steamid64,mode
...
```

This will prepopulate the database with Discord users mapped to Steam IDs and
preferred KZ game modes (on a per-Discord server basis).

## Deployment

To deploy with Docker Compose, use a `docker-compose.yaml` file:

```yaml
services:
  kzkitty:
    container_name: kzkitty
    restart: unless-stopped
    user: "1000:1000"
    build:
      context: ~/src/kzkitty
      dockerfile: Dockerfile
    environment:
      - KZKITTY_DB=/etc/kzkitty/kzkitty.db
      - KZKITTY_DEFAULT_PLAYERS=/etc/kzkitty/players.csv
      - KZKITTY_DISCORD_TOKEN=...
      - TZ=America/Chicago
    volumes:
      - './etc-kzkitty:/etc/kzkitty'
```

Change `TZ` to the timezone to use for nightly map database refreshes. Then
build and start the service:

```sh
docker compose up --build -d kzkitty
```

Note that you will need to visit https://discord.com/developers/applications
to create an application for the bot. This will give you the Discord token
needed above. The bot only requires `application.commands` and `bot` OAuth2
scopes. For bot permissions, only `send messages` and `send messages in
threads` are needed.
