"""Kivy Android front-end for the shared Deriv TradingEngine."""
import asyncio
import logging
import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch

import deriv_synth_bot as bot

# Android service imports (only available on Android)
try:
    from service import ForegroundService, acquire_wake_lock, release_wake_lock, ANDROID_AVAILABLE
except ImportError:
    ANDROID_AVAILABLE = False
    print("Android service module not available - running in desktop mode")


class UILogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        msg = self.format(record)
        Clock.schedule_once(lambda dt: self.callback(msg))


class SynthBotApp(App):
    title = "Deriv Trader (demo first)"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.foreground_service = None
        self.wake_lock = None
        self.risk = None
        if ANDROID_AVAILABLE:
            self.foreground_service = ForegroundService()

    def build(self):
        self.loop = self.task = None
        self.accounts = []
        root = BoxLayout(orientation="vertical", padding=8, spacing=5)

        # Start stats update timer
        Clock.schedule_interval(self._periodic_stats_update, 1.0)

        def field(hint, text="", password=False):
            widget = TextInput(hint_text=hint, text=text, password=password,
                               multiline=False, size_hint_y=None, height=42)
            root.add_widget(widget)
            return widget

        self.pat = field("Deriv Personal Access Token", password=True)
        self.app_id = field("Deriv App ID")

        # Account selection section
        acct_layout = BoxLayout(orientation="horizontal", size_hint_y=None, height=90, spacing=5)
        acct_left = BoxLayout(orientation="vertical", size_hint_x=0.3)
        acct_right = BoxLayout(orientation="vertical", size_hint_x=0.7)

        acct_label = Label(text="Account:", size_hint_y=None, height=42, halign="left", valign="middle")
        acct_label.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        acct_left.add_widget(acct_label)

        self.acct_spinner = Spinner(text="Load accounts...", values=["Load accounts..."], size_hint_y=None, height=42)
        self.acct_spinner.bind(text=self.on_account_select)
        acct_right.add_widget(self.acct_spinner)

        refresh_btn = Button(text="Refresh", size_hint_y=None, height=42)
        refresh_btn.bind(on_press=self.load_accounts)
        acct_right.add_widget(refresh_btn)

        create_btn = Button(text="Create Demo", size_hint_y=None, height=42)
        create_btn.bind(on_press=self.create_demo_account)
        acct_right.add_widget(create_btn)

        acct_layout.add_widget(acct_left)
        acct_layout.add_widget(acct_right)
        root.add_widget(acct_layout)

        # Manual account ID fallback
        self.acct = field("Or enter account ID manually (use DEMO)")

        self.symbols = field("Markets, comma-separated", "R_100,frxEURUSD")
        self.stake = field("Stake (max limited by MAX_STAKE)", "1")
        self.max_loss = field("Session max loss", "10")
        self.target = field("Session profit target", "10")
        self.strategy = field("Strategy: trend or meanrev", "trend")

        # Allow real account toggle
        toggle_layout = BoxLayout(orientation="horizontal", size_hint_y=None, height=48, spacing=10)
        toggle_label = Label(text="Allow real account:", size_hint_x=0.7, halign="left", valign="middle")
        toggle_label.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        self.allow_real_switch = Switch(active=False, size_hint_x=0.3)
        toggle_layout.add_widget(toggle_label)
        toggle_layout.add_widget(self.allow_real_switch)
        root.add_widget(toggle_layout)

        warning = Label(text="⚠️ DEMO FIRST • Real accounts blocked unless toggle is ON",
                        size_hint_y=None, height=48, halign="left", valign="middle")
        warning.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        root.add_widget(warning)

        # Dashboard stats
        self.stats_label = Label(text="Session P/L: $0.00 | Trades: 0 | Win Rate: 0% | Losing Streak: 0",
                                  size_hint_y=None, height=36, halign="center", valign="middle")
        self.stats_label.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        root.add_widget(self.stats_label)

        self.btn = Button(text="START", size_hint_y=None, height=52)
        self.btn.bind(on_press=self.toggle)
        root.add_widget(self.btn)

        self.log_label = Label(text="", size_hint_y=None, halign="left", valign="top")
        self.log_label.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        self.log_label.bind(texture_size=lambda w, size: setattr(w, "height", size[1]))
        self.scroll = ScrollView()
        self.scroll.add_widget(self.log_label)
        root.add_widget(self.scroll)
        bot.log.addHandler(UILogHandler(self.append))
        bot.log.setLevel(logging.INFO)
        return root

    def append(self, msg):
        self.log_label.text = "\n".join((self.log_label.text + "\n" + msg).splitlines()[-200:])
        self.scroll.scroll_y = 0

        # Update stats from log messages
        if "session" in msg.lower() and "|" in msg:
            try:
                parts = msg.split("|")
                for part in parts:
                    if "session" in part.lower():
                        pnl = part.split()[-1]
                        self._update_stats()
            except:
                pass

    def _update_stats(self):
        """Update dashboard stats from the trading engine."""
        if self.risk:
            r = self.risk
            win_rate = (r.wins / r.trades * 100) if r.trades > 0 else 0
            stats = f"Session P/L: ${r.pnl:+.2f} | Trades: {r.trades} | Win Rate: {win_rate:.1f}% | Losing Streak: {r.losses_in_row}"
            self.stats_label.text = stats

    def _periodic_stats_update(self, dt):
        """Periodically update stats display."""
        self._update_stats()

    def load_accounts(self, *_):
        """Load accounts from Deriv API."""
        if not self.pat.text or not self.app_id.text:
            self.append("Error: Enter PAT and App ID first")
            return

        def _load():
            try:
                cfg = bot.Config(pat=self.pat.text.strip(), app_id=self.app_id.text.strip())
                deriv = bot.Deriv(cfg)
                accounts = deriv.list_accounts()
                self.accounts = accounts if isinstance(accounts, list) else accounts.get("accounts", [])
                demo_accounts = [acc for acc in self.accounts if acc.get("account_type") == "demo"]
                account_values = [f"{acc.get('loginid', 'N/A')} ({acc.get('currency', 'USD')})" for acc in demo_accounts]
                if not account_values:
                    account_values = ["No demo accounts found"]

                Clock.schedule_once(lambda dt: self._update_account_spinner(account_values))
                self.append(f"Loaded {len(demo_accounts)} demo account(s)")
            except Exception as exc:
                self.append(f"Failed to load accounts: {exc}")

        threading.Thread(target=_load, daemon=True).start()

    def _update_account_spinner(self, values):
        self.acct_spinner.values = values
        if len(values) == 1 and values[0] == "No demo accounts found":
            self.acct_spinner.text = "No demo accounts"
        else:
            self.acct_spinner.text = values[0] if values else "Load accounts..."

    def create_demo_account(self, *_):
        """Create a new demo account."""
        if not self.pat.text or not self.app_id.text:
            self.append("Error: Enter PAT and App ID first")
            return

        def _create():
            try:
                cfg = bot.Config(pat=self.pat.text.strip(), app_id=self.app_id.text.strip())
                deriv = bot.Deriv(cfg)
                result = deriv.create_demo_account()
                self.append(f"Demo account created: {result.get('loginid', 'N/A')}")
                self.load_accounts()
            except Exception as exc:
                self.append(f"Failed to create demo account: {exc}")

        threading.Thread(target=_create, daemon=True).start()

    def on_account_select(self, spinner, text):
        """Handle account selection from spinner."""
        if "Load accounts" in text or "No demo" in text:
            return
        # Extract account ID from text (format: "CRW123456 (USD)")
        account_id = text.split()[0] if text else ""
        if account_id:
            self.acct.text = account_id
            self.append(f"Selected account: {account_id}")

    def toggle(self, *_):
        if self.task is not None:
            self.loop.call_soon_threadsafe(self.task.cancel)
            self.btn.text = "STOPPING..."
            return
        try:
            stake, max_loss, target = float(self.stake.text), float(self.max_loss.text), float(self.target.text)
            strategy = self.strategy.text.strip().lower()
            symbols = [item.strip() for item in self.symbols.text.split(",") if item.strip()]
            allow_real = self.allow_real_switch.active
            cfg = bot.Config(pat=self.pat.text.strip(), app_id=self.app_id.text.strip(),
                             account_id=self.acct.text.strip(), stake=stake,
                             max_daily_loss=max_loss, take_profit=target, strategy=strategy,
                             allow_real=allow_real)
            cfg.validate()
            if not symbols:
                raise ValueError("Enter at least one market symbol.")
            for symbol in symbols:
                if not symbol.startswith("frx") and not (symbol.startswith("R_") or symbol.startswith("1HZ") or symbol.startswith("JD") or symbol.startswith("stpRNG")):
                    raise ValueError(f"Unsupported symbol format: {symbol}")
            if allow_real:
                self.append("⚠️ WARNING: Real account trading ENABLED")
        except ValueError as exc:
            self.append(f"Configuration error: {exc}")
            return

        # Start Android foreground service and wake lock
        if ANDROID_AVAILABLE and self.foreground_service:
            if self.foreground_service.start():
                self.append("Foreground service started")
            self.wake_lock = acquire_wake_lock()
            if self.wake_lock:
                self.append("Wake lock acquired")

        self.btn.text = "STARTING..."
        threading.Thread(target=self._run, args=(cfg, symbols), daemon=True).start()

    def _run(self, cfg, symbols):
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        engine = bot.TradingEngine(cfg, symbols=symbols)
        self.risk = engine.risk  # Track risk for dashboard stats
        self.task = loop.create_task(engine.run())
        try:
            loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            bot.log.info("Stop requested.")
        except Exception as exc:
            bot.log.exception("Engine stopped: %s", exc)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            self.task = self.loop = None
            self.risk = None

            # Stop Android foreground service and wake lock
            if ANDROID_AVAILABLE:
                if self.foreground_service:
                    self.foreground_service.stop()
                    bot.log.info("Foreground service stopped")
                if self.wake_lock:
                    release_wake_lock(self.wake_lock)
                    self.wake_lock = None
                    bot.log.info("Wake lock released")

            Clock.schedule_once(lambda dt: setattr(self.btn, "text", "START"))


if __name__ == "__main__":
    SynthBotApp().run()
