"""Shared Deriv trading engine for Android/Kivy and Telegram. Demo trading is default.

Required environment for CLI/Telegram: DERIV_PAT, DERIV_APP_ID, DERIV_ACCOUNT_ID.
Real accounts are rejected unless ALLOW_REAL=1 is explicitly set.
"""
import asyncio
import itertools
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field

import requests
import websockets

REST_BASE = "https://api.derivws.com"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")


@dataclass
class Config:
    pat: str = field(default_factory=lambda: os.environ.get("DERIV_PAT", ""))
    app_id: str = field(default_factory=lambda: os.environ.get("DERIV_APP_ID", ""))
    account_id: str = field(default_factory=lambda: os.environ.get("DERIV_ACCOUNT_ID", ""))
    allow_real: bool = field(default_factory=lambda: os.environ.get("ALLOW_REAL", "").strip() == "1")
    symbol: str = "R_100"
    currency: str = "USD"
    stake: float = 1.0
    duration_ticks: int = 5
    duration_minutes: int = 5
    strategy: str = "trend"
    max_daily_loss: float = 10.0
    take_profit: float = 10.0
    max_losses_in_row: int = 4
    cooldown_s: float = 6.0
    max_stake: float = field(default_factory=lambda: float(os.environ.get("MAX_STAKE", "5")))

    def validate(self):
        if not (self.pat and self.app_id):
            raise ValueError("Set Personal Access Token and App ID first.")
        if not self.account_id:
            raise ValueError("Select or enter an account ID first.")
        if self.stake <= 0 or self.stake > self.max_stake:
            raise ValueError(f"Stake must be > 0 and no more than MAX_STAKE ({self.max_stake}).")
        if self.max_daily_loss <= 0 or self.take_profit <= 0:
            raise ValueError("Loss limit and take-profit must be positive.")
        if self.strategy not in ("trend", "meanrev"):
            raise ValueError("Strategy must be trend or meanrev.")


def profile(symbol):
    """Deriv forex symbols use minute contracts; synthetic symbols use ticks."""
    if symbol.startswith("frx"):
        return {"duration": 5, "unit": "m", "bar_s": 60}
    return {"duration": 5, "unit": "t", "bar_s": 0}


class Bars:
    def __init__(self, bar_s=0):
        self.bar_s, self.bucket, self.last = bar_s, None, None

    def push(self, epoch, price):
        if not self.bar_s:
            return price
        bucket = int(epoch) // self.bar_s
        out = self.last if self.bucket is not None and bucket != self.bucket else None
        self.bucket, self.last = bucket, price
        return out


class Strategy:
    """Incremental EMA-cross + RSI filter. update(price) -> CALL/PUT/None."""
    def __init__(self, mode="trend", fast=10, slow=30, rsi_n=14):
        self.mode, self.rsi_n = mode, rsi_n
        self.kf, self.ks = 2 / (fast + 1), 2 / (slow + 1)
        self.f = self.s = self.last = self.prev_diff = None
        self.ag = self.al = 0.0
        self.n, self.warmup = 0, slow * 2

    def update(self, price):
        price = float(price)
        self.n += 1
        if self.last is not None:
            ch, a = price - self.last, 1 / self.rsi_n
            self.ag = (1-a)*self.ag + a*max(ch, 0)
            self.al = (1-a)*self.al + a*max(-ch, 0)
        self.last = price
        self.f = price if self.f is None else price*self.kf + self.f*(1-self.kf)
        self.s = price if self.s is None else price*self.ks + self.s*(1-self.ks)
        diff, prev = self.f-self.s, self.prev_diff
        self.prev_diff = diff
        if self.n < self.warmup or prev is None:
            return None
        rsi = 100.0 if self.al == 0 else 100 - 100/(1+self.ag/self.al)
        if self.mode == "trend":
            if prev <= 0 < diff and rsi < 70: return "CALL"
            if prev >= 0 > diff and rsi > 30: return "PUT"
        else:
            if rsi < 20: return "CALL"
            if rsi > 80: return "PUT"
        return None


