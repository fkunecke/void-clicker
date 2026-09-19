"""
Loot/health monitor and auto-triager for an iPhone-mirrored roguelike.

Watches configured screen regions and, once a dropped item's rarity is
confirmed, clicks the corresponding sell/salvage/stash button per ACTIONS.
"manual" rarities (currently eldritch) and unrecognized drops are never
clicked -- left for you to handle by hand.

SETUP (one-time):
  pip3 install pyautogui pillow numpy
  Then grant Terminal (or your IDE) access under:
    System Settings > Privacy & Security > Accessibility
    System Settings > Privacy & Security > Screen Recording

USAGE:
  1. Calibrate to find pixel coordinates and colors:
       python3 main.py calibrate
     Move your mouse around the iPhone Mirroring window; it prints the live
     cursor position and the pixel color under it, once per second.

  2. Fill in the CONFIG section below with what you found.

  3. If drops aren't being detected, run debug mode with an item visibly
     dropped/equipped on screen to see per-rarity match scores live:
       python3 main.py debug

  4. Run the monitor:
       python3 main.py monitor
"""

import sys
import time
from datetime import datetime
import numpy as np
import pyautogui
from PIL import ImageGrab

# ---------------- CONFIG (edit these) ----------------

# Reference RGB color for each rarity tier's icon background.
# Eldritch drops rarely, so its color is a guess (red) until one is seen.
RARITY_COLORS = {
    "crude": (78, 78, 78),
    "sturdy": (52, 125, 42),
    "enchanted": (26, 98, 204),
    "mythic": (99, 11, 149),
    "relic": (246, 178, 20),
    "eldritch": (255, 0, 0),
}

# Suggested triage per rarity. "manual" means: don't suggest anything, just
# surface it so it can be looked at directly (eldritch is rare enough to
# want eyes on it).
ACTIONS = {
    "crude": "sell",
    "sturdy": "salvage",
    "enchanted": "salvage",
    "mythic": "stash",
    "relic": "stash",
    "eldritch": "manual",
}

# Inventory tracking is manual: set INVENTORY_USED to whatever's actually in
# your inventory when you start the script. From there, every "stash" action
# increments it by 1 for the rest of the session.
INVENTORY_CAPACITY = 5
INVENTORY_USED = 2

# How close a sampled color needs to be to a reference color to count as a
# match (0 = exact only; higher = more tolerant of variation). Distance is
# summed absolute difference across R/G/B, so the effective threshold is
# roughly tolerance * 3.
RARITY_TOLERANCE = 25

# The item info box is anchored to the BOTTOM, so it grows upward as an item
# has more stats -- the icon's position shifts and isn't a fixed offset from
# either corner. These regions are the max bounds the box (and therefore the
# icon) can ever occupy; we search inside them rather than sampling a point.
DROP_REGION = (1947, 472, 2120, 740)
EQUIPPED_REGION = (2133, 472, 2306, 740)

SELL_BUTTON_LOCATION = (1986, 783)
SALVAGE_BUTTON_LOCATION = (2120, 789)
STASH_BUTTON_LOCATION = (2142, 784)

# Where to click for each action. Actions with no entry here (currently just
# "manual") are never clicked -- same for an unrecognized/None action, e.g.
# an eldritch drop whose color doesn't match the (unconfirmed) guess above.
BUTTONS = {
    "sell": SELL_BUTTON_LOCATION,
    "salvage": SALVAGE_BUTTON_LOCATION,
    "stash": STASH_BUTTON_LOCATION,
}

# Once the item box closes, the attack button (which starts the next
# encounter) lands in the same spot the sell button was in.
ATTACK_BUTTON_LOCATION = SELL_BUTTON_LOCATION

# How long to wait after clicking sell/salvage/stash before attacking again.
ATTACK_DELAY = 0.1

# iPhone Mirroring translates mouse input into touches, and an instant
# move-then-click (near-zero dwell/press time) doesn't always register as a
# tap. CLICK_SETTLE lets the cursor "arrive" before pressing down;
# CLICK_HOLD is how long to hold the press before releasing. These are the
# main lever if taps start getting silently dropped again -- raise these
# two specifically before touching anything else below.
CLICK_SETTLE = 0.05
CLICK_HOLD = 0.08

