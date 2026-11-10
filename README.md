# kzkitty

Discord bot for the CS:GO and CS2 KZ global APIs.

Available commands:

- `/pb` - show a personal best run for a given map/mode/player
- `/latest` - show the most recent run
- `/profile` - show current rank, points, point average
- `/map` - show map info and world record times
- `/register` - register a steam profile url and default mode with the bot
- `/unregister` - unregister your steam profile
- `/mode` - change the default mode to use for commands
- `/server` - show server status (the map, who's playing, etc.)

<img alt="screenshot of /pb, /map, /profile" src="screenshot.png" width="672">

## Development

To set up a dev environment (with `uv`):

```sh
git clone https://github.com/misscatmint/kzkitty.git ~/src/kzkitty
cd ~/src/kzkitty
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

Then, to run locally:

```sh
KZKITTY_DB=sqlite://kzkitty.db KZKITTY_DISCORD_TOKEN=... uv run -m kzkitty
```

Optionally set the `KZKITTY_DEFAULT_PLAYERS` environment variable to point to
a CSV file in the following format:

```csv
user_id,server_id,steamid64,mode
...
```

This will prepopulate the database with Discord users mapped to Steam IDs and
preferred KZ game modes (on a per-Discord server basis).

Similarly, set `KZKITTY_DEFAULT_SERVERS` to a CSV file in the following
format:

```csv
name,address,game,preferred_mode,location,server_id,channel_id,message_id,is_default
...
```

Setting `channel_id` will make the bot keep one auto-updating status message
in that channel, covering every server with that `channel_id`. Set
`message_id` on any of those rows to reuse an existing message instead of
posting a new one. Rows for the same channel can't have different
`message_id`s. To show a server in more than one channel, give it one row per
channel. The first row's details (name, location, etc.) are used for all of
them. `is_default` controls what server `/server` uses by default.

`KZKITTY_REFRESH_DB_HOURS` can optionally be used to control how frequently
the map cache is updated from the global APIs. The default is 24 hours.

`KZKITTY_REFRESH_SERVER_MINS` controls how often the bot queries default
servers to update their Discord messages.

To control timeouts, set `KZKITTY_API_TIMEOUT`, `KZKITTY_STEAM_TIMEOUT`, and
`KZKITTY_A2S_TIMEOUT` (in seconds). They default to 3, 5, and 5 seconds
respectively.

Set `KZKITTY_REST` to an `address:port` to run the bot as a REST bot, or to
`1` to listen on `0.0.0.0:8080`. This requires an internet-facing HTTPS server
to proxy to kzkitty (see [Deploying as a REST bot](#deploying-as-a-rest-bot)).

`KZKITTY_CONNECT_URL` can be set to the public URL of the sidecar (see below)
to turn server IPs into links that launch the game and join the server. This
works for CS2 servers and the standalone CS:GO app. Servers on the
`csgo_legacy` beta branch of CS2 are shown with a note instead, since a link
can't choose that branch.

The sidecar is a second process that shows the bot as online (REST bots can't
do this themselves) and serves the join links. Run it with:

```sh
KZKITTY_DB=sqlite://kzkitty.db KZKITTY_DISCORD_TOKEN=... \
    uv run -m kzkitty sidecar
```

It listens on `KZKITTY_SIDECAR` (an `address:port`, defaulting to
`0.0.0.0:8081`) and uses the same database as the bot.

## Deployment

To deploy with Docker Compose, use a `compose.yaml` file:

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
      - KZKITTY_DB=sqlite:///etc/kzkitty/kzkitty.db
      - KZKITTY_DEFAULT_PLAYERS=/etc/kzkitty/players.csv
      - KZKITTY_DEFAULT_SERVERS=/etc/kzkitty/servers.csv
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

### Deploying as a REST bot

Running as a REST bot needs three services: the bot, the sidecar, and an HTTPS
server in front of both. This example uses Caddy, which gets a certificate
for your domain automatically. Replace `kzkitty.example.com` with a domain
pointing at your server:

```yaml
services:
  kzkitty:
    container_name: kzkitty
    restart: unless-stopped
    user: "1000:1000"
    build:
      context: ~/src/kzkitty
      dockerfile: Dockerfile
    image: kzkitty
    environment:
      - KZKITTY_DB=sqlite:///etc/kzkitty/kzkitty.db
      - KZKITTY_DEFAULT_PLAYERS=/etc/kzkitty/players.csv
      - KZKITTY_DEFAULT_SERVERS=/etc/kzkitty/servers.csv
      - KZKITTY_DISCORD_TOKEN=...
      - KZKITTY_REST=1
      - KZKITTY_CONNECT_URL=https://kzkitty.example.com
      - TZ=America/Chicago
    volumes:
      - './etc-kzkitty:/etc/kzkitty'
    healthcheck:
      test: ["CMD", "python", "-m", "kzkitty", "healthcheck"]
      interval: 30s
      timeout: 10s

  kzkitty-sidecar:
    container_name: kzkitty-sidecar
    restart: unless-stopped
    user: "1000:1000"
    image: kzkitty
    command: ["python", "-m", "kzkitty", "sidecar"]
    environment:
      - KZKITTY_DB=sqlite:///etc/kzkitty/kzkitty.db
      - KZKITTY_DISCORD_TOKEN=...
    volumes:
      - './etc-kzkitty:/etc/kzkitty'

  caddy:
    container_name: caddy
    restart: unless-stopped
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - './Caddyfile:/etc/caddy/Caddyfile:ro'
      - 'caddy-data:/data'
      - 'caddy-config:/config'

volumes:
  caddy-data:
  caddy-config:
```

The sidecar reuses the image built for the bot, so only `kzkitty` has a
`build` section. The health check marks the bot as unhealthy in `docker ps`
if its web server stops answering. Docker doesn't restart unhealthy
containers on its own, so it's only a status label.

Next to `compose.yaml`, create a `Caddyfile` that sends join links to the
sidecar and everything else to the bot:

```
kzkitty.example.com {
	handle /connect/* {
		reverse_proxy kzkitty-sidecar:8081
	}
	handle {
		reverse_proxy kzkitty:8080
	}
}
```

Build and start everything:

```sh
docker compose up --build -d
```

Finally, in the Discord developer portal, set the application's
"Interactions Endpoint URL" to `https://kzkitty.example.com/`. Discord checks
the URL when you save it, so the bot must already be running.

### Discord application

Note that you will need to visit https://discord.com/developers/applications
to create an application for the bot. This will give you the Discord token
needed above. The bot only requires `application.commands` and `bot` OAuth2
scopes. For bot permissions, only `send messages` and `send messages in
threads` are needed.
