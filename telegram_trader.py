"""Telegram control surface for the shared Deriv TradingEngine.
Set TELEGRAM_TOKEN and TELEGRAM_OWNER_ID plus Deriv demo credentials in environment.
"""
import asyncio
import logging
import os
import re
import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from deriv_synth_bot import Config, TradingEngine, profile, log

OWNER_ID = int(os.environ.get("TELEGRAM_OWNER_ID", "0") or 0)
SYMBOL_RE = re.compile(r"^(?:R_\d{2,3}|1HZ\d{2}V|JD\d{2}|stpRNG\d+|frx[A-Z]{6})$")

class TelegramController:
    def __init__(self, app):
        self.app = app
        self.cfg = Config()
        self.engine = TradingEngine(self.cfg, symbols=["R_100", "frxEURUSD"], notify=self.notify)
        self.task = None

    async def notify(self, message):
        if OWNER_ID: await self.app.bot.send_message(chat_id=OWNER_ID, text=message[:3900])

    def running(self): return self.task is not None and not self.task.done()

controller_key = "controller"
def owner_only(fn):
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or update.effective_user.id != OWNER_ID or OWNER_ID == 0:
            return
        return await fn(update, context, context.application.bot_data[controller_key])
    return wrapped

@owner_only
async def start(update, context, c):
    await update.message.reply_text("Deriv bot ready. DEMO FIRST; real accounts blocked by default.\n/run /stop /status /symbols /add SYMBOL /remove SYMBOL /set stake|maxloss|target|strategy VALUE /reset")

@owner_only
async def run_cmd(update, context, c):
    if c.running(): return await update.message.reply_text("Already running.")
    try: c.cfg.validate()
    except Exception as exc: return await update.message.reply_text(f"Configuration error: {exc}")
    c.task = asyncio.create_task(c.engine.run(), name="shared-trading-engine")
    await update.message.reply_text("Starting shared trading engine...")

@owner_only
async def stop_cmd(update, context, c):
    if c.running():
        c.engine.stop(); c.task.cancel()
        await update.message.reply_text("Stop requested; connection will be closed.")
    else: await update.message.reply_text("Not running.")

@owner_only
async def status(update, context, c):
    r, cfg = c.engine.risk, c.cfg
    wr = f"{r.wins/r.trades*100:.0f}%" if r.trades else "n/a"
    await update.message.reply_text(f"{'RUNNING' if c.running() else 'stopped'}\nMarkets: {', '.join(c.engine.symbols)}\nStrategy: {cfg.strategy}; stake: {cfg.stake}; max loss: {cfg.max_daily_loss}; target: {cfg.take_profit}\nP/L: {r.pnl:+.2f}; trades: {r.trades}; wins: {wr}; losing streak: {r.losses_in_row}")

@owner_only
async def symbols(update, context, c): await update.message.reply_text(", ".join(c.engine.symbols) or "No markets")

@owner_only
async def add(update, context, c):
    sym = context.args[0] if context.args else ""
    if not SYMBOL_RE.fullmatch(sym): return await update.message.reply_text("Usage: /add R_50 or /add frxGBPUSD")
    if sym not in c.engine.symbols:
        c.engine.symbols.append(sym)
        if c.engine.client: await c.engine.client.subscribe(sym)
    await update.message.reply_text(f"Added {sym}. Strategy warm-up is required before a signal.")

@owner_only
async def remove(update, context, c):
    sym = context.args[0] if context.args else ""
    if sym not in c.engine.symbols: return await update.message.reply_text("Market not in list.")
    if len(c.engine.symbols) == 1: return await update.message.reply_text("Keep at least one market configured.")
    c.engine.symbols.remove(sym); c.engine.strats.pop(sym, None); c.engine.bars.pop(sym, None)
    sid = c.engine.client.sub_ids.get(sym) if c.engine.client else None
    if sid:
        try: await c.engine.client.send({"forget": sid})
        except Exception: pass
    await update.message.reply_text(f"Removed {sym}.")

@owner_only
async def set_cmd(update, context, c):
    try:
        key, val = context.args[0].lower(), context.args[1].lower()
        if key == "strategy":
            if val not in ("trend", "meanrev"): raise ValueError
            c.cfg.strategy = val; c.engine.strats.clear(); c.engine.bars.clear()
        else:
            number = float(val)
            if number <= 0: raise ValueError
            if key == "stake":
                if number > c.cfg.max_stake: return await update.message.reply_text(f"Stake capped at MAX_STAKE={c.cfg.max_stake}.")
                c.cfg.stake = number
            elif key == "maxloss": c.cfg.max_daily_loss = number
            elif key == "target": c.cfg.take_profit = number
            else: raise ValueError
        await update.message.reply_text(f"Set {key} to {val}.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: /set stake 1 | maxloss 10 | target 10 | strategy trend|meanrev")

@owner_only
async def reset(update, context, c):
    if c.running(): return await update.message.reply_text("Stop the engine before resetting session counters.")
    r = c.engine.risk; r.pnl = 0.0; r.losses_in_row = r.trades = r.wins = 0
    await update.message.reply_text("Session counters reset.")

def main():
    for key in ("TELEGRAM_TOKEN", "TELEGRAM_OWNER_ID", "DERIV_PAT", "DERIV_APP_ID", "DERIV_ACCOUNT_ID"):
        if not os.environ.get(key): raise SystemExit(f"Set {key} first.")
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()
    app.bot_data[controller_key] = TelegramController(app)
    for name, fn in [("start",start),("help",start),("run",run_cmd),("stop",stop_cmd),("status",status),("symbols",symbols),("add",add),("remove",remove),("set",set_cmd),("reset",reset)]:
        app.add_handler(CommandHandler(name, fn))
    app.run_polling()

if __name__ == "__main__": main()