# Tuning the tap itself only goes so far -- taps still get silently dropped
# sometimes. Instead of hoping the timing is exactly right, verify each tap
# actually did something (via our own drop detection) and retry if it
# didn't, rather than blindly moving on.
TAP_RETRY_ATTEMPTS = 3
TAP_POLL_INTERVAL = 0.05
# How many consecutive polls must agree before something is considered
# confirmed (guards against a single-frame flicker looking like the truth).
CONFIRM_POLLS = 2
# How long to wait for a sell/salvage/stash tap to close the item box.
ACTION_CONFIRM_TIMEOUT = 1.0
# How long to wait for an attack tap to produce a new, stable drop rarity
# (the encounter itself takes a second or two).
ATTACK_CONFIRM_TIMEOUT = 2.0
# How often to check while waiting for a manual (eldritch) item to be
# dismissed by hand.
MANUAL_POLL_INTERVAL = 0.3

# If the mouse ends up somewhere other than where the script itself last put
# it (i.e. you touch it) during the automated part of a cycle, stop rather
# than fight you for control. Not checked while waiting on a manual item --
# moving the mouse there is expected. Tolerance absorbs tiny reported jitter.
MOUSE_OVERRIDE_TOLERANCE = 3

# The icon is a ~45x45 solid-color square (with a small white item glyph
# drawn on top of it), so we scan for a square window that's mostly one
# rarity's color -- this also skips over stat-diff text (e.g. a green "+123")
# since text is thin/sparse and can't fill a window this densely. Kept
# smaller than the icon itself so a window fully inside it can reach ~100%
# density. Also, equipped/upgraded items show a black level badge in one
# corner of the icon -- a smaller window has more slack to land somewhere
# else inside the icon and dodge the badge entirely, rather than being
# forced to include a slice of it.
ICON_WINDOW = 30

# The glyph drawn on top of the rarity-colored background.
WHITE_COLOR = (255, 255, 255)
WHITE_TOLERANCE = 30

# A window counts as "the icon" for a given rarity only if enough of it is
# that rarity's color specifically (not just white) ...
MIN_RARITY_FRACTION = 0.4
# ... and combined (rarity color + white glyph) it's overwhelmingly two-tone,
# which real background art / UI chrome behind it won't be. Since the window
# (40px) fits entirely inside the icon (~45px), a good match can reach ~100%,
# but real captures land lower (anti-aliased edges, compression) -- observed
# ~0.85 for a true match vs. ~0.15-0.19 background noise, so keep this well
# below the former and well above the latter.
MIN_COMBINED_FRACTION = 0.75

# Pixel to sample for the health bar/indicator color.
HEALTH_POINT = (1953, 206)

# If the user is hovering over a non-UI element it will probably be close to
# this color (useful during calibration to confirm you're on/off a real
# element).
ERROR_COLOR = (19, 15, 35)

# Reference colors for simple health states.
HEALTH_COLORS = {
    "good": (129, 85, 200),
    "ok": (191, 145, 31),
    "low": (174, 48, 59),
}
HEALTH_TOLERANCE = 30

# -------------------------------------------------------


def color_distance(c1, c2):
    return sum(abs(a - b) for a, b in zip(c1, c2))


def classify_color(color, color_map, tolerance):
    """Return the label of the closest reference color within tolerance, else None."""
    best_label, best_dist = None, None
    for label, ref in color_map.items():
        dist = color_distance(color, ref)
        if dist <= tolerance * 3 and (best_dist is None or dist < best_dist):
            best_label, best_dist = label, dist
    return best_label


def get_average_color(region):
    """Average color of a region (more robust than a single pixel)."""
    img = ImageGrab.grab(bbox=region).convert("RGB")
    pixels = list(img.getdata())
    n = len(pixels)
    r = sum(p[0] for p in pixels) / n
    g = sum(p[1] for p in pixels) / n
    b = sum(p[2] for p in pixels) / n
    return (r, g, b)


def grab_region_array(region):
    """Region screenshot as an (H, W, 3) int32 array."""
    img = ImageGrab.grab(bbox=region).convert("RGB")
    return np.asarray(img, dtype=np.int32)


def color_mask(arr, color, tolerance):
    """Boolean (H, W) mask of pixels within tolerance of color."""
    diff = np.abs(arr - np.array(color, dtype=np.int32)).sum(axis=2)
    return diff <= tolerance * 3


