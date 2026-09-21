"""
Loot/health monitor and auto-triager for an iPhone-mirrored roguelike.

Watches configured screen regions and, once a dropped item's rarity is
confirmed, clicks the corresponding sell/salvage/stash button per ACTIONS.
"manual" rarities (currently eldritch) and unrecognized drops are never
clicked -- left for you to handle by hand.

See README.md for setup, calibration, .env format, and the available modes
(calibrate, auto, debug, debug-drops, timing).
"""

import os
import sys
import time
from datetime import datetime
import mss
import numpy as np
import pyautogui

# pyautogui silently sleeps this long after EVERY call (moveTo, mouseDown,
# mouseUp, etc.) by default -- 0.1s each, so tap()'s three calls would add
# 0.3s of hidden overhead per tap on top of our own explicit CLICK_SETTLE/
# CLICK_HOLD tuning, which already serves the same "let the click register"
# purpose. Disable it; we control click timing ourselves.
pyautogui.PAUSE = 0

# One persistent capture instance -- creating a fresh mss.mss() per call has
# its own setup overhead, so reuse it across every grab in the process.
_sct = mss.mss()

# Machine/session-specific settings (calibrated pixel locations, and the
# gameplay settings below that you might tune between sessions) live in a
# local .env file next to this script rather than as hardcoded constants
# here. No fallback defaults: these must be set in .env, since a wrong guess
# here fails silently and confusingly rather than erroring.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env():
    """Read .env as a flat dict of key -> raw string value."""
    if not os.path.exists(_ENV_PATH):
        return {}
    env = {}
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            env[key.strip()] = raw.strip()
    return env


