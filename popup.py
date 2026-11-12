"""
JARVIS pop-up — a quick-entry bar you can open from any app, by text or voice.

    .venv/bin/python popup.py

Layout (bottom-center of the screen):
  ┌─────────────────────────────────────────────────────────────┐
  │ ◉  What can I help you with today?                     [↑]  │
  │    …JARVIS's answer appears here, and the bar grows up…     │
  ├─────────────────────────────────────────────────────────────┤
  │ Listening…                                   ( ✕ ıııııı ✓ ) │
  │ Double-tap ⌘ any time · Esc to close                        │
  └─────────────────────────────────────────────────────────────┘

  · Double-tap Command (JARVIS_HOTKEY) or click ◉ in the menu bar: the bar opens
    and starts listening. Start typing instead and listening stops.
  · Voice pill: ✕ cancels (or stops it talking), ✓ means "done talking — send it",
    click the pill while idle to talk. The bars move with your voice.
  · "Hey JARVIS" from anywhere also opens it, without stealing your typing.
  · Typed answers are shown, not spoken (JARVIS_POPUP_SPEAK=1 to hear them).

Needs server.py; voice needs listener.py, which sends live mic levels over UDP
to 127.0.0.1:JARVIS_UI_PORT (8731).
"""
import json
import math
import os
import random
import signal
import socket
import subprocess
import threading
import time
import urllib.request

import objc
from AppKit import (NSApp, NSAppearance, NSApplication, NSApplicationActivationPolicyAccessory,
                    NSAttributedString, NSBackingStoreBuffered, NSBezierPath, NSButton, NSColor, NSEvent,
                    NSEventMaskFlagsChanged, NSEventMaskKeyDown, NSEventModifierFlagCommand,
                    NSEventModifierFlagControl, NSEventModifierFlagFunction, NSEventModifierFlagOption,
                    NSEventModifierFlagShift, NSFloatingWindowLevel, NSFont, NSFontAttributeName,
                    NSForegroundColorAttributeName, NSMakeRect, NSPanel, NSScreen, NSScrollView, NSStatusBar,
                    NSTextField, NSTextView, NSVariableStatusItemLength, NSView,
                    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary)
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

ROOT = os.path.dirname(os.path.abspath(__file__))
_env = os.path.join(ROOT, ".env")
for line in (open(_env, encoding="utf-8") if os.path.exists(_env) else []):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PORT = int(os.environ.get("JARVIS_PORT", "8730"))
UI_PORT = int(os.environ.get("JARVIS_UI_PORT", "8731"))
SERVER, HOST = f"http://127.0.0.1:{PORT}", f"localhost:{PORT}"
SPEAK = os.environ.get("JARVIS_POPUP_SPEAK", "0") == "1"
LISTEN_ON_OPEN = os.environ.get("JARVIS_LISTEN_ON_OPEN", "1") != "0"
HOTKEY = {"ctrl": NSEventModifierFlagControl, "cmd": NSEventModifierFlagCommand,
          "shift": NSEventModifierFlagShift, "fn": NSEventModifierFlagFunction,
          "option": NSEventModifierFlagOption}.get(os.environ.get("JARVIS_HOTKEY", "cmd").lower(),
                                                   NSEventModifierFlagCommand)
HOTKEY_GLYPH = {NSEventModifierFlagControl: "⌃", NSEventModifierFlagCommand: "⌘",
                NSEventModifierFlagShift: "⇧", NSEventModifierFlagFunction: "fn",
                NSEventModifierFlagOption: "⌥"}[HOTKEY]
ALL_MODS = (NSEventModifierFlagControl | NSEventModifierFlagCommand | NSEventModifierFlagShift
            | NSEventModifierFlagOption | NSEventModifierFlagFunction)
DOUBLE_TAP_S = 0.35
SNAPSHOT_DIR = os.environ.get("JARVIS_POPUP_SNAPSHOT", "")   # testing: save a PNG of the bar per state
FOCUS_FLAG = "/tmp/jarvis-page-focus"           # written by server.py's /api/focus


def page_focused():
    """True while the JARVIS page/app is in front (it heartbeats every 10 s)."""
    try:
        if time.time() - os.path.getmtime(FOCUS_FLAG) > 30:
            return False
        with open(FOCUS_FLAG) as f:
            return f.read().strip() == "1"
    except OSError:
        return False

