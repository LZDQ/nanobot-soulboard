# External Services

To deploy nanobot soulboard along with other services, it is recommended to run these external services as another user with higher privileges.

This directory uses `docker-compose.yml` for litellm proxy, napcat qq bot and filebrowser.

## NapCatQQ

Create `.env.napcat` and set `NAPCAT_UID` and `NAPCAT_GID` to your `id -u` and `id -g` to avoid permission errors.

Example:

```dotenv
NAPCAT_UID=1000
NAPCAT_GID=1000
```

## ttyd

You should run this in your own shell because running shell inside container is not that useful.

Example:

```bash
ttyd -i lo -W bash
```

Change `lo` to your loopback interface name, for example `lo0`.
