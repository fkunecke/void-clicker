# void-clicker

Loot/health monitor and auto-triager for a roguelike played through iPhone
Mirroring. It watches the screen, and once a dropped item's rarity is
confirmed, clicks the corresponding sell/salvage/stash button. "Manual"
rarities (currently eldritch) and unrecognized drops are never clicked --
left for you to handle by hand.

## Setup (one-time)

```
pip3 install pyautogui mss numpy pynput
```

Grant Terminal (or your IDE) access under:

- System Settings > Privacy & Security > Accessibility
- System Settings > Privacy & Security > Screen Recording
- System Settings > Privacy & Security > Input Monitoring (needed for
  `calibrate`'s click detection, separate from Accessibility)

## Calibration

Pixel locations are machine/screen-specific, so they're kept out of the
script and live in a local `.env` file instead. Generate them by running:

```
python3 main.py calibrate
```

It walks you through clicking each of the following spots in order. For the
two health points, it also shows the color it read and whether that matched
the expected "good" color -- if it doesn't match, it'll ask you to click
that same point again.

1. **Health point** -- a pixel on the main health bar.

   <!-- TODO: screenshot of where to click for the health point -->

2. **Max health checkpoint** -- a point further along the health bar, at
   roughly the 95% mark. Used to tell "good tier" apart from "actually near
   max" (see `REQUIRE_MAX_HEALTH` below).

   <!-- TODO: screenshot of where to click for the max health checkpoint -->

3. **Drop region top-left corner** -- the item box is anchored to the
   bottom and grows upward with more stats, so this and the next point mark
   the max bounds the box (and its icon) can ever occupy.

   <!-- TODO: screenshot of the drop region's top-left corner -->

4. **Drop region bottom-right corner**

   <!-- TODO: screenshot of the drop region's bottom-right corner -->

5. **Equipped region top-left corner** -- same idea, for the box comparing
   against your currently equipped item.

   <!-- TODO: screenshot of the equipped region's top-left corner -->

6. **Equipped region bottom-right corner**

   <!-- TODO: screenshot of the equipped region's bottom-right corner -->

7. **Sell button**

   <!-- TODO: screenshot of the sell button -->

8. **Salvage button**

   <!-- TODO: screenshot of the salvage button -->

9. **Stash button**

   <!-- TODO: screenshot of the stash button -->

This writes `HEALTH_POINT`, `MAX_HEALTH_POINT`, `DROP_REGION`,
`EQUIPPED_REGION`, `SELL_BUTTON_LOCATION`, `SALVAGE_BUTTON_LOCATION`, and
`STASH_BUTTON_LOCATION` into `.env`, merging with (not overwriting) whatever
else is already there.

## `.env` reference

None of these have fallback values in the script -- if one's missing, the
script tells you which key and how to add it, then exits (except
`calibrate`, which doesn't need any of them to run).

| Key | Format | Example |
|---|---|---|
| `HEALTH_POINT` | `x,y` | `1955,194` |
| `MAX_HEALTH_POINT` | `x,y` | `2072,194` |
| `DROP_REGION` | `left,top,right,bottom` | `1947,472,2120,740` |
| `EQUIPPED_REGION` | `left,top,right,bottom` | `2133,472,2306,740` |
| `SELL_BUTTON_LOCATION` | `x,y` | `1986,783` |
| `SALVAGE_BUTTON_LOCATION` | `x,y` | `2120,789` |
| `STASH_BUTTON_LOCATION` | `x,y` | `2142,784` |
| `ACTIONS` | `rarity:action,...` | `crude:sell,sturdy:salvage,enchanted:salvage,mythic:salvage,relic:stash,eldritch:manual` |
| `REQUIRE_MAX_HEALTH` | `true` or `false` | `false` |
| `INVENTORY_CAPACITY` | integer | `5` |
| `INVENTORY_USED` | integer, set to whatever's actually in your inventory when you start | `1` |

The first seven are written by `calibrate`; the last four are hand-edited.

Rarity colors (`RARITY_COLORS`) and the tuning constants further down (tap
timing, match thresholds, etc.) stay in the CONFIG section of `main.py`
itself, since they're not machine-specific in the same way.

## Modes

```
python3 main.py calibrate     # interactive: click each pixel location
python3 main.py auto          # default mode: run the monitor/auto-triager
python3 main.py debug         # hover and print live cursor position + color
python3 main.py debug-drops   # live per-rarity match scores for drop/equipped
python3 main.py timing        # benchmark actual screen-capture latency
```

`auto` prints the current `.env`-derived settings (actions, inventory,
require-max-health) first and waits for a keypress -- press Escape to bail
out and edit `.env` yourself, or any other key to start.