@dataclass
class Risk:
    cfg: Config
    pnl: float = 0.0
    losses_in_row: int = 0
    trades: int = 0
    wins: int = 0
    last_trade_ts: float = 0.0

    def can_trade(self):
        if self.pnl <= -self.cfg.max_daily_loss: return False, "session loss limit hit"
        if self.pnl >= self.cfg.take_profit: return False, "session profit target hit"
        if self.losses_in_row >= self.cfg.max_losses_in_row: return False, "maximum losing streak hit"
        return True, ""

    def ready(self): return time.time() - self.last_trade_ts >= self.cfg.cooldown_s

    def record(self, profit):
        self.trades += 1
        self.pnl += float(profit)
        self.last_trade_ts = time.time()
        if profit > 0:
            self.wins += 1
            self.losses_in_row = 0
        else:
            self.losses_in_row += 1


def find_ws_url(obj):
    if isinstance(obj, str) and obj.startswith("wss://"): return obj
    vals = obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else []
    for value in vals:
        found = find_ws_url(value)
        if found: return found
    return None


class Deriv:
    """Async WebSocket client. Tick queue items are consistently (symbol, epoch, price)."""
    def __init__(self, cfg):
        self.cfg, self.ws = cfg, None
        self.ids = itertools.count(1)
        self.pending, self.ticks, self.pocs = {}, asyncio.Queue(), asyncio.Queue()
        self.sub_ids, self.reader_task = {}, None

    def _make_request(self, method, endpoint, data=None):
        """Helper to make REST requests with proper error handling."""
        url = f"{REST_BASE}{endpoint}"
        headers = {"Authorization": f"Bearer {self.cfg.pat}", "Deriv-App-ID": self.cfg.app_id}
        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=15)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=15)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # Handle HTTP errors with clear messages
            if response.status_code == 401:
                raise RuntimeError(f"HTTP 401 Unauthorized: Invalid Personal Access Token or App ID at {endpoint}")
            elif response.status_code == 404:
                raise RuntimeError(f"HTTP 404 Not Found: Account not found at {endpoint}")
            elif response.status_code == 403:
                raise RuntimeError(f"HTTP 403 Forbidden: Access denied at {endpoint}")
            elif response.status_code >= 400:
                error_msg = response.text[:500] if response.text else "No error details"
                raise RuntimeError(f"HTTP {response.status_code} error at {endpoint}: {error_msg}")

            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Request timeout at {endpoint}")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"Connection error - check internet connectivity for {endpoint}")
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"Request failed at {endpoint}: {exc}")

    def list_accounts(self):
        """List all accounts using GET /trading/v1/options/accounts."""
        return self._make_request("GET", "/trading/v1/options/accounts")

    def create_demo_account(self, currency="USD"):
        """Create a demo account using POST /trading/v1/options/accounts."""
        data = {"currency": currency, "group": "row", "account_type": "demo"}
        return self._make_request("POST", "/trading/v1/options/accounts", data)

    def _otp_url(self):
        """Get WebSocket URL via OTP endpoint."""
        response_data = self._make_request("POST", f"/trading/v1/options/accounts/{self.cfg.account_id}/otp")
        url = find_ws_url(response_data)
        if not url:
            raise RuntimeError(f"No WebSocket URL in OTP response: {str(response_data)[:250]}")
        return url

    async def connect(self):
        try:
            url = await asyncio.to_thread(self._otp_url)
        except Exception as exc:
            log.error("Failed to get WebSocket URL: %s", exc)
            raise
        self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5)
        self.reader_task = asyncio.create_task(self._reader(), name="deriv-reader")
        # Verify the account from Deriv's authorize response, not from a guessed URL pattern.
        try:
            auth = await self.call({"authorize": self.cfg.pat}, timeout=20)
            if "error" in auth or not isinstance(auth.get("authorize"), dict):
                raise RuntimeError("Deriv did not confirm the account type during authorization.")
            account = auth["authorize"]
            is_virtual = account.get("is_virtual")
            if is_virtual not in (0, 1, False, True):
                raise RuntimeError("Account type was not verifiable; refusing to trade for safety.")
            is_real = int(is_virtual) == 0
            if is_real and not self.cfg.allow_real:
                raise RuntimeError("Real account blocked. Use a demo account; real trading is disabled by default.")
            log.warning("Authorized %s account %s", "REAL" if is_real else "DEMO", account.get("loginid", "(ID hidden)"))
        except BaseException:
            await self.close()
            raise

    async def _reader(self):
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                    kind, rid = msg.get("msg_type"), msg.get("req_id")
                    if kind == "tick":
                        tick = msg["tick"]
                        self.sub_ids[tick["symbol"]] = msg.get("subscription", {}).get("id")
                        self.ticks.put_nowait((tick["symbol"], int(tick["epoch"]), float(tick["quote"])))
                    elif kind == "proposal_open_contract":
                        self.pocs.put_nowait(msg)
                    if rid in self.pending:
                        future = self.pending.pop(rid)
                        if not future.done(): future.set_result(msg)
                    elif "error" in msg:
                        log.warning("Deriv API error: %s", msg["error"].get("message", msg["error"]) if isinstance(msg["error"], dict) else msg["error"])
                except Exception as exc:
                    log.warning("Skipping malformed Deriv message: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Deriv reader stopped: %s", exc)
        finally:
            for future in self.pending.values():
                if not future.done(): future.set_exception(ConnectionError("Deriv WebSocket closed"))
            self.pending.clear()

    async def call(self, payload, timeout=15):
        if not self.ws:
            raise ConnectionError("Deriv WebSocket is not connected")
        rid = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.ws.send(json.dumps({**payload, "req_id": rid}))
            response = await asyncio.wait_for(future, timeout)
        except BaseException:
            self.pending.pop(rid, None)
            if not future.done(): future.cancel()
            raise
        return response

    async def send(self, payload):
        if not self.ws: raise ConnectionError("Deriv WebSocket is not connected")
        await self.ws.send(json.dumps(payload))

    async def subscribe(self, symbol): await self.send({"ticks": symbol, "subscribe": 1})

    async def trade(self, side, symbol=None, duration=None, unit=None):
        """Buy and wait for a Rise/Fall contract to settle; returns net profit or None."""
        symbol = symbol or self.cfg.symbol
        p = profile(symbol)
        duration = int(duration if duration is not None else (self.cfg.duration_minutes if p["unit"] == "m" else self.cfg.duration_ticks))
        unit = unit or p["unit"]
        if side not in ("CALL", "PUT"): raise ValueError("side must be CALL or PUT")
        if unit not in ("t", "m") or duration < 1: raise ValueError("Invalid contract duration")
        if (symbol.startswith("frx") and unit != "m") or (not symbol.startswith("frx") and unit != "t"):
            raise ValueError(f"Duration unit {unit} is not appropriate for {symbol}")
        proposal = await self.call({"proposal": 1, "amount": float(self.cfg.stake), "basis": "stake",
            "contract_type": side, "currency": self.cfg.currency, "duration": duration,
            "duration_unit": unit, "underlying_symbol": symbol})
        if "error" in proposal:
            log.warning("Proposal failed for %s: %s", symbol, proposal["error"])
            return None
        offer = proposal.get("proposal")
        if not offer or "id" not in offer or "ask_price" not in offer:
            log.warning("Unexpected proposal response for %s", symbol)
            return None
        bought = await self.call({"buy": offer["id"], "price": float(offer["ask_price"])})
        if "error" in bought:
            log.warning("Buy failed for %s: %s", symbol, bought["error"])
            return None
        contract = bought.get("buy", {}).get("contract_id")
        if not contract:
            log.warning("Buy response did not contain contract_id")
            return None
        log.info("Bought %s %s (%s ticks/minutes=%s) contract %s", side, symbol, duration, unit, contract)
        sub_id = None
        try:
            await self.send({"proposal_open_contract": 1, "contract_id": contract, "subscribe": 1})
            deadline = time.monotonic() + max(180, duration * (60 if unit == "m" else 2) + 90)
            while time.monotonic() < deadline:
                message = await asyncio.wait_for(self.pocs.get(), timeout=min(60, max(1, deadline-time.monotonic())))
                poc = message.get("proposal_open_contract", {})
                if poc.get("contract_id") != contract: continue
                sub_id = message.get("subscription", {}).get("id", sub_id)
                if poc.get("is_sold") or poc.get("status") in ("won", "lost", "sold"):
                    if sub_id:
                        try: await self.send({"forget": sub_id})
                        except Exception: pass
                    return float(poc.get("profit", 0.0))
            log.warning("Timed out waiting for contract %s; result treated as unknown, not a loss")
            return None
        finally:
            # Do not leave a contract subscription running after cancellation/error.
            if sub_id:
                try: await self.send({"forget": sub_id})
                except Exception: pass

    async def close(self):
        if self.reader_task and self.reader_task is not asyncio.current_task():
            self.reader_task.cancel()
            try: await self.reader_task
            except (asyncio.CancelledError, Exception): pass
        if self.ws:
            try: await self.ws.close()
            except Exception: pass
            self.ws = None


