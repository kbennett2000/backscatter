# Help & FAQ

Common questions and "it's not working" moments, in plain language. Most fixes are one
line.

## I opened the page but there's no radar / it's blank

Almost always, this just means **backscatter hasn't collected anything yet**. It saves a
new radar picture every few minutes while it runs, so a brand-new setup is empty for the
first little while.

- Leave it running and check back in 10–15 minutes.
- Or click **Load recent radar now** on the page to fill in the last few hours
  immediately — see [below](#i-dont-want-to-wait-can-i-load-past-radar).

If it's still blank after a while, peek at what it's doing:

```bash
docker compose logs -f
```

You should see lines about collecting and rendering. (Press **Ctrl+C** to stop watching.)

## I don't want to wait — can I load past radar? { #i-dont-want-to-wait-can-i-load-past-radar }

Yes! This is called a **backfill** — it downloads a stretch of older radar so you have
something to look at immediately.

**The easy way:** while the page is empty, click **Load recent radar now**. It pulls the
last 6 hours, shows a progress bar while it works, and the radar appears on its own when
it's done. No terminal needed. (Only one backfill runs at a time, and it picks up where
the live collector left off, so clicking it again later never downloads the same frame
twice.)

**For a specific older window** (a storm last week, a custom range), use the command line.
Run this in the project folder, with your own dates:

```bash
docker compose exec backscatter backscatter backfill --start 2026-06-01T00:00:00Z --end 2026-06-01T06:00:00Z --dry-run
```

That `--dry-run` just **previews** how much it would download (it changes nothing). When
you're happy, run it again **without** `--dry-run` and add `--yes`:

```bash
docker compose exec backscatter backscatter backfill --start 2026-06-01T00:00:00Z --end 2026-06-01T06:00:00Z --yes
```

Use times in UTC (the `Z` means UTC). Pick a window when you know there was weather, and
keep it short to start — a few hours downloads quickly.

!!! tip "Backfilling old radar + retention"
    If you backfill radar that's older than your [retention setting](configure.md#how-much-history-to-keep-retention)
    (30 days by default), backscatter will warn you that it'll be cleaned up again on the
    next tidy-up. Raise `BACKSCATTER_RETENTION_DAYS` if you want to keep older stuff.

## "unable to open database file" / the container keeps restarting

If `docker compose logs` shows **`sqlite3.OperationalError: unable to open database
file`** and the container restarts over and over, the **`data` folder is owned by
`root`** while backscatter runs as your normal user — so it can't write its database.

This happens on Linux if the `data` folder didn't exist when you first ran
`docker compose up`: Docker creates it owned by `root`. (Fresh copies of backscatter now
include an empty `data` folder so this shouldn't happen — but if you cloned an older
version, or deleted the folder, you can hit it.)

Fix it once, in the project folder:

```bash
sudo chown -R 1000:1000 ./data
docker compose up -d
```

`1000:1000` is the default user. If you set `PUID`/`PGID` in your `.env` to different
numbers (because your `id -u` / `id -g` differ), use **those** numbers instead — e.g.
`sudo chown -R 1001:1001 ./data`. backscatter also prints this exact command in its logs
when it detects the problem, so you can copy it from there.

## It says Docker isn't running / "cannot connect to the Docker daemon"

backscatter runs inside Docker, so Docker has to be on:

- **Windows / Mac:** open **Docker Desktop** and wait for the whale icon to settle. Then
  try again.
- **Linux:** start it with `sudo systemctl start docker` (and `sudo systemctl enable
  docker` to start it automatically at boot).

## "Port is already in use" / port 8085 is taken

Something else on your computer is using address `8085`. Pick a different one: open your
`.env` file, change

```
BACKSCATTER_PORT=8085
```

to, say, `BACKSCATTER_PORT=9000`, save, and run `docker compose up -d` again. Then open
**http://localhost:9000** instead.

## Where is my saved radar kept?

In the **`data`** folder inside the project folder. Everything — the radar pictures and
the small index file — lives there. To **back it up**, just copy that folder. To start
completely fresh, stop backscatter and delete the `data` folder (you'll lose your saved
radar).

## How do I stop it? And does it keep running?

Yes — once started, backscatter keeps running in the background (and restarts itself if
your computer reboots, as long as Docker is running). To stop it, run this in the project
folder:

```bash
docker compose down
```

Your saved radar in `data` stays put. Start again any time with `docker compose up -d`.

## How do I update to the latest version?

Get the newest files (download the ZIP again and replace the folder, or `git pull` if you
used git), then rebuild:

```bash
docker compose up -d --build
```

Your `data` folder and your `.env` are kept.

## Can I view it on my phone or another computer?

Yes, if it's on the same home network (Wi-Fi). Find the IP address of the computer running
backscatter (e.g. `192.168.1.50`) and, on the other device, open
**http://192.168.1.50:8085**.

??? question "How do I find that computer's IP address?"
    **Windows:** run `ipconfig` and look for "IPv4 Address". **Mac:** System Settings →
    Wi-Fi → Details. **Linux:** run `hostname -I`. It usually starts with `192.168.` or
    `10.`.

## On Linux: "permission denied" writing data

The container needs to write the `data` folder as your user. If your user isn't the
standard `1000`, set `PUID` and `PGID` in `.env` to your `id -u` and `id -g`. On rootless
Docker, comment out the `user:` line in `docker-compose.yml`. See the
[Linux guide](get-started/linux.md) for details.

## How far back can it remember?

As far back as it has collected, minus anything cleaned up by retention. By default it
keeps **30 days**; raise `BACKSCATTER_RETENTION_DAYS` (or set it to `0` for "keep
everything") on the [Configure](configure.md#how-much-history-to-keep-retention) page.

---

Still stuck, or found a bug? Open an issue on
[GitHub](https://github.com/kbennett2000/backscatter/issues) — describe what you did and
what happened, and we'll take a look.