def window_density(mask, size):
    """Fraction of True pixels in every size x size window, via a summed-area table."""
    h, w = mask.shape
    if h < size or w < size:
        return np.zeros((0, 0))
    integral = np.pad(mask.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    window_sum = (integral[size:, size:] - integral[:-size, size:]
                  - integral[size:, :-size] + integral[:-size, :-size])
    return window_sum / (size * size)


def icon_scores(region):
    """For each rarity, the best (rarity_density, combined_density) achieved anywhere
    in region -- regardless of whether it clears the classification thresholds.
    Used both by classify_icon() and by debug mode to show near-misses."""
    arr = grab_region_array(region)
    white_mask = color_mask(arr, WHITE_COLOR, WHITE_TOLERANCE)

    scores = {}
    for rarity, color in RARITY_COLORS.items():
        rarity_mask = color_mask(arr, color, RARITY_TOLERANCE)
        combined_mask = rarity_mask | white_mask

        rarity_density = window_density(rarity_mask, ICON_WINDOW)
        combined_density = window_density(combined_mask, ICON_WINDOW)
        if combined_density.size == 0:
            scores[rarity] = (0.0, 0.0)
            continue

        idx = np.unravel_index(np.argmax(combined_density), combined_density.shape)
        scores[rarity] = (float(rarity_density[idx]), float(combined_density[idx]))
    return scores


def classify_icon(region):
    """Find a rarity-colored icon anywhere inside region; return its rarity or None."""
    best_rarity, best_score = None, 0.0
    for rarity, (rarity_density, combined_density) in icon_scores(region).items():
        if rarity_density >= MIN_RARITY_FRACTION and combined_density >= MIN_COMBINED_FRACTION:
            if combined_density > best_score:
                best_rarity, best_score = rarity, combined_density
    return best_rarity


def timestamp():
    return datetime.now().strftime("%H:%M:%S")



class ManualOverride(Exception):
    """Raised when the mouse has moved somewhere the script didn't put it."""


# Where the script itself last positioned the mouse, so a human touching it
# can be told apart from our own moveTo() calls. None means "don't check yet"
# (nothing tapped this session, or we're deliberately yielding control).
_expected_mouse_pos = None


def check_mouse_untouched():
    if _expected_mouse_pos is None:
        return
    x, y = pyautogui.position()
    ex, ey = _expected_mouse_pos
    if abs(x - ex) > MOUSE_OVERRIDE_TOLERANCE or abs(y - ey) > MOUSE_OVERRIDE_TOLERANCE:
        raise ManualOverride()


def guarded_sleep(duration):
    """Sleep in small increments, checking for manual mouse movement throughout
    rather than just before/after -- so a mid-sleep grab is caught promptly."""
    deadline = time.time() + duration
    while True:
        check_mouse_untouched()
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(TAP_POLL_INTERVAL, remaining))


def tap(x, y):
    """Click in a way iPhone Mirroring reliably registers as a touch."""
    global _expected_mouse_pos
    pyautogui.moveTo(x, y)
    time.sleep(CLICK_SETTLE)
    pyautogui.mouseDown()
    time.sleep(CLICK_HOLD)
    pyautogui.mouseUp()
    _expected_mouse_pos = (x, y)


def tap_until(location, confirmed, description, timeout):
    """Tap location and poll confirmed() for its effect; retry the tap if it
    never shows up. confirmed() must return True for CONFIRM_POLLS polls
    in a row (not just once) before the tap counts as having landed."""
    for attempt in range(1, TAP_RETRY_ATTEMPTS + 1):
        tap(*location)
        deadline = time.time() + timeout
        streak = 0
        while time.time() < deadline:
            check_mouse_untouched()
            if confirmed():
                streak += 1
                if streak >= CONFIRM_POLLS:
                    return True
            else:
                streak = 0
            time.sleep(TAP_POLL_INTERVAL)
        print(f"[{timestamp()}] {description}: tap not confirmed (attempt {attempt}/{TAP_RETRY_ATTEMPTS})")
    print(f"[{timestamp()}] {description}: giving up after {TAP_RETRY_ATTEMPTS} attempts")
    return False


def tap_until_stable(location, sample, description, timeout):
    """Tap location, retrying if needed, until sample() returns the same non-None
    value CONFIRM_POLLS times in a row. Combines "confirm the tap did something"
    and "wait for a stable, non-flickering reading" into one poll instead of two
    sequential ones. Returns that value, or None if it never stabilizes."""
    for attempt in range(1, TAP_RETRY_ATTEMPTS + 1):
        tap(*location)
        deadline = time.time() + timeout
        candidate, streak = None, 0
        while time.time() < deadline:
            check_mouse_untouched()
            value = sample()
            if value is not None and value == candidate:
                streak += 1
                if streak >= CONFIRM_POLLS:
                    return value
            else:
                candidate, streak = value, (1 if value is not None else 0)
            time.sleep(TAP_POLL_INTERVAL)
        print(f"[{timestamp()}] {description}: tap not confirmed (attempt {attempt}/{TAP_RETRY_ATTEMPTS})")
    print(f"[{timestamp()}] {description}: giving up after {TAP_RETRY_ATTEMPTS} attempts")
    return None


