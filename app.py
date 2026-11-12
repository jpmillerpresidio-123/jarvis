"""
JARVIS.app — the dashboard in its own Mac window (no browser, no localhost tab).

Opening the app:
  · starts JARVIS in the background if it isn't running (server, "Hey JARVIS"
    listener, and the ⌘⌘ pop-up), with full power, on this Mac only;
  · shows the mission-control dashboard in a window with a Dock icon.
Closing the window keeps JARVIS running in the background (it's your
assistant). Quit from the menu bar ◉ — or "Quit JARVIS & stop assistant" here.
"""
import os
import subprocess
import time
import urllib.request

import objc
from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSColor, NSMakeRect, NSMenu, NSMenuItem, NSScreen, NSWindow,
                    NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
from Foundation import NSObject, NSURL, NSURLRequest, NSTimer
from WebKit import WKWebView, WKWebViewConfiguration

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8730
for line in (open(os.path.join(ROOT, ".env")) if os.path.exists(os.path.join(ROOT, ".env")) else []):
    if line.startswith("JARVIS_PORT="):
        PORT = int(line.split("=", 1)[1].strip() or 8730)
URL = f"http://localhost:{PORT}/"
LOG = os.path.expanduser("~/Library/Logs/JARVIS.log")


def server_up():
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/status",
                                     headers={"Host": f"localhost:{PORT}"})
        urllib.request.urlopen(req, timeout=1.5).read()
        return True
    except Exception:                               # noqa: BLE001
        return False


