# kzkitty

discord bot for the csgo kz global api

available commands:

    `/pb` - show a personal best run for a given map/mode/player
    `/latest` - show the most recent run
    `/register` - register a steam profile url and default mode with the bot
    `/mode` - change the default mode when using `/pb` and `/latest`

to set up a dev environment:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv ~/envs/kzkitty
source ~/envs/kzkitty/bin/activate
git clone https://github.com/catsymint/kzkitty.git ~/src/kzkitty
cd ~/src/kzkitty
uv pip install -r requirements.txt
```

then, to run locally:

```sh
source ~/envs/kzkitty/bin/activate
KZKITTY_DB=kzkitty.db KZKITTY_DISCORD_TOKEN=... python -O -m kzkitty
```

optionally set the `KZKITTY_INITIAL_PLAYERS` environment variable to point to
a csv file in the following format:

```csv
id,steamid64,mode
...
```

this will prepopulate the database with discord ids mapped to steam ids and
preferred kz game modes

to deploy with docker compose, use a `docker-compose.yaml` file:

```yaml
services:
  kzkitty:
    container_name: kzkitty
    user: "1000:1000"
    build:
      context: ~/src/kzkitty
      dockerfile: Dockerfile
    environment:
      - KZKITTY_DB=/etc/kzkitty/kzkitty.db
      - KZKITTY_DEFAULT_PLAYERS=/etc/kzkitty/players.csv
      - KZKITTY_DISCORD_TOKEN=...
    volumes:
      - './etc-kzkitty:/etc/kzkitty'
```

then build and start the service:

```sh
docker compose up --build -d kzkitty
```

note that you will need to visit https://discord.com/developers/applications
to create an application for the bot. this will give you the discord token
needed above. the bot only requires `application.commands` and `bot` oauth2
scopes. for bot permissions, only `send messages` and `send messages in
threads` is needed