def calibrate():
    print("Calibrate mode. Move your mouse over the iPhone Mirroring window.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            x, y = pyautogui.position()
            color = get_average_color((x, y, x + 1, y + 1))
            print(f"Position: ({x}, {y})   Color under cursor: "
                  f"({int(color[0])}, {int(color[1])}, {int(color[2])})")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


def debug_scan():
    print("Debug mode: best match density per rarity, for each region, every second.")
    print("Point the game at a visible drop/equipped comparison and watch the numbers.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            for label, region in (("drop", DROP_REGION), ("equipped", EQUIPPED_REGION)):
                scores = icon_scores(region)
                ranked = sorted(scores.items(), key=lambda kv: kv[1][1], reverse=True)
                parts = ", ".join(f"{r}(rarity={rd:.2f},combined={cd:.2f})" for r, (rd, cd) in ranked)
                result = classify_icon(region)
                print(f"[{timestamp()}] {label} classified={result or 'none'} -- {parts}")
            print()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


def print_status(health, inventory_used, total_drops, total_sold, total_salvaged,
                  drop_rarity, equipped_rarity, action):
    print(f"[{timestamp()}]")
    print(f"health: {health or 'unknown'}")
    print(f"inventory: {inventory_used}/{INVENTORY_CAPACITY}")
    print(f"total drops: {total_drops}")
    print(f"total sold: {total_sold}")
    print(f"total salvaged: {total_salvaged}")
    print(f"drop: {drop_rarity or 'none'}")
    print(f"equipped: {equipped_rarity or 'none'}")
    print(f"suggested action: {action or 'none'}")
    print()


def read_health():
    color = get_average_color(
        (HEALTH_POINT[0], HEALTH_POINT[1], HEALTH_POINT[0] + 1, HEALTH_POINT[1] + 1))
    return classify_color(color, HEALTH_COLORS, HEALTH_TOLERANCE)


def wait_for_manual_dismissal():
    # Handling this by hand means touching the mouse, which is expected here --
    # disarm the override check until the next tap re-establishes where we are.
    global _expected_mouse_pos
    _expected_mouse_pos = None
    print(f"[{timestamp()}] waiting for you to handle this one...")
    while classify_icon(DROP_REGION) is not None:
        time.sleep(MANUAL_POLL_INTERVAL)


def monitor():
    print("Monitoring. Press Ctrl+C to stop.\n")

    inventory_used = INVENTORY_USED
    total_drops = 0
    total_sold = 0
    total_salvaged = 0

    # Wherever we last tapped becomes the attack button once its box closes
    # (it replaces the whole button bar), so reuse that spot for the next
    # attack instead of moving back to a separate fixed location.
    attack_location = ATTACK_BUTTON_LOCATION

    try:
        while True:
            # 1. Attack, and don't move on until the drop's rarity is stable --
            # folds "confirm the tap landed" and "wait for a clean read" into
            # one poll instead of two sequential ones.
            drop_rarity = tap_until_stable(attack_location, lambda: classify_icon(DROP_REGION),
                                            "attack", ATTACK_CONFIRM_TIMEOUT)

            # 2. Equipped/health don't gate any decision, so a single read is
            # enough -- the box has already settled by the time drop_rarity
            # resolved above.
            equipped_rarity = classify_icon(EQUIPPED_REGION)
            health = read_health()

            total_drops += 1
            action = ACTIONS.get(drop_rarity)
            if action == "sell":
                total_sold += 1
            elif action == "salvage":
                total_salvaged += 1
            elif action == "stash":
                inventory_used += 1

            print_status(health, inventory_used, total_drops, total_sold, total_salvaged,
                         drop_rarity, equipped_rarity, action)

            # 3. Perform the action, if we have a button for it.
            if action in BUTTONS:
                tap_until(BUTTONS[action], lambda: classify_icon(DROP_REGION) is None,
                          f"{action} click", ACTION_CONFIRM_TIMEOUT)
                attack_location = BUTTONS[action]
            else:
                wait_for_manual_dismissal()
                attack_location = ATTACK_BUTTON_LOCATION

            # 4. We're confirmed out of combat (drop region reads none) at this
            # point either way -- loop back and attack again.
            guarded_sleep(ATTACK_DELAY)
    except ManualOverride:
        print(f"\n[{timestamp()}] Mouse moved manually -- stopping.")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    if mode == "calibrate":
        calibrate()
    elif mode == "monitor":
        monitor()
    elif mode == "debug":
        debug_scan()
    else:
        print("Usage: python3 main.py [calibrate|monitor|debug]")
