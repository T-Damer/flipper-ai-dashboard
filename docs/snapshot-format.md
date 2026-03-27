# Snapshot Format

The Flipper app reads a line-oriented text file from:

`/ext/apps_data/ai_dashboard/usage.txt`

## Lines

Metadata:

```text
meta|2026-03-27 18:00
```

Provider entry:

```text
provider|codex|Codex|<>|42|5h limit|Resets in 2h 4m|Weekly 88% left / Plus|codex-rpc
```

## Provider Fields

In order:

1. literal `provider`
2. provider id
3. display name
4. short icon token, usually 2-3 chars
5. used percent, `0-100`
6. primary window label
7. reset text
8. detail text
9. source label

The app derives remaining percent as `100 - used`.

## Settings File

The app persists settings to:

`/ext/apps_data/ai_dashboard/settings.txt`

Format:

```text
autoscroll|1
main|codex|1
main|claude|0
```

- `autoscroll|1` enables main-screen autoscroll
- `main|<provider_id>|0|1` controls whether a provider appears on the overview screen