class TradingEngine:
    """Shared multi-market engine used by both the Kivy app and Telegram interface."""
    def __init__(self, cfg=None, symbols=None, notify=None):
        self.cfg = cfg or Config()
        self.symbols = list(symbols or [self.cfg.symbol, "frxEURUSD"])
        self.notify = notify
        self.risk = Risk(self.cfg)
        self.strats, self.bars, self.paused = {}, {}, {}
        self.client = None
        self.stop_event = asyncio.Event()

    async def message(self, text):
        log.info(text)
        if self.notify:
            try: await self.notify(text)
            except Exception as exc: log.warning("Notification failed: %s", exc)

    def on_tick(self, symbol, epoch, price):
        if symbol not in self.symbols: return None
        if symbol not in self.strats:
            self.strats[symbol] = Strategy(self.cfg.strategy)
            self.bars[symbol] = Bars(profile(symbol)["bar_s"])
        close = self.bars[symbol].push(epoch, price)
        return None if close is None else self.strats[symbol].update(close)

    async def run(self):
        self.cfg.validate()
        self.stop_event.clear()
        client = self.client = Deriv(self.cfg)
        try:
            await client.connect()
            for symbol in self.symbols: await client.subscribe(symbol)
            await self.message(f"Engine running in DEMO-first mode on {', '.join(self.symbols)}; strategy={self.cfg.strategy}; stake={self.cfg.stake:g}")
            while not self.stop_event.is_set():
                try: symbol, epoch, price = await asyncio.wait_for(client.ticks.get(), timeout=90)
                except asyncio.TimeoutError:
                    await self.message("No market ticks for 90 seconds; stopping safely.")
                    break
                sig = self.on_tick(symbol, epoch, price)
                if not sig or not self.risk.ready() or time.time() < self.paused.get(symbol, 0): continue
                ok, reason = self.risk.can_trade()
                if not ok:
                    await self.message(f"Risk stop: {reason}; session P/L {self.risk.pnl:+.2f}")
                    break
                try:
                    p = profile(symbol)
                    result = await client.trade(sig, symbol, p["duration"], p["unit"])
                except asyncio.CancelledError: raise
                except Exception as exc:
                    log.exception("Trade error on %s", symbol)
                    result = None
                if result is None:
                    self.paused[symbol] = time.time() + 300
                    await self.message(f"{symbol}: trade failed or result unknown; paused for 5 minutes. No P/L assumed.")
                else:
                    self.risk.record(result)
                    r = self.risk
                    await self.message(f"{symbol} {sig}: {result:+.2f} | session {r.pnl:+.2f} | wins {r.wins}/{r.trades} | losing streak {r.losses_in_row}")
                # Drain stale tick backlog without losing the latest indicator updates.
                while not client.ticks.empty():
                    try: self.on_tick(*client.ticks.get_nowait())
                    except asyncio.QueueEmpty: break
        finally:
            await client.close()
            self.client = None
            await self.message("Trading engine stopped; connection closed.")

    def stop(self): self.stop_event.set()


