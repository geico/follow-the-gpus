# The board

The board shows where and when GPU capacity was available, from the rows of the prober. It runs on your computer and reads a local DuckDB file. The full requirements are in [spec 0006](../spec/0006-dashboard-spec.md).

## Try the board with no cloud

You need no cloud account and no login. This is the same run as in the [README](../README.md#try-the-board-with-no-cloud).

```sh
make init
make web/run
```

If no real rows exist, the board uses the synthetic sample in `web/sample/`. The sample values are not measurements. Open `http://127.0.0.1:4173/`. Press Ctrl-C to stop the board. The first run downloads the npm packages once.

To use another port, run `make web/run WEB_PORT=4180`.

## Show your own data

`make dev` syncs the rows from your cloud store, builds the database, and starts the board. It needs a deployed stack and your cloud login (`make dev` reads the store names from the deploy outputs). If the AWS login is expired, `make dev` prints one line and the board starts with the data that is already local. See [troubleshooting](troubleshooting.md#aws-login-expired).

## Share the board

`make web/standalone` writes one HTML file. The file opens by double-click, with no server and no network.

```sh
make web/standalone
```

The command prints the path of the file. The default is `web/dist/standalone.html`. To choose the path, run `make web/standalone OUT=<path>`.

To send the file, zip it. Then attach the zip.

CAUTION: Read the file before you share it. It holds real counts, including the quota limits of your account ([spec 0006](../spec/0006-dashboard-spec.md), section 9). It holds no bucket name and no account ID. Logs are different: they can hold account IDs, so read them before you share them ([how it works](how-it-works.md)).

For a folder that a web server can host, run `make web/export`. To look at that folder, run `make web/preview`.

## What each section answers

The board has these sections ([spec 0006](../spec/0006-dashboard-spec.md), section 11).

| Section | Question |
|---|---|
| Right now | Can I get one right now, anywhere? |
| So what | What is the short answer for each machine? |
| Stickiness | Does the last round tell me about the next? |
| Backtest | Is it better to stay in one region or ask everywhere? |
| Trend | Is it getting better or worse? |
| How many at once? | How many machines did one ask return? |
| Substitutes | When one GPU said no, what said yes? |
| Book ahead | How long a block can I book, and how soon does it start? |
| Sawtooth | When could a block start, check by check? |
| Time of day | Does the time of day matter? |
| Forecast | Does a high Spot score predict a yes? |
| Price | Did the price move with scarcity? |
| Matrix | Where did the yes come from? |
| Definitions | What does a yes mean, exactly? |