# geometry (points)
W = 740                 # bar width
TOP = 74                # input row
BOT = 60                # voice row
ANS_MAX = 260           # tallest the answer area gets
BOTTOM_MARGIN = 36      # gap above the Dock / bottom edge
PILL_W, PILL_H = 116, 34
PILL_BARS = 11

ACCENT = (0.851, 0.467, 0.341)          # send button (coral)
BG = (0.118, 0.122, 0.149)              # bar background


def rgb(t, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(t[0], t[1], t[2], a)


def _req(path, data=None, ctype="application/json"):
    return urllib.request.Request(SERVER + path, data=data, method="POST" if data is not None else "GET",
                                  headers={"Content-Type": ctype, "Host": HOST, "X-Jarvis-Source": "popup"})


class Panel(NSPanel):
    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, sender):           # Esc
        d = self.delegate()
        if d is not None:
            d.close_box()
        else:
            self.orderOut_(None)


class PillView(NSView):
    """The Wispr-style voice pill:  ( ✕  ı ı ı ı ı ı  ✓ )"""

    def initWithFrame_(self, frame):
        self = objc.super(PillView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.levels = [0.0] * PILL_BARS
        self.mode = "idle"
        self.ctl = None
        return self

    def isOpaque(self):
        return False

    def acceptsFirstMouse_(self, event):
        return True

    def drawRect_(self, rect):
        b = self.bounds()
        w, h = b.size.width, b.size.height
        cap = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0.5, 0.5, w - 1, h - 1),
                                                                      (h - 1) / 2, (h - 1) / 2)
        NSColor.blackColor().setFill(); cap.fill()
        NSColor.colorWithWhite_alpha_(1.0, 0.16).setStroke(); cap.setLineWidth_(1.0); cap.stroke()

        r = h / 2 - 4                                  # button circles
        active = self.mode != "idle"
        # ✕ (left)
        cx, cy = h / 2, h / 2
        NSColor.colorWithWhite_alpha_(0.42, 1.0 if active else 0.45).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)).fill()
        x = NSBezierPath.bezierPath(); d = r * 0.42
        x.moveToPoint_((cx - d, cy - d)); x.lineToPoint_((cx + d, cy + d))
        x.moveToPoint_((cx - d, cy + d)); x.lineToPoint_((cx + d, cy - d))
        x.setLineWidth_(1.8); x.setLineCapStyle_(1)
        NSColor.colorWithWhite_alpha_(1.0, 0.95 if active else 0.5).setStroke(); x.stroke()
        # ✓ (right)
        cx = w - h / 2
        NSColor.colorWithWhite_alpha_(1.0, 1.0 if self.mode in ("idle", "listening") else 0.45).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)).fill()
        c = NSBezierPath.bezierPath(); d = r * 0.45
        c.moveToPoint_((cx - d, cy)); c.lineToPoint_((cx - d * 0.25, cy - d * 0.7))
        c.lineToPoint_((cx + d, cy + d * 0.6))
        c.setLineWidth_(2.0); c.setLineCapStyle_(1); c.setLineJoinStyle_(1)
        NSColor.blackColor().setStroke(); c.stroke()
        # bars (middle)
        x0, x1 = h + 2, w - h - 2
        step = (x1 - x0) / PILL_BARS
        bw = 2.0
        NSColor.colorWithWhite_alpha_(1.0, 0.92 if active else 0.55).setFill()
        for i, lvl in enumerate(self.levels):
            bh = 3.0 + lvl * (h - 14)
            bx = x0 + i * step + (step - bw) / 2
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bx, (h - bh) / 2, bw, bh), 1.0, 1.0).fill()

    def mouseDown_(self, event):
        if self.ctl is None:
            return
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        h, w = self.bounds().size.height, self.bounds().size.width
        if p.x < h:
            self.ctl.pill_cancel()
        elif p.x > w - h:
            self.ctl.pill_done()
        else:
            self.ctl.pill_middle()


