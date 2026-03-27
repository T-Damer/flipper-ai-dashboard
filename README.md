# Flipper AI Dashboard

`Flipper AI Dashboard` is a two-part project:

- A Flipper Zero external app that shows AI provider usage on-device.
- A local bridge script that collects usage snapshots on macOS/Linux and writes a compact text file the Flipper app can read from `/ext/apps_data/ai_dashboard/usage.txt`.

## What It Does

- Main screen with all visible providers.
- Left and right switch between the main screen and per-provider detail screens.
- Up and down scroll the main screen.
- Center button opens settings.
- Settings let you toggle main-screen autoscroll and hide providers from the main screen.
- If there is no data, or you hide everything, the app falls back to a deliberately goofy idle screen.

The current bridge ships with:

- `codex` auto-collection through `codex app-server`
- `claude` best-effort CLI parsing when `claude` is installed
- manual provider injection through a JSON file for ChatGPT, Cursor, Gemini, or anything else
- a sample mode so you can test the Flipper UI before wiring real collectors

## Repo Layout

- [`flipper/`](./flipper) Flipper Zero external app
- [`bridge/`](./bridge) local collector/sync script
- [`docs/snapshot-format.md`](./docs/snapshot-format.md) bridge/app file contract

## Build The Flipper App

Run `ufbt` from the app folder:

```bash
cd flipper
ufbt
```

That should produce a `.fap` you can load onto the Flipper.

## Generate Sample Data

```bash
python3 bridge/ai_usage_bridge.py sample
```

By default this writes to `bridge/out/usage.txt`.

To write directly to a mounted Flipper SD card:

```bash
python3 bridge/ai_usage_bridge.py sample --flipper-root /Volumes/FLIPPER
```

## Collect Real Data

Auto-detect local collectors and merge them with manual providers:

```bash
python3 bridge/ai_usage_bridge.py collect \
  --manual bridge/providers.local.json \
  --flipper-root /Volumes/FLIPPER
```

If you only want a local output file:

```bash
python3 bridge/ai_usage_bridge.py collect --output bridge/out/usage.txt
```

## USB Sync

Recommended on macOS right now.

This pushes the collected snapshot directly to `/ext/apps_data/ai_dashboard/usage.txt` over the Flipper USB CLI port:

```bash
python3 bridge/ai_usage_bridge.py usb-push --port auto
```

Transport-only test with sample data:

```bash
python3 bridge/ai_usage_bridge.py usb-push --port auto --sample-if-empty --skip-codex --skip-claude
```

You can override the detected CDC port with something like:

```bash
python3 bridge/ai_usage_bridge.py usb-push --port /dev/cu.usbmodemflip_Azalon1
```

## BLE Sync

The Flipper app can now listen for snapshot updates over the Flipper BLE serial profile while it is open.

Install the Python BLE client dependency on macOS:

```bash
pip install bleak
```

Then collect and push a fresh snapshot over BLE:

```bash
python3 bridge/ai_usage_bridge.py ble-push --name Flipper
```

Useful options:

- `--address AA:BB:CC:DD:EE:FF` to target a specific paired Flipper
- `--sample-if-empty` to test the BLE path even if no collector returns data
- `--output bridge/out/ble_snapshot.txt` to also save the exact payload locally

The current bridge writes to the Flipper BLE serial TX characteristic UUID `19ed82ae-ed21-4c9d-4145-228e62fe0000`.

## Manual Providers

Copy the example file and edit it:

```bash
cp bridge/providers.example.json bridge/providers.local.json
```

Example use cases:

- track a ChatGPT web subscription window manually
- track Cursor or Gemini with your own script
- override auto-collected providers with cleaner names/details

## Controls On Flipper

- `Left` / `Right`: move between the overview and provider detail screens
- `Up` / `Down`: scroll the main screen
- `OK`: open settings
- `Back`: leave settings or exit the app

## Custom Icons

Provider icons are bundled from PNG files in [`flipper/images/`](./flipper/images).

- `chatgpt_12x12.png`
- `claude_12x12.png`
- `codex_12x12.png`
- `cursor_12x12.png`
- `gemini_12x12.png`
- `bot_12x12.png`

If you want to draw them yourself, replace those files with your own monochrome `12x12` PNGs and rebuild with:

```bash
cd flipper
ufbt
```

The app list icon is [`flipper/radar_ai_10px.png`](./flipper/radar_ai_10px.png).