def save_env(updates):
    """Merge updates into the existing .env (preserving unrelated keys) and
    write the result back."""
    env = load_env()
    env.update(updates)
    lines = ["# Managed by main.py -- calibrate writes pixel locations here;"
              " the rest can be hand-edited."]
    lines += [f"{key}={value}" for key, value in env.items()]
    with open(_ENV_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


_env = load_env()
_mode = sys.argv[1] if len(sys.argv) > 1 else "auto"


def required_raw(key, hint):
    """Fetch a raw string value from .env -- no fallback. If it's missing, tell
    the user how to add it and stop, unless calibrate is the thing currently
    running (it produces the pixel-location keys, so can't depend on them
    already being set)."""
    if key in _env:
        return _env[key]
    if _mode == "calibrate":
        return None
    print(f"{key} is not set in .env. {hint}")
    sys.exit(1)


def encode_point(value):
    return ",".join(str(n) for n in value)


def parse_point(raw):
    return None if raw is None else tuple(int(n) for n in raw.split(","))


def parse_int(raw):
    return None if raw is None else int(raw)


def parse_bool(raw):
    return None if raw is None else raw.strip().lower() == "true"


def encode_actions(actions):
    return ",".join(f"{rarity}:{action}" for rarity, action in actions.items())


def parse_actions(raw):
    if raw is None:
        return None
    actions = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        rarity, _, action = pair.partition(":")
        actions[rarity.strip()] = action.strip()
    return actions


CALIBRATE_HINT = "Run `python3 main.py calibrate` first."


def required_point(key):
    return parse_point(required_raw(key, CALIBRATE_HINT))


# ---------------- CONFIG (edit these) ----------------

# Reference RGB color for each rarity tier's icon background.
# Eldritch drops rarely, so its color is a guess (red) until one is seen.
RARITY_COLORS = {
    "crude": (78, 78, 78),
    "sturdy": (52, 125, 42),
    "enchanted": (26, 98, 204),
    "mythic": (99, 11, 149),
    "relic": (246, 178, 20),
    "eldritch": (155, 25, 33), # approximate
}

# Suggested triage per rarity. "manual" means: don't suggest anything, just
# surface it so it can be looked at directly (eldritch is rare enough to
# want eyes on it). Set in .env as e.g.
# ACTIONS=crude:sell,sturdy:salvage,enchanted:salvage,mythic:salvage,relic:stash,eldritch:manual
ACTIONS = parse_actions(required_raw(
    "ACTIONS", "Add it to .env, e.g. ACTIONS=crude:sell,sturdy:salvage,relic:stash,eldritch:manual"))

# Pixels to sample for the health bar/indicator color. See .env / calibrate.
HEALTH_POINT = required_point("HEALTH_POINT")
MAX_HEALTH_POINT = required_point("MAX_HEALTH_POINT")

# Inventory tracking is manual: set INVENTORY_USED in .env to whatever's
# actually in your inventory when you start the script. From there, every
# "stash" action increments it by 1 for the rest of the session.
INVENTORY_CAPACITY = parse_int(required_raw("INVENTORY_CAPACITY", "Add it to .env, e.g. INVENTORY_CAPACITY=5."))
INVENTORY_USED = parse_int(required_raw("INVENTORY_USED", "Add it to .env, e.g. INVENTORY_USED=0."))

# If True, attacking also requires the bar to be filled to MAX_HEALTH_POINT
# (near max), not just in the "good" tier overall. Set in .env as true/false.
REQUIRE_MAX_HEALTH = parse_bool(required_raw(
    "REQUIRE_MAX_HEALTH", "Add it to .env as REQUIRE_MAX_HEALTH=true or REQUIRE_MAX_HEALTH=false."))

# How close a sampled color needs to be to a reference color to count as a
# match (0 = exact only; higher = more tolerant of variation). Distance is
# summed absolute difference across R/G/B, so the effective threshold is
# roughly tolerance * 3.
RARITY_TOLERANCE = 25

# The item info box is anchored to the BOTTOM, so it grows upward as an item
# has more stats -- the icon's position shifts and isn't a fixed offset from
# either corner. These regions are the max bounds the box (and therefore the
# icon) can ever occupy; we search inside them rather than sampling a point.
DROP_REGION = required_point("DROP_REGION")
EQUIPPED_REGION = required_point("EQUIPPED_REGION")

# Max bounds the dialog close button's text can ever appear within (it's
# centered horizontally but shifts vertically with dialog height). See
# DIALOG_CLOSE_BUTTON_COLOR above / .env / calibrate.
DIALOG_CLOSE_REGION = required_point("DIALOG_CLOSE_REGION")

# See .env / calibrate.
SELL_BUTTON_LOCATION = required_point("SELL_BUTTON_LOCATION")
SALVAGE_BUTTON_LOCATION = required_point("SALVAGE_BUTTON_LOCATION")
STASH_BUTTON_LOCATION = required_point("STASH_BUTTON_LOCATION")

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

# The bottom tab bar icons are bright/white when ready to attack and dimmed
# when an item has dropped -- an unambiguous, rarity-independent signal for
# which phase the game is in, used to gate attack/action taps instead of
# trying to infer game phase from the item box's own (rarity-dependent)
# colors. See .env / calibrate.
TAB_BAR_POINT = required_point("TAB_BAR_POINT")
TAB_BAR_COLORS = {
    "ready": parse_point(required_raw("TAB_BAR_READY_COLOR", CALIBRATE_HINT)),
    "dropped": parse_point(required_raw("TAB_BAR_DROPPED_COLOR", CALIBRATE_HINT)),
}
TAB_BAR_TOLERANCE = 30

# How long to wait after clicking sell/salvage/stash before attacking again.
# The attack tap reuses the SAME pixel coordinates as the action button, so
# this is a real safety margin, not just a formality: if it's too short, the
# next tap can land while the old box is still mid-close-animation, or (worse)
# while a new item's box is already appearing at that same spot -- meaning
# "attack" could actually hit that new item's sell/salvage/stash button
# before its rarity's even been read. Don't cut this much further.
ATTACK_DELAY = 0.05

# iPhone Mirroring translates mouse input into touches, and an instant
# move-then-click (near-zero dwell/press time) doesn't always register as a
# tap. CLICK_SETTLE lets the cursor "arrive" before pressing down;
# CLICK_HOLD is how long to hold the press before releasing. These are the
# main lever if taps start getting silently dropped again -- raise these
# two specifically before touching anything else below.
CLICK_SETTLE = 0.03
CLICK_HOLD = 0.05

# Tuning the tap itself only goes so far -- taps still get silently dropped
# sometimes. Instead of hoping the timing is exactly right, verify each tap
# actually did something (via our own drop detection) and retry if it
# didn't, rather than blindly moving on.
TAP_RETRY_ATTEMPTS = 3
# The gap between polls. Together with CONFIRM_POLLS below, this sets how
# long something has to stay stable before it's trusted -- e.g. with the
# values below, "confirmed" requires ~2 consecutive intervals (0.12s) of the
# same reading, comfortably longer than a single flicker frame. Don't cut
# this much further: at very small values "confirmed" can trigger off a
# single transient animation frame instead of genuine stability.
TAP_POLL_INTERVAL = 0.06
# How many consecutive polls must agree before something is considered
# confirmed (guards against a single-frame flicker looking like the truth).
CONFIRM_POLLS = 1
# How long to wait for a sell/salvage/stash tap to close the item box.
ACTION_CONFIRM_TIMEOUT = 1.0
# How long to wait for an attack tap to flip the tab bar to "dropped".
ATTACK_CONFIRM_TIMEOUT = 2.0
# The tab bar dims as soon as combat starts, but the item box itself doesn't
# render until combat finishes (up to ~1s later) -- so right after the tab
# bar confirms "dropped", the drop/equipped regions can still briefly read as
# empty. Keep retrying the rarity read until both resolve, up to this long,
# rather than trusting a single read.
RARITY_READ_TIMEOUT = 1.5
# How often to check while waiting for a manual (eldritch) item to be
# dismissed by hand.
MANUAL_POLL_INTERVAL = 0.3

# Attack only when health reads "good" (the highest tier) -- "ok" and "low"
# both pause it. How often to re-check health while paused.
HEALTH_POLL_INTERVAL = 0.5

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


# If the user is hovering over a non-UI element it will probably be close to
# this color (useful during calibration to confirm you're on/off a real
# element).
ERROR_COLOR = (19, 15, 35)

# Reference colors for simple health states.
HEALTH_COLORS = {
    "good": (169, 109, 255),
    "ok": (252, 190, 12),
    "low": (227, 58, 64),
}
HEALTH_TOLERANCE = 30

# An open dialog dims everything behind it, so HEALTH_POINT reads as roughly
# 43-45% of its normal "good" brightness -- a much more reliable signal than
# trying to tell two similar dark background colors apart (that difference
# was only ~27 total vs. this one's ~300). Checked separately from the
# normal good/ok/low tiers since "dialog open" isn't a health state.
DIALOG_HEALTH_COLOR = (75, 49, 109)
DIALOG_TOLERANCE = 20

# The dialog's close button isn't at a fixed position -- it's horizontally
# centered but shifts vertically with the dialog's height, so we can't
# calibrate a single point like the other buttons. Instead, DIALOG_CLOSE_REGION
# (see .env / calibrate) is the max bounds the button can ever appear within,
# and we search it for this purple text color. Unlike the solid rarity icons,
# text is thin/sparse, so a density-window match (which specifically rejects
# sparse patterns) won't find it -- instead we average the position of every
# matching pixel in the region.
DIALOG_CLOSE_BUTTON_COLOR = (155, 103, 235)
DIALOG_CLOSE_TOLERANCE = 30
# Minimum matching pixels in the region before trusting it's really the
# button (vs. stray noise/anti-aliasing) rather than the dialog being closed.
DIALOG_CLOSE_MIN_PIXELS = 20

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


def grab_region_array(region):
    """Region screenshot as an (H, W, 3) int32 array."""
    left, top, right, bottom = region
    shot = _sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
    arr = np.frombuffer(shot.rgb, dtype=np.uint8).reshape(shot.height, shot.width, 3)
    return arr.astype(np.int32)


def get_average_color(region):
    """Average color of a region (more robust than a single pixel)."""
    arr = grab_region_array(region)
    return tuple(arr.reshape(-1, 3).mean(axis=0))


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


def tap_until(location, confirmed, description, timeout, quiet=False, stats=None):
    """Tap location and poll confirmed() for its effect; retry the tap if it
    never shows up. confirmed() must return True for CONFIRM_POLLS polls
    in a row (not just once) before the tap counts as having landed. quiet
    suppresses the per-attempt logging (used by live mode). If stats is a
    dict, records "polls" (total confirmed() calls) and "attempts" -- lets
    callers tell "needed many polls" (e.g. a slow animation) apart from
    "each poll was slow" (capture overhead)."""
    total_polls = 0
    for attempt in range(1, TAP_RETRY_ATTEMPTS + 1):
        if not quiet:
            print(f"[{timestamp()}] tapping {description} at {location} (attempt {attempt}/{TAP_RETRY_ATTEMPTS})")
        tap(*location)
        deadline = time.time() + timeout
        streak = 0
        while time.time() < deadline:
            check_mouse_untouched()
            total_polls += 1
            if confirmed():
                streak += 1
                if streak >= CONFIRM_POLLS:
                    if stats is not None:
                        stats["polls"] = total_polls
                        stats["attempts"] = attempt
                    return True
            else:
                streak = 0
            time.sleep(TAP_POLL_INTERVAL)
        if not quiet:
            print(f"[{timestamp()}] {description}: tap not confirmed (attempt {attempt}/{TAP_RETRY_ATTEMPTS})")
    if stats is not None:
        stats["polls"] = total_polls
        stats["attempts"] = TAP_RETRY_ATTEMPTS
    if not quiet:
        print(f"[{timestamp()}] {description}: giving up after {TAP_RETRY_ATTEMPTS} attempts")
    return False


def tap_until_state(location, expected_state, description, timeout, quiet=False, stats=None):
    """Tap location until the tab bar reports expected_state ("ready" or
    "dropped") -- an unambiguous, rarity-independent game-state signal, used
    instead of trying to infer game phase from the item box's own colors."""
    return tap_until(location, lambda: read_tab_bar_state() == expected_state, description, timeout,
                      quiet=quiet, stats=stats)


def debug():
    print("Debug mode. Move your mouse over the iPhone Mirroring window.")
    print("Also shows live dialog-open detection (checked at HEALTH_POINT) and")
    print("the detected close button position (searched in DIALOG_CLOSE_REGION),")
    print("regardless of where the cursor is.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            x, y = pyautogui.position()
            color = get_average_color((x, y, x + 1, y + 1))
            print(f"Position: ({x}, {y})   Color under cursor: "
                  f"({int(color[0])}, {int(color[1])}, {int(color[2])})   "
                  f"dialog open: {is_dialog_open()}   "
                  f"close button: {find_dialog_close_button()}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


def wait_for_click():
    """Block until the next real mouse click anywhere on screen; return its (x, y)."""
    from pynput import mouse

    clicked_at = {}

    def on_click(x, y, button, pressed):
        if pressed:
            clicked_at["pos"] = (int(x), int(y))
            return False  # stop the listener

    with mouse.Listener(on_click=on_click) as listener:
        listener.join()
    return clicked_at["pos"]


def wait_for_click_or_skip(show_live_color=False):
    """Block until either a real mouse click or the space bar. Returns
    ("click", (x, y)) or ("skip", None) -- used so an already-calibrated
    point can be kept without re-clicking it.

    If show_live_color, prints the color under the cursor as it moves, like a
    live color picker, updating the same terminal line in place (\\r, no
    newline) instead of flooding the console with one line per position."""
    from pynput import keyboard, mouse

    result = {}

    def on_click(x, y, button, pressed):
        if pressed and "type" not in result:
            result["type"] = "click"
            result["pos"] = (int(x), int(y))
            return False

    def on_press(key):
        if key == keyboard.Key.space and "type" not in result:
            result["type"] = "skip"
            return False

    mouse_listener = mouse.Listener(on_click=on_click)
    keyboard_listener = keyboard.Listener(on_press=on_press)
    mouse_listener.start()
    keyboard_listener.start()
    while "type" not in result:
        if show_live_color:
            x, y = pyautogui.position()
            color = get_average_color((x, y, x + 1, y + 1))
            rgb = tuple(int(c) for c in color)
            print(f"\r  live: ({x}, {y}) color={rgb}" + " " * 10, end="", flush=True)
        time.sleep(0.02)
    if show_live_color:
        print()  # leave the last live reading in place, move to a fresh line
    mouse_listener.stop()
    keyboard_listener.stop()
    return result["type"], result.get("pos")


def calibrate():
    """Interactive calibration: click each requested spot in turn to build up
    HEALTH_POINT, MAX_HEALTH_POINT, DROP_REGION, EQUIPPED_REGION, the
    sell/salvage/stash button locations, the tab bar ready/dropped colors,
    and the dialog close button's search region. Rarity colors aren't
    covered here -- use debug for those."""
    print("Interactive calibration. Click each requested spot when prompted.")
    print("If a spot is already set in .env, press Space to keep it as-is.")
    print("If clicking does nothing, grant Input Monitoring permission to this")
    print("terminal/IDE under System Settings > Privacy & Security.\n")

    def existing_point(key):
        return parse_point(_env[key]) if key in _env else None

    def prompt_point(label, current=None):
        if current is not None:
            print(f"Click {label}  (already set to {current} -- press Space to keep it)...")
            kind, pos = wait_for_click_or_skip(show_live_color=True)
            if kind == "skip":
                print(f"  -> kept {current}\n")
                return current
        else:
            print(f"Click {label}...")
            kind, pos = wait_for_click_or_skip(show_live_color=True)
        x, y = pos
        print(f"  -> ({x}, {y})\n")
        return (x, y)

    def prompt_color_point(label, current_point=None, current_color=None):
        if current_point is not None and current_color is not None:
            print(f"Click {label}  (already set: point={current_point} color={current_color} "
                  f"-- press Space to keep it)...")
            kind, pos = wait_for_click_or_skip(show_live_color=True)
            if kind == "skip":
                print(f"  -> kept point={current_point} color={current_color}\n")
                return current_point, current_color
        else:
            print(f"Click {label}...")
            kind, pos = wait_for_click_or_skip(show_live_color=True)
        x, y = pos
        color = get_average_color((x, y, x + 1, y + 1))
        clicked = tuple(int(c) for c in color)
        print(f"  -> ({x}, {y})  color={clicked}\n")
        return (x, y), clicked

    def prompt_health_point(label, current=None):
        while True:
            if current is not None:
                print(f"Click {label}  (already set to {current} -- press Space to keep it)...")
                kind, pos = wait_for_click_or_skip(show_live_color=True)
                if kind == "skip":
                    print(f"  -> kept {current}\n")
                    return current
            else:
                print(f"Click {label}...")
                kind, pos = wait_for_click_or_skip(show_live_color=True)
            x, y = pos
            color = get_average_color((x, y, x + 1, y + 1))
            clicked = tuple(int(c) for c in color)
            expected = HEALTH_COLORS["good"]
            matches = color_distance(clicked, expected) <= HEALTH_TOLERANCE * 3
            print(f"  -> ({x}, {y})  clicked={clicked}  expected(good)={expected}  "
                  f"{'MATCH' if matches else 'NO MATCH -- try again'}\n")
            if matches:
                return (x, y)

    health_point = prompt_health_point("the HEALTH point (main health bar)", existing_point("HEALTH_POINT"))
    max_health_point = prompt_health_point(
        "the MAX HEALTH checkpoint (~95% mark on the health bar)", existing_point("MAX_HEALTH_POINT"))

    existing_drop_region = existing_point("DROP_REGION")
    drop_tl = prompt_point("the DROP region TOP-LEFT corner",
                            existing_drop_region[:2] if existing_drop_region else None)
    drop_br = prompt_point("the DROP region BOTTOM-RIGHT corner",
                            existing_drop_region[2:] if existing_drop_region else None)

    existing_equipped_region = existing_point("EQUIPPED_REGION")
    equipped_tl = prompt_point("the EQUIPPED region TOP-LEFT corner",
                                existing_equipped_region[:2] if existing_equipped_region else None)
    equipped_br = prompt_point("the EQUIPPED region BOTTOM-RIGHT corner",
                                existing_equipped_region[2:] if existing_equipped_region else None)

    sell_button = prompt_point("the SELL button", existing_point("SELL_BUTTON_LOCATION"))
    salvage_button = prompt_point("the SALVAGE button", existing_point("SALVAGE_BUTTON_LOCATION"))
    stash_button = prompt_point("the STASH button", existing_point("STASH_BUTTON_LOCATION"))

    print("Now the bottom tab bar -- used to tell 'ready to attack' apart from")
    print("'item dropped' unambiguously, independent of item rarity. Click the")
    print("SAME spot on the tab bar for both of the following.\n")
    tab_bar_point, tab_bar_ready_color = prompt_color_point(
        "a tab bar icon while READY TO ATTACK (icons bright/white)",
        existing_point("TAB_BAR_POINT"), existing_point("TAB_BAR_READY_COLOR"))
    _, tab_bar_dropped_color = prompt_color_point(
        "the SAME tab bar icon while an ITEM IS DROPPED (icons dimmed)",
        tab_bar_point, existing_point("TAB_BAR_DROPPED_COLOR"))

    print("Now the dialog close button region. It's horizontally centered but")
    print("shifts vertically with dialog height, so this needs the max bounds it")
    print("could ever appear within -- open the SMALLEST dialog you can for the")
    print("top-left corner, and the LARGEST/tallest dialog you can for the")
    print("bottom-right corner.\n")
    existing_dialog_close_region = existing_point("DIALOG_CLOSE_REGION")
    dialog_close_tl = prompt_point(
        "the DIALOG CLOSE button region TOP-LEFT corner (smallest dialog)",
        existing_dialog_close_region[:2] if existing_dialog_close_region else None)
    dialog_close_br = prompt_point(
        "the DIALOG CLOSE button region BOTTOM-RIGHT corner (largest dialog)",
        existing_dialog_close_region[2:] if existing_dialog_close_region else None)

    save_env({
        "HEALTH_POINT": encode_point(health_point),
        "MAX_HEALTH_POINT": encode_point(max_health_point),
        "DROP_REGION": encode_point((drop_tl[0], drop_tl[1], drop_br[0], drop_br[1])),
        "EQUIPPED_REGION": encode_point((equipped_tl[0], equipped_tl[1], equipped_br[0], equipped_br[1])),
        "SELL_BUTTON_LOCATION": encode_point(sell_button),
        "SALVAGE_BUTTON_LOCATION": encode_point(salvage_button),
        "STASH_BUTTON_LOCATION": encode_point(stash_button),
        "TAB_BAR_POINT": encode_point(tab_bar_point),
        "TAB_BAR_READY_COLOR": encode_point(tab_bar_ready_color),
        "TAB_BAR_DROPPED_COLOR": encode_point(tab_bar_dropped_color),
        "DIALOG_CLOSE_REGION": encode_point(
            (dialog_close_tl[0], dialog_close_tl[1], dialog_close_br[0], dialog_close_br[1])),
    })
    print(f"Saved to {_ENV_PATH}")


def debug_drops():
    print("Debug-drops mode: best match density per rarity, for each region, every second.")
    print("Point the game at a visible drop/equipped comparison and watch the numbers.")
    print("Also shows the live tab bar reading -- toggle between ready/dropped in-game")
    print("and watch whether it actually classifies correctly and how close each")
    print("distance is to the TAB_BAR_TOLERANCE*3 cutoff.")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            for label, region in (("drop", DROP_REGION), ("equipped", EQUIPPED_REGION)):
                scores = icon_scores(region)
                ranked = sorted(scores.items(), key=lambda kv: kv[1][1], reverse=True)
                parts = ", ".join(f"{r}(rarity={rd:.2f},combined={cd:.2f})" for r, (rd, cd) in ranked)
                result = classify_icon(region)
                print(f"[{timestamp()}] {label} classified={result or 'none'} -- {parts}")

            tab_bar_color = get_average_color(
                (TAB_BAR_POINT[0], TAB_BAR_POINT[1], TAB_BAR_POINT[0] + 1, TAB_BAR_POINT[1] + 1))
            tab_bar_clicked = tuple(int(c) for c in tab_bar_color)
            dist_ready = color_distance(tab_bar_clicked, TAB_BAR_COLORS["ready"])
            dist_dropped = color_distance(tab_bar_clicked, TAB_BAR_COLORS["dropped"])
            cutoff = TAB_BAR_TOLERANCE * 3
            state = read_tab_bar_state()
            print(f"[{timestamp()}] tab bar classified={state or 'none'} -- color={tab_bar_clicked} "
                  f"dist_to_ready={dist_ready}{'(within cutoff)' if dist_ready <= cutoff else ''} "
                  f"dist_to_dropped={dist_dropped}{'(within cutoff)' if dist_dropped <= cutoff else ''} "
                  f"cutoff={cutoff}")
            print()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")


def benchmark():
    """Measure how long the actual screen-capture calls take on this machine, so
    the fixed sleep constants aren't the only known part of the cycle-time budget."""
    samples = 15

    def time_calls(label, fn):
        times = []
        for _ in range(samples):
            start = time.time()
            fn()
            times.append(time.time() - start)
        avg = sum(times) / len(times)
        print(f"{label}: avg={avg * 1000:.1f}ms  min={min(times) * 1000:.1f}ms  max={max(times) * 1000:.1f}ms")
        return avg

    print(f"Timing {samples} samples each (point the game at whatever's normally on "
          f"screen -- content doesn't affect capture cost)...\n")
    tab_bar_avg = time_calls("tab bar read_tab_bar_state()", read_tab_bar_state)
    drop_avg = time_calls("drop region classify_icon()", lambda: classify_icon(DROP_REGION))
    equipped_avg = time_calls("equipped region classify_icon()", lambda: classify_icon(EQUIPPED_REGION))
    health_avg = time_calls("health read_health()", read_health)

    # Per cycle (best case, no retries): CONFIRM_POLLS tab-bar captures each
    # for the attack and action confirmations, one single-shot drop rarity
    # read, one equipped read, two health reads (wait_for_healthy + the
    # post-attack re-read).
    captures = 2 * CONFIRM_POLLS * tab_bar_avg + drop_avg + equipped_avg + 2 * health_avg
    # Two taps (attack + action), each needing CONFIRM_POLLS-1 poll intervals
    # between confirmations, plus the settle margin after the action tap.
    fixed_sleeps = 2 * (CLICK_SETTLE + CLICK_HOLD) + 2 * (CONFIRM_POLLS - 1) * TAP_POLL_INTERVAL + ATTACK_DELAY

    print(f"\nFixed sleeps per cycle (from config constants): {fixed_sleeps:.3f}s")
    print(f"Measured screen-capture time per cycle "
          f"({2 * CONFIRM_POLLS} tab bar + 1 drop + 1 equipped + 2 health): {captures:.3f}s")
    print(f"Estimated best-case cycle time: {fixed_sleeps + captures:.3f}s")


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


def print_status_inline(health, inventory_used, total_drops, total_sold, total_salvaged,
                         drop_rarity, equipped_rarity, action, first):
    """Same fields as print_status, but rewrites the same block in place (ANSI
    cursor-up + clear-line) instead of appending a new one each cycle -- used
    by live mode so watching timing doesn't flood the console."""
    lines = [
        f"[{timestamp()}]",
        f"health: {health or 'unknown'}",
        f"inventory: {inventory_used}/{INVENTORY_CAPACITY}",
        f"total drops: {total_drops}",
        f"total sold: {total_sold}",
        f"total salvaged: {total_salvaged}",
        f"drop: {drop_rarity or 'none'}",
        f"equipped: {equipped_rarity or 'none'}",
        f"suggested action: {action or 'none'}",
    ]
    if not first:
        sys.stdout.write(f"\033[{len(lines)}A")
    for line in lines:
        sys.stdout.write("\033[K" + line + "\n")
    sys.stdout.flush()


def read_health():
    color = get_average_color(
        (HEALTH_POINT[0], HEALTH_POINT[1], HEALTH_POINT[0] + 1, HEALTH_POINT[1] + 1))
    return classify_color(color, HEALTH_COLORS, HEALTH_TOLERANCE)


def is_dialog_open():
    """True if HEALTH_POINT currently reads as dimmed the way it does when a
    dialog is covering the screen."""
    color = get_average_color(
        (HEALTH_POINT[0], HEALTH_POINT[1], HEALTH_POINT[0] + 1, HEALTH_POINT[1] + 1))
    return color_distance(color, DIALOG_HEALTH_COLOR) <= DIALOG_TOLERANCE * 3


def find_dialog_close_button():
    """Search DIALOG_CLOSE_REGION for the close button's purple text and
    return its approximate screen coordinates (the centroid of all matching
    pixels), or None if not enough matching pixels were found. The button
    isn't at a fixed position, so this searches rather than sampling a point;
    and since text is sparse (unlike the solid rarity icons), this averages
    matching pixel positions rather than looking for a dense window."""
    arr = grab_region_array(DIALOG_CLOSE_REGION)
    mask = color_mask(arr, DIALOG_CLOSE_BUTTON_COLOR, DIALOG_CLOSE_TOLERANCE)
    ys, xs = np.nonzero(mask)
    if len(xs) < DIALOG_CLOSE_MIN_PIXELS:
        return None
    left, top = DIALOG_CLOSE_REGION[0], DIALOG_CLOSE_REGION[1]
    return (left + int(xs.mean()), top + int(ys.mean()))


def close_dialog(quiet=False):
    """Tap the detected close button, confirmed via is_dialog_open() clearing.
    Returns False without tapping if the button couldn't be found."""
    button = find_dialog_close_button()
    if button is None:
        if not quiet:
            print(f"[{timestamp()}] dialog detected but close button not found -- can't auto-close.")
        return False
    return tap_until(button, lambda: not is_dialog_open(), "dialog close", ACTION_CONFIRM_TIMEOUT, quiet=quiet)


def handle_dialogs(quiet=False, max_dialogs=3):
    """Close any open dialogs (there could be more than one stacked), up to
    max_dialogs. Returns how many were actually closed."""
    closed = 0
    while is_dialog_open() and closed < max_dialogs:
        if not close_dialog(quiet=quiet):
            break
        closed += 1
    return closed


def read_tab_bar_state():
    """'ready' (bright, ok to attack), 'dropped' (dimmed, item showing), or
    None if neither matches."""
    color = get_average_color(
        (TAB_BAR_POINT[0], TAB_BAR_POINT[1], TAB_BAR_POINT[0] + 1, TAB_BAR_POINT[1] + 1))
    return classify_color(color, TAB_BAR_COLORS, TAB_BAR_TOLERANCE)


def read_drop_and_equipped():
    """Poll drop/equipped rarity until both resolve. The tab bar confirming
    "dropped" means combat has started, not that the item box has finished
    rendering, so a single read right after can still legitimately see an
    empty box for up to ~1s."""
    deadline = time.time() + RARITY_READ_TIMEOUT
    drop_rarity = equipped_rarity = None
    while time.time() < deadline:
        drop_rarity = classify_icon(DROP_REGION)
        equipped_rarity = classify_icon(EQUIPPED_REGION)
        if drop_rarity is not None and equipped_rarity is not None:
            return drop_rarity, equipped_rarity
        time.sleep(TAP_POLL_INTERVAL)
    return drop_rarity, equipped_rarity


def is_near_max_health():
    """True if the health bar is filled out to the MAX_HEALTH_POINT checkpoint."""
    color = get_average_color(
        (MAX_HEALTH_POINT[0], MAX_HEALTH_POINT[1], MAX_HEALTH_POINT[0] + 1, MAX_HEALTH_POINT[1] + 1))
    return classify_color(color, HEALTH_COLORS, HEALTH_TOLERANCE) == "good"


def wait_for_manual_dismissal(quiet=False):
    # Handling this by hand means touching the mouse, which is expected here --
    # disarm the override check until the next tap re-establishes where we are.
    global _expected_mouse_pos
    _expected_mouse_pos = None
    if not quiet:
        print(f"[{timestamp()}] waiting for you to handle this one...")
    while read_tab_bar_state() != "ready":
        time.sleep(MANUAL_POLL_INTERVAL)


def is_healthy_enough():
    if read_health() != "good":
        return False
    return not REQUIRE_MAX_HEALTH or is_near_max_health()


def wait_for_healthy(quiet=False):
    """Pause attacking until health is "good" (and, if REQUIRE_MAX_HEALTH, also
    filled to the near-max checkpoint). You may need to heal by hand while this
    waits, so the mouse-override check is disarmed meanwhile."""
    global _expected_mouse_pos
    if is_healthy_enough():
        return
    _expected_mouse_pos = None
    if not quiet:
        print(f"[{timestamp()}] health not high enough yet, waiting before attacking...")
    while not is_healthy_enough():
        time.sleep(HEALTH_POLL_INTERVAL)
    if not quiet:
        print(f"[{timestamp()}] health is high enough, resuming.")


def read_single_key():
    """Read one raw keypress from stdin without waiting for Enter (macOS/Unix)."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


ESCAPE_KEY = "\x1b"


def confirm_settings():
    """Show the settings that came from .env and let the user bail out to
    edit them by hand before anything starts clicking."""
    print("Current settings (from .env):")
    print(f"  actions: {ACTIONS}")
    print(f"  inventory: {INVENTORY_USED}/{INVENTORY_CAPACITY}")
    print(f"  require max health: {REQUIRE_MAX_HEALTH}")
    print()
    print("Press Escape to cancel and edit .env yourself, or any other key to continue...")
    return read_single_key() != ESCAPE_KEY


def wait_for_tab_bar(expected_state, quiet=False):
    """Passively wait for the tab bar to reach expected_state, without tapping
    anything -- used by live's dry-run mode, where you're doing the clicking
    and this is just watching to verify detection matches what you're seeing."""
    if not quiet:
        print(f"[{timestamp()}] waiting for tab bar to read '{expected_state}'...")
    while read_tab_bar_state() != expected_state:
        time.sleep(TAP_POLL_INTERVAL)


def run_cycle_loop(display, quiet, dry_run=False, show_timing=False, auto_close_dialogs=False):
    """Core attack/read/act loop shared by auto and live -- they differ only in
    how each cycle's status is displayed and whether per-tap logging is shown.
    display(health, inventory_used, total_drops, total_sold, total_salvaged,
    drop_rarity, equipped_rarity, action) is called once per cycle.

    If dry_run, never taps anything: waits for you to attack/act yourself and
    just verifies detection tracks what actually happens, via the same tab
    bar signal. inventory/sold/salvaged still track what the suggested
    action WOULD have done, for comparing against what you actually did.
    auto_close_dialogs is ignored in dry_run, since closing a dialog is
    itself a click.

    If show_timing, prints how long each step of the cycle actually took --
    real timing from a live run, not the synthetic capture-only numbers from
    `timing` mode, so it also reflects retries, confirmation waits, etc."""
    inventory_used = INVENTORY_USED
    total_drops = 0
    total_sold = 0
    total_salvaged = 0

    # Wherever we last tapped becomes the attack button once its box closes
    # (it replaces the whole button bar), so reuse that spot for the next
    # attack instead of moving back to a separate fixed location.
    attack_location = ATTACK_BUTTON_LOCATION

    while True:
        cycle_start = time.time()

        # 0. Close any dialog before doing anything else -- health/tab bar/
        # rarity all read as dimmed nonsense while one's covering the screen.
        step_start = time.time()
        if auto_close_dialogs and not dry_run:
            handle_dialogs(quiet=quiet)
        t_dialog = time.time() - step_start

        # 1. Don't attack unless health is ok.
        step_start = time.time()
        wait_for_healthy(quiet=quiet)
        t_healthy = time.time() - step_start

        # 2. Attack, confirmed via the tab bar dimming -- an unambiguous
        # signal that a drop genuinely happened, decoupled from having to
        # correctly classify the item's rarity to know the state changed.
        step_start = time.time()
        attack_stats = {} if show_timing else None
        if dry_run:
            wait_for_tab_bar("dropped", quiet=quiet)
        else:
            tap_until_state(attack_location, "dropped", "attack", ATTACK_CONFIRM_TIMEOUT,
                             quiet=quiet, stats=attack_stats)
        t_attack = time.time() - step_start

        # 3. The tab bar only confirms combat has started, not that the
        # item box has finished rendering -- keep retrying until both
        # drop and equipped resolve.
        step_start = time.time()
        drop_rarity, equipped_rarity = read_drop_and_equipped()
        health = read_health()
        t_read = time.time() - step_start

        total_drops += 1
        action = ACTIONS.get(drop_rarity)
        if action == "sell":
            total_sold += 1
        elif action == "salvage":
            total_salvaged += 1
        elif action == "stash":
            inventory_used += 1

        display(health, inventory_used, total_drops, total_sold, total_salvaged,
                drop_rarity, equipped_rarity, action)

        # 4. Perform the action, if we have a button for it, confirmed the
        # same way -- tab bar back to "ready". If the tap never gets
        # confirmed, don't loop back into "attack": the box is very
        # likely still open with this same undismissed item, and the next
        # attack tap would just re-hit that button on the wrong item.
        # Pause instead.
        step_start = time.time()
        action_stats = {} if show_timing else None
        if dry_run:
            wait_for_tab_bar("ready", quiet=quiet)
        elif action in BUTTONS:
            action_confirmed = tap_until_state(BUTTONS[action], "ready", f"{action} click",
                                                ACTION_CONFIRM_TIMEOUT, quiet=quiet, stats=action_stats)
            if action_confirmed:
                attack_location = BUTTONS[action]
            else:
                if not quiet:
                    print(f"[{timestamp()}] {action} click never registered -- "
                          f"pausing so you can check this by hand.")
                wait_for_manual_dismissal(quiet=quiet)
                attack_location = ATTACK_BUTTON_LOCATION
        else:
            wait_for_manual_dismissal(quiet=quiet)
            attack_location = ATTACK_BUTTON_LOCATION
        t_action = time.time() - step_start

        # 5. We're confirmed out of combat (drop region reads none) at this
        # point either way -- loop back and check health/attack again. No
        # click just happened in dry_run, so there's nothing to buffer for.
        step_start = time.time()
        if not dry_run:
            guarded_sleep(ATTACK_DELAY)
        t_delay = time.time() - step_start

        if show_timing:
            attack_polls = attack_stats.get("polls", "?") if attack_stats else "n/a"
            action_polls = action_stats.get("polls", "?") if action_stats else "n/a"
            print(f"[{timestamp()}] cycle timing: dialog={t_dialog:.2f}s healthy={t_healthy:.2f}s "
                  f"attack={t_attack:.2f}s({attack_polls} polls) "
                  f"read={t_read:.2f}s action={t_action:.2f}s({action_polls} polls) "
                  f"delay={t_delay:.2f}s total={time.time() - cycle_start:.2f}s")


def auto(show_timing=False, close_dialogs=False):
    if not confirm_settings():
        print("Cancelled.")
        return

    print("\nMonitoring. Press Ctrl+C to stop.\n")

    try:
        run_cycle_loop(display=print_status, quiet=False, show_timing=show_timing,
                        auto_close_dialogs=close_dialogs)
    except ManualOverride:
        print(f"\n[{timestamp()}] Mouse moved manually -- stopping.")
    except KeyboardInterrupt:
        print("\nStopped.")


def live(dry_run=False, close_dialogs=False):
    """Same as auto, but the status block updates in place (no per-tap logging,
    no scrolling) so you can watch real cycle timing without flooding the
    console -- meant for tightening up the timing constants.

    If dry_run, never taps anything: you drive (attack/sell/salvage/stash
    yourself) and this just verifies the detected state matches what you'd
    expect, which is safe to run without wiring up real clicks first."""
    if not confirm_settings():
        print("Cancelled.")
        return

    label = "Live mode (--no-click, you drive)" if dry_run else "Live mode"
    print(f"{label}. Press Ctrl+C to stop.\n")

    state = {"first": True}

    def display(*args):
        print_status_inline(*args, first=state["first"])
        state["first"] = False

    try:
        run_cycle_loop(display=display, quiet=True, dry_run=dry_run, auto_close_dialogs=close_dialogs)
    except ManualOverride:
        print(f"\n[{timestamp()}] Mouse moved manually -- stopping.")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    flags = sys.argv[2:]
    if mode == "calibrate":
        calibrate()
    elif mode == "auto":
        auto(show_timing="--timing" in flags, close_dialogs="--close-dialogs" in flags)
    elif mode == "live":
        live(dry_run="--no-click" in flags, close_dialogs="--close-dialogs" in flags)
    elif mode == "debug":
        debug()
    elif mode == "debug-drops":
        debug_drops()
    elif mode == "timing":
        benchmark()
    else:
        print("Usage: python3 main.py [calibrate|auto [--timing] [--close-dialogs]|"
              "live [--no-click] [--close-dialogs]|debug|debug-drops|timing]")
