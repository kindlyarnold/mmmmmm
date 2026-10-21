DERIV TRADER — ANDROID + TELEGRAM, SHARED ENGINE
================================================

IMPORTANT SAFETY NOTES
- Demo account first. Real accounts are blocked by default.
- Never commit your Deriv PAT or Telegram token to GitHub or send them in Telegram.
- A trading strategy can lose money; this project does not promise profits.
- Start with a small demo stake and verify the correct account/market in Deriv.
- The engine stops on session loss/profit limits, a losing streak, or a tick timeout.
- MAX_STAKE defaults to 5. The engine checks Deriv authorization metadata and refuses to trade if account type
  cannot be verified. Real-account access requires toggle to be ON; do not enable it casually.

SHARED ENGINE
- deriv_synth_bot.py: shared strategy, Deriv WebSocket client, risk manager and engine.
- main.py: Kivy Android app entry point with account selection and dashboard.
- service.py: Android foreground service for background trading.
- telegram_trader.py: Telegram command interface using the same TradingEngine.

NEW FEATURES (UPDATED)
- Account selection: Load demo accounts from Deriv API or create new demo accounts
- "Allow real account" toggle: OFF by default, must be explicitly enabled for real trading
- Dashboard: Real-time session P/L, trades, win rate, and losing streak display
- Android foreground service: Keeps trading running with screen off
- Wake lock: Prevents CPU sleep during trading
- HTTP error handling: Clear error messages for 401, 404, 403, and other HTTP errors
- Token security: Never saved to disk, kept in memory only for the session

TELEGRAM SETUP (PC, VPS, or Termux environment that supports the dependencies)
1. Install Python 3.10+.
2. pip install -r requirements-telegram.txt
3. Create a bot with @BotFather and obtain your numeric Telegram user ID.
4. Set these environment variables (use a DEMO account):
   TELEGRAM_TOKEN=... TELEGRAM_OWNER_ID=...
   DERIV_PAT=... DERIV_APP_ID=... DERIV_ACCOUNT_ID=...
   Optional: MAX_STAKE=5
5. Run: python telegram_trader.py
6. In Telegram, use /start then /status and /run. Only TELEGRAM_OWNER_ID is allowed.

On Linux/macOS, for example:
  export TELEGRAM_TOKEN='...'
  export TELEGRAM_OWNER_ID='12345678'
  export DERIV_PAT='...'
  export DERIV_APP_ID='...'
  export DERIV_ACCOUNT_ID='...'
  python telegram_trader.py

MARKETS
- Synthetic indices such as R_100 and R_50: 5-tick Rise/Fall contracts.
- Forex symbols such as frxEURUSD and frxGBPUSD: 1-minute bar closes feed the
  strategy; contracts use a 5-minute duration. These are Deriv Rise/Fall options,
  not spot forex. Forex markets are generally closed on weekends. Confirm that the
  chosen symbol and contract duration are offered on your Deriv account.
- Add markets in Telegram with /add SYMBOL, or enter comma-separated symbols in
  the Android app. Only use symbols supported by your account.

ANDROID APK BUILD
REQUIREMENTS:
- Linux or macOS (Buildozer works best on these platforms)
- Python 3.10+
- Buildozer: pip install buildozer
- Android SDK, NDK, Java JDK (Buildozer will download these automatically)
- Docker (optional, but recommended for consistent builds)

BUILD INSTRUCTIONS (Linux/macOS):
1. Install buildozer: pip install buildozer
2. Navigate to project directory: cd synthbot_android
3. Initialize buildozer (first time only): buildozer init
4. Build debug APK: buildozer android debug
5. The APK will be in: bin/derivtrader-1.0.0-arm64-v8a-debug.apk

BUILD WITH DOCKER (RECOMMENDED):
1. Install Docker
2. Run: docker run --rm -v $(pwd):/home/user/appcode buildozer buildozer android debug
3. The APK will be in the project's bin/ directory

WINDOWS BUILD:
Buildozer is not officially supported on Windows. Options:
1. Use WSL (Windows Subsystem for Linux) and follow Linux instructions
2. Use a Linux VM
3. Use GitHub Actions workflow (see .github/workflows/ if available)

INSTALL APK ON ANDROID:
1. Enable "Install from unknown sources" in Android settings
2. Transfer APK to device (USB, email, cloud)
3. Tap APK to install
4. Grant permissions: Internet, Wake Lock, Foreground Service, Notifications
5. Open app and enter your Deriv credentials
6. Load or create a demo account, configure settings, and start trading

ANDROID APP USAGE:
1. Enter Personal Access Token and App ID
2. Click "Refresh" to load demo accounts, or "Create Demo" to create a new one
3. Select account from dropdown or enter ID manually
4. Enter markets (comma-separated), stake, loss limit, profit target, and strategy
5. Toggle "Allow real account" ONLY if you want to trade with real money (⚠️ RISK)
6. Click START to begin trading
7. Dashboard shows real-time stats at the top
8. Log shows detailed trading activity below
9. Click STOP to stop trading
10. Foreground service notification will appear while trading

OFFLINE TEST
  python deriv_synth_bot.py backtest
This is a random-walk simulation only, not evidence of real-market profitability.