def backtest(cfg, n_ticks=200_000, payout=0.95, seed=1):
    rng, price, prices = random.Random(seed), 1000.0, []
    for _ in range(n_ticks):
        price += rng.gauss(0, 0.5); prices.append(price)
    strat, trades, wins, pnl, cool = Strategy(cfg.strategy), 0, 0, 0.0, 0
    for i, current in enumerate(prices):
        signal = strat.update(current); j = i + cfg.duration_ticks
        if signal is None or j >= n_ticks or i < cool: continue
        won = prices[j] > current if signal == "CALL" else prices[j] < current
        trades += 1; wins += int(won); pnl += cfg.stake*payout if won else -cfg.stake; cool = j
    win_rate = wins/trades if trades else 0
    log.info("SIMULATED ONLY: trades=%d win_rate=%.1f%% pnl=%.2f; break-even win rate at payout %.0f%% is %.1f%%",
             trades, win_rate*100, pnl, payout*100, 100/(1+payout))


async def run_live(cfg):
    engine = TradingEngine(cfg, symbols=[cfg.symbol])
    await engine.run()


if __name__ == "__main__":
    config = Config()
    mode = sys.argv[1] if len(sys.argv) > 1 else "backtest"
    if mode == "backtest": backtest(config)
    elif mode == "live": asyncio.run(run_live(config))
    else: raise SystemExit("Usage: python deriv_synth_bot.py [backtest|live]")