class Controller(NSObject):
    # ── setup ─────────────────────────────────────────────
    def applicationDidFinishLaunching_(self, note):
        self.busy = False
        self.last_tap = 0.0
        self.mod_down = False
        self.toggle_flag = False
        self.voice_opened = False
        self.hide_at = 0.0
        self.mode = "idle"                         # idle | listening | thinking | speaking
        self.hist = [0.0] * PILL_BARS
        self.phase = 0.0
        self.you, self.reply = "", ""
        self.ans_h = 0

        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        b = self.item.button()
        b.setTitle_("◉")
        b.setToolTip_(f"JARVIS — click, or double-tap {HOTKEY_GLYPH}")
        b.setTarget_(self)
        b.setAction_("toggle:")

        self.build_panel()

        mask = NSEventMaskFlagsChanged | NSEventMaskKeyDown
        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self.on_key)
        NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self.on_key_local)

        signal.signal(signal.SIGUSR1, lambda *_: setattr(self, "toggle_flag", True))
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 30, self, "tick:", None, True)

        threading.Thread(target=self.feed_loop, daemon=True).start()
        threading.Thread(target=self.ui_loop, daemon=True).start()
        self.check_accessibility()
        print(f"JARVIS pop-up ready — click ◉ in the menu bar, or double-tap {HOTKEY_GLYPH}", flush=True)

    @objc.python_method
    def check_accessibility(self):
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
            if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
                print("! The double-tap hotkey needs Accessibility permission: System Settings → "
                      "Privacy & Security → Accessibility. The menu bar icon works without it.", flush=True)
        except Exception:                          # noqa: BLE001
            pass

    @objc.python_method
    def label(self, text, size, color, weight=0.0):
        t = NSTextField.labelWithString_(text)
        t.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        t.setTextColor_(color)
        return t

    @objc.python_method
    def build_panel(self):
        h = TOP + BOT + 1
        p = Panel.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, W, h), 0,
                                                                       NSBackingStoreBuffered, False)
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(True)
        p.setLevel_(NSFloatingWindowLevel)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | NSWindowCollectionBehaviorFullScreenAuxiliary)
        p.setHidesOnDeactivate_(False)
        p.setMovableByWindowBackground_(True)
        p.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        p.setDelegate_(self)

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, h))
        root.setWantsLayer_(True)
        L = root.layer()
        L.setBackgroundColor_(rgb(BG, 0.98).CGColor())
        L.setCornerRadius_(18.0)
        L.setMasksToBounds_(True)
        L.setBorderWidth_(1.0)
        L.setBorderColor_(NSColor.colorWithWhite_alpha_(1.0, 0.09).CGColor())
        p.setContentView_(root)

        self.icon = self.label("◉", 26, NSColor.systemTealColor())
        root.addSubview_(self.icon)

        f = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        f.setBordered_(False); f.setBezeled_(False); f.setDrawsBackground_(False)
        f.setFocusRingType_(1)
        f.setFont_(NSFont.systemFontOfSize_(22))
        f.setTextColor_(NSColor.whiteColor())
        f.setPlaceholderAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            "What can I help you with today?",
            {NSFontAttributeName: NSFont.systemFontOfSize_(22),
             NSForegroundColorAttributeName: NSColor.colorWithWhite_alpha_(1.0, 0.42)}))
        f.setTarget_(self); f.setAction_("send:"); f.setDelegate_(self)
        f.cell().setUsesSingleLineMode_(True)
        root.addSubview_(f)
        self.field = f

        s = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 44, 42))
        s.setBordered_(False)
        s.setWantsLayer_(True)
        s.layer().setBackgroundColor_(rgb(ACCENT).CGColor())
        s.layer().setCornerRadius_(9.0)
        s.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            "↑", {NSFontAttributeName: NSFont.systemFontOfSize_weight_(22, 0.5),
                  NSForegroundColorAttributeName: NSColor.whiteColor()}))
        s.setTarget_(self); s.setAction_("send:")
        root.addSubview_(s)
        self.send_btn = s

        sc = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        sc.setHasVerticalScroller_(True); sc.setDrawsBackground_(False); sc.setBorderType_(0)
        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, W - 96, 10))
        tv.setEditable_(False); tv.setSelectable_(True); tv.setDrawsBackground_(False)
        tv.setFont_(NSFont.systemFontOfSize_(16))
        tv.setTextColor_(NSColor.colorWithWhite_alpha_(1.0, 0.92))
        tv.setAutoresizingMask_(2)
        sc.setDocumentView_(tv)
        root.addSubview_(sc)
        self.scroll, self.answer = sc, tv

        self.divider = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, W, 1))
        self.divider.setWantsLayer_(True)
        self.divider.layer().setBackgroundColor_(NSColor.colorWithWhite_alpha_(1.0, 0.09).CGColor())
        root.addSubview_(self.divider)

        self.title = self.label("Ask by voice or text", 15, NSColor.whiteColor(), 0.23)
        self.sub = self.label(f"Double-tap {HOTKEY_GLYPH} any time  ·  Esc to close", 12,
                              NSColor.colorWithWhite_alpha_(1.0, 0.45))
        root.addSubview_(self.title)
        root.addSubview_(self.sub)

        self.pill = PillView.alloc().initWithFrame_(NSMakeRect(0, 0, PILL_W, PILL_H))
        self.pill.ctl = self
        root.addSubview_(self.pill)

        self.panel, self.root = p, root
        self.layout()

    @objc.python_method
    def layout(self):
        a = self.ans_h
        h = BOT + 1 + a + TOP
        self.root.setFrame_(NSMakeRect(0, 0, W, h))
        ty = h - TOP                                   # bottom of the input row
        self.icon.setFrame_(NSMakeRect(22, ty + 19, 36, 34))
        self.field.setFrame_(NSMakeRect(62, ty + 22, W - 62 - 84, 30))
        self.send_btn.setFrame_(NSMakeRect(W - 66, ty + 16, 44, 42))
        self.scroll.setHidden_(a == 0)
        if a:
            self.scroll.setFrame_(NSMakeRect(62, BOT + 1 + 6, W - 62 - 30, a - 6))
            self.answer.setFrame_(NSMakeRect(0, 0, W - 62 - 30, a - 6))
        self.divider.setFrame_(NSMakeRect(0, BOT, W, 1))
        self.title.setFrame_(NSMakeRect(24, 30, 460, 20))
        self.sub.setFrame_(NSMakeRect(24, 11, 460, 16))
        self.pill.setFrame_(NSMakeRect(W - 24 - PILL_W, (BOT - PILL_H) / 2, PILL_W, PILL_H))

    @objc.python_method
    def resize_for_answer(self):
        text = self.answer.string() or ""
        if not text.strip():
            want = 0
        else:
            per_line = max(20, int((W - 110) / 8.6))
            lines = sum(max(1, math.ceil(len(seg) / per_line)) for seg in text.split("\n"))
            want = min(ANS_MAX, 16 + lines * 21)
        if want == self.ans_h:
            return
        self.ans_h = want
        fr = self.panel.frame()                        # grow upward, keep the bottom edge put
        self.panel.setFrame_display_(NSMakeRect(fr.origin.x, fr.origin.y, W, BOT + 1 + want + TOP), True)
        self.layout()

    @objc.python_method
    def snapshot(self, name):
        """Testing aid: render the bar to a PNG (no screen-recording permission needed)."""
        if not SNAPSHOT_DIR:
            return
        import Quartz
        self.root.displayIfNeeded()
        fr = self.root.bounds()
        scale = 2
        w, h = int(fr.size.width * scale), int(fr.size.height * scale)
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, cs, Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        self.root.layer().renderInContext_(ctx)
        img = Quartz.CGBitmapContextCreateImage(ctx)
        from Foundation import NSURL
        url = NSURL.fileURLWithPath_(os.path.join(SNAPSHOT_DIR, f"bar-{name}.png"))
        dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
        Quartz.CGImageDestinationAddImage(dest, img, None)
        Quartz.CGImageDestinationFinalize(dest)

    # ── show / hide ───────────────────────────────────────
    def toggle_(self, sender):
        if self.panel.isVisible():
            self.close_box()
        else:
            self.show(focus=True)
            if LISTEN_ON_OPEN:
                self.listen()

    @objc.python_method
    def place(self):
        scr = NSScreen.mainScreen().visibleFrame()
        fr = self.panel.frame()
        self.panel.setFrameOrigin_((scr.origin.x + (scr.size.width - W) / 2, scr.origin.y + BOTTOM_MARGIN))
        return fr

    @objc.python_method
    def show(self, focus):
        if not self.panel.isVisible():
            self.place()
        if focus:
            self.voice_opened = False
            NSApp.activateIgnoringOtherApps_(True)
            self.panel.makeKeyAndOrderFront_(None)
            self.panel.makeFirstResponder_(self.field)
        else:
            self.panel.orderFrontRegardless()      # appear without stealing your typing

    @objc.python_method
    def close_box(self):
        self.stop_listening()
        self.panel.orderOut_(None)

    def windowDidResignKey_(self, note):           # click away = close, like Spotlight
        if not self.busy and self.mode in ("idle", "listening"):
            self.close_box()

    # ── voice controls ────────────────────────────────────
    @objc.python_method
    def listen(self):
        ok = subprocess.run(["pkill", "-USR1", "-f", "listener.py"]).returncode == 0
        if not ok:
            self.set_status("Voice is off", "The listener isn't running — type instead, or start ./jarvis.sh")

    @objc.python_method
    def stop_listening(self):
        if self.mode in ("listening", "speaking", "thinking"):
            subprocess.run(["pkill", "-USR2", "-f", "listener.py"])
            if self.mode == "listening":
                self.set_mode("idle")

    @objc.python_method
    def pill_cancel(self):                         # ✕
        self.stop_listening()
        self.set_mode("idle")

    @objc.python_method
    def pill_done(self):                           # ✓ = done talking, send it
        if self.mode == "listening":
            subprocess.run(["pkill", "-USR1", "-f", "listener.py"])
        elif self.mode == "idle":
            self.listen()

    @objc.python_method
    def pill_middle(self):
        if self.mode == "idle":
            self.listen()

    def controlTextDidChange_(self, note):         # you started typing: stop listening
        if self.mode == "listening":
            self.stop_listening()
            self.set_status("Typing…", "Press Return to send")

    # ── 30 fps: hotkey flag, bars, auto-hide ──────────
    def tick_(self, timer):
        if self.toggle_flag:
            self.toggle_flag = False
            self.toggle_(None)
        self.phase += 0.2
        m = self.mode
        if m == "listening":
            target = self.hist
        elif m == "speaking":
            target = [0.2 + 0.6 * abs(math.sin(self.phase + i * 0.6)) * random.uniform(0.55, 1.0)
                      for i in range(PILL_BARS)]
        elif m == "thinking":
            target = [0.08 + 0.35 * max(0.0, math.sin(self.phase - i * 0.5)) for i in range(PILL_BARS)]
        else:
            target = [0.0] * PILL_BARS
        self.pill.levels = [c + (t - c) * 0.4 for c, t in zip(self.pill.levels, target)]
        self.pill.mode = m
        if self.panel.isVisible():
            self.pill.setNeedsDisplay_(True)
        if self.hide_at and time.monotonic() > self.hide_at:
            self.hide_at = 0.0
            if self.voice_opened and not self.panel.isKeyWindow():
                self.panel.orderOut_(None)

    # ── double-tap hotkey ─────────────────────────────────
    @objc.python_method
    def on_key_local(self, event):
        self.on_key(event)
        return event

    @objc.python_method
    def on_key(self, event):
        if event.type() == 10:
            self.last_tap = 0.0
            self.key_between = True                # ⌘C etc. isn't a tap
            return
        flags = event.modifierFlags() & ALL_MODS
        down = flags == HOTKEY
        now = time.monotonic()
        # while listening: ONE tap of the hotkey = "I'm done talking"
        if self.mode == "listening" and now - getattr(self, "listen_at", 0) > 0.6:
            if down and not self.mod_down:
                self.tap_down_at, self.key_between = now, False
            elif not down and self.mod_down and not getattr(self, "key_between", True) \
                    and now - getattr(self, "tap_down_at", 0) < 0.5:
                self.last_tap = 0.0
                AppHelper.callAfter(self.pill_done)
            self.mod_down = down
            return
        if down and not self.mod_down:
            now = time.monotonic()
            if now - self.last_tap < DOUBLE_TAP_S:
                self.last_tap = 0.0
                self.toggle_flag = True
            else:
                self.last_tap = now
        elif flags and flags != HOTKEY:
            self.last_tap = 0.0
        self.mod_down = down

    # ── text in the bar ───────────────────────────────────
    @objc.python_method
    def render(self):
        s = (f"🗣  {self.you}\n\n" if self.you else "") + self.reply
        self.answer.setString_(s)
        self.resize_for_answer()
        self.answer.scrollToEndOfDocument_(None)

    @objc.python_method
    def set_status(self, title, sub=None):
        self.title.setStringValue_(title)
        if sub is not None:
            self.sub.setStringValue_(sub)

    @objc.python_method
    def set_mode(self, mode):
        self.mode = mode
        self.set_status(*{
            "idle": ("Ask by voice or text", f"Double-tap {HOTKEY_GLYPH} any time  ·  Esc to close"),
            "listening": ("Listening…  take your time", f"Tap {HOTKEY_GLYPH} (or ✓) when you're done  ·  ✕ to cancel"),
            "thinking": ("Thinking…", "✕ to cancel"),
            "speaking": ("JARVIS is answering", "✕ to stop talking"),
        }[mode])

    # ── typing ────────────────────────────────────────────
    def send_(self, sender):
        q = self.field.stringValue().strip()
        if not q or self.busy:
            return
        self.busy = True
        self.field.setStringValue_("")
        self.you, self.reply = q, ""
        self.render()
        self.set_mode("thinking")
        threading.Thread(target=self.run, args=(q,), daemon=True).start()

    @objc.python_method
    def run(self, q):
        t0 = time.monotonic()

        def put(text):
            self.reply = text
            self.render()
        text = ""
        try:
            with urllib.request.urlopen(_req("/api/run", json.dumps({"message": q}).encode()), timeout=300) as r:
                for raw in r:
                    ev = json.loads(raw)
                    if ev.get("t") == "delta":
                        text += ev["text"]
                        AppHelper.callAfter(put, text)
                    elif ev.get("t") == "tool" and ev.get("phase") == "use":
                        AppHelper.callAfter(self.set_status, "Looking that up…")
                    elif ev.get("t") == "error" and not text:
                        text = "⚠ " + str(ev.get("message", "Something went wrong."))
                        AppHelper.callAfter(put, text)
        except Exception as e:                     # noqa: BLE001
            text = f"⚠ Can't reach JARVIS ({e}). Is ./jarvis.sh running?"
            AppHelper.callAfter(put, text)
        took = time.monotonic() - t0

        def finish():
            self.set_mode("idle")
            self.set_status(f"Answered in {took:.1f}s", f"Double-tap {HOTKEY_GLYPH} any time  ·  Esc to close")
        AppHelper.callAfter(finish)
        self.busy = False
        if SPEAK and text and not text.startswith("⚠"):
            try:
                with urllib.request.urlopen(_req("/api/speak", json.dumps({"text": text}).encode()), timeout=30) as r:
                    open("/tmp/jarvis-popup.mp3", "wb").write(r.read())
                subprocess.run(["afplay", "/tmp/jarvis-popup.mp3"])
            except Exception:                      # noqa: BLE001
                pass

    # ── voice state from the listener ─────────────────────
    @objc.python_method
    def on_ui(self, msg):
        s = msg.get("s")
        if s == "level":
            self.hist = self.hist[1:] + [float(msg.get("l", 0))]
            return
        if s == "listening":
            self.listen_at = time.monotonic()
            self.hide_at = 0.0
            if not self.panel.isVisible() and not page_focused():
                self.voice_opened = True              # on the JARVIS page, the page shows it instead
                self.show(focus=False)
            self.you, self.reply = "", ""
            self.hist = [0.0] * PILL_BARS
            self.render()
            self.set_mode("listening")
        elif s == "thinking":
            self.set_mode("thinking")
        elif s == "heard":
            self.you = msg.get("t", "")
            self.render()
        elif s == "speaking":
            self.set_mode("speaking")
        elif s == "snap":
            self.pill.setNeedsDisplay_(True)
            self.snapshot(msg.get("t", "x"))
        elif s == "idle":
            self.set_mode("idle")
            if self.voice_opened:
                self.hide_at = time.monotonic() + 12      # linger so you can read it

    @objc.python_method
    def ui_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", UI_PORT))
        except OSError as e:
            print(f"! voice bars unavailable (port {UI_PORT}: {e})", flush=True)
            return
        while True:
            try:
                data, _ = sock.recvfrom(4096)
                AppHelper.callAfter(self.on_ui, json.loads(data))
            except Exception:                      # noqa: BLE001
                continue

    # ── voice answers stream in from the server's feed ────
    @objc.python_method
    def feed_loop(self):
        seq = -1

        def put(text):
            self.reply = text
            self.render()
        text = ""
        while True:
            try:
                with urllib.request.urlopen(_req(f"/api/feed?since={seq}"), timeout=30) as r:
                    j = json.loads(r.read())
            except Exception:                      # noqa: BLE001
                time.sleep(2)
                continue
            if seq >= 0:
                for ev in j["events"]:
                    if ev.get("src") != "voice":
                        continue
                    if ev.get("t") == "mirror_start":
                        text = ""
                    elif ev.get("t") == "delta":
                        text += ev["text"]
                        AppHelper.callAfter(put, text)
                    elif ev.get("t") == "tool" and ev.get("phase") == "use":
                        AppHelper.callAfter(self.set_status, "Looking that up…")
            seq = j["seq"]


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)    # no Dock icon
    ctl = Controller.alloc().init()
    # Set up directly: launched from a terminal without an app bundle, the
    # "did finish launching" callback doesn't reliably arrive.
    ctl.applicationDidFinishLaunching_(None)
    signal.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    app.run()


if __name__ == "__main__":
    main()