def start_jarvis():
    """Launch jarvis.sh detached, so it outlives this window."""
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + ":/usr/bin:/bin:/usr/sbin:/sbin")
    subprocess.Popen(["/bin/bash", os.path.join(ROOT, "jarvis.sh")], cwd=ROOT, env=env,
                     stdout=open(LOG, "a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True)


def stop_jarvis():
    subprocess.run(["pkill", "-f", os.path.join(ROOT, "jarvis.sh")])
    for name in ("listener.py", "popup.py"):
        subprocess.run(["pkill", "-f", "--", f"ignore {name}"])
    subprocess.run(["pkill", "-f", "Python server.py"])


MIC_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"


class App(NSObject):
    def applicationDidFinishLaunching_(self, note):
        if getattr(self, "started", False):         # may be called twice (bundle + direct)
            return
        self.started = True
        icon = os.path.join(ROOT, "assets", "jarvis-icon.png")
        if os.path.exists(icon):
            from AppKit import NSImage
            NSApp.setApplicationIconImage_(NSImage.alloc().initWithContentsOfFile_(icon))
        self.build_menu()
        scr = NSScreen.mainScreen().visibleFrame()
        w, h = min(1440, scr.size.width - 80), min(900, scr.size.height - 60)
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
        win.setTitle_("JARVIS")
        win.setTitlebarAppearsTransparent_(True)
        win.setBackgroundColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0.02, 0.07, 0.10, 1.0))
        win.setFrameAutosaveName_("JARVISMain")
        win.setReleasedWhenClosed_(False)
        win.center()
        cfg = WKWebViewConfiguration.alloc().init()
        # tell the page it's inside the app, so the app (not the page) reports
        # when JARVIS is in front — the embedded page can't see window focus
        from WebKit import WKUserScript
        cfg.userContentController().addUserScript_(
            WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                "window.__JARVIS_APP = true;", 0, True))
        web = WKWebView.alloc().initWithFrame_configuration_(win.contentView().bounds(), cfg)
        web.setAutoresizingMask_(18)               # width + height sizable
        web.setValue_forKey_(False, "drawsBackground")
        win.contentView().addSubview_(web)
        win.setDelegate_(self)
        win.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        self.win, self.web = win, web
        # heartbeat: re-send "in front / not" every 5 s (server flag goes stale after 30 s)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(2.0, self, "focusBeat:", None, True)
        self.tries = 0
        self.load_html("<p style='font:15px -apple-system;color:#7fa7b5;text-align:center;margin-top:40vh'>"
                       "Starting JARVIS…</p>")
        self.ensure_mic()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.5, self, "waitForServer:",
                                                                                None, True)

    @objc.python_method
    def ensure_mic(self):
        """macOS hands an app silence until it's been allowed the mic, so ask first.
        "Hey JARVIS" can't work without this."""
        import AVFoundation as AV
        st = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio)
        if st == 3:                                  # allowed
            if not server_up():
                start_jarvis()
            return
        if st == 0:                                  # never asked: show the system prompt
            def done(granted):
                from PyObjCTools import AppHelper
                AppHelper.callAfter(self.mic_answer, bool(granted))
            AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeAudio, done)
            if not server_up():
                start_jarvis()                       # typing works meanwhile
            return
        self.mic_answer(False)                       # denied earlier

    @objc.python_method
    def mic_answer(self, granted):
        if granted:
            # restart so the "Hey JARVIS" listener picks up the real microphone
            stop_jarvis()
            time.sleep(1.0)
            start_jarvis()
            return
        from AppKit import NSAlert, NSWorkspace
        a = NSAlert.alloc().init()
        a.setMessageText_("JARVIS needs the microphone")
        a.setInformativeText_("Without it, \"Hey JARVIS\" can't hear you (typing still works). "
                              "Turn on JARVIS (it may be called Python) under Microphone, then reopen JARVIS.")
        a.addButtonWithTitle_("Open Microphone Settings")
        a.addButtonWithTitle_("Not now")
        if a.runModal() == 1000:
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(MIC_SETTINGS))

    @objc.python_method
    def load_html(self, html):
        self.web.loadHTMLString_baseURL_(
            f"<html><body style='background:#05121a;margin:0'>{html}</body></html>", None)

    def waitForServer_(self, timer):
        self.tries += 1
        if server_up():
            timer.invalidate()
            self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(URL)))
        elif self.tries > 60:
            timer.invalidate()
            self.load_html("<p style='font:15px -apple-system;color:#ff6b6b;text-align:center;margin-top:38vh'>"
                           f"JARVIS didn't start. Details: {LOG}</p>")

    # ── is the JARVIS window in front? (pop-up stays hidden + 2 s hands-free end) ──
    @objc.python_method
    def report_focus(self, on):
        import json as _json
        import threading as _th

        def send():
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/focus", method="POST",
                                             data=_json.dumps({"on": bool(on)}).encode(),
                                             headers={"Host": f"localhost:{PORT}", "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=2).read()
            except Exception:                       # noqa: BLE001
                pass
        _th.Thread(target=send, daemon=True).start()

    def windowDidBecomeKey_(self, note):
        self.report_focus(True)

    def windowDidResignKey_(self, note):
        self.report_focus(False)

    def windowWillClose_(self, note):
        self.report_focus(False)

    def applicationDidBecomeActive_(self, note):
        self.report_focus(self.win.isVisible() and not self.win.isMiniaturized())

    def applicationDidResignActive_(self, note):
        self.report_focus(False)

    def focusBeat_(self, timer):
        self.report_focus(self.win.isKeyWindow() and NSApp.isActive())

    # reopen the window when the Dock icon is clicked
    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.win.makeKeyAndOrderFront_(None)
        return True

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return False

    def reload_(self, sender):
        self.web.reload()

    def quitAll_(self, sender):
        stop_jarvis()
        NSApp.terminate_(None)

    @objc.python_method
    def build_menu(self):
        bar = NSMenu.alloc().init()
        top = NSMenuItem.alloc().init()
        bar.addItem_(top)
        m = NSMenu.alloc().initWithTitle_("JARVIS")
        m.addItemWithTitle_action_keyEquivalent_("Reload", "reload:", "r").setTarget_(self)
        m.addItem_(NSMenuItem.separatorItem())
        m.addItemWithTitle_action_keyEquivalent_("Close Window", "performClose:", "w")
        m.addItemWithTitle_action_keyEquivalent_("Hide JARVIS", "hide:", "h")
        m.addItem_(NSMenuItem.separatorItem())
        m.addItemWithTitle_action_keyEquivalent_("Quit Window (JARVIS keeps running)", "terminate:", "q")
        m.addItemWithTitle_action_keyEquivalent_("Quit JARVIS & Stop Assistant", "quitAll:", "").setTarget_(self)
        top.setSubmenu_(m)
        # Edit menu so ⌘C / ⌘V / ⌘A work in the dashboard
        ei = NSMenuItem.alloc().init(); bar.addItem_(ei)
        e = NSMenu.alloc().initWithTitle_("Edit")
        for t, a, k in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"),
                        ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
            e.addItemWithTitle_action_keyEquivalent_(t, a, k)
        ei.setSubmenu_(e)
        NSApp.setMainMenu_(bar)


def close_other_windows():
    """Only one JARVIS window at a time: close any older ones still running
    (e.g. left behind when the launcher was stopped), so the Dock shows one icon."""
    me = os.getpid()
    out = subprocess.run(["pgrep", "-f", os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")],
                         capture_output=True, text=True).stdout.split()
    for pid in out:
        if pid.isdigit() and int(pid) != me:
            try:
                os.kill(int(pid), 15)
            except OSError:
                pass


def main():
    close_other_windows()
    # show "JARVIS" (not "Python") in the menu bar
    from Foundation import NSBundle
    info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = "JARVIS"
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)     # Dock icon + menu bar
    d = App.alloc().init()
    app.setDelegate_(d)
    d.applicationDidFinishLaunching_(None)
    app.run()


if __name__ == "__main__":
    main()
