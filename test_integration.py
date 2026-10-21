"""Integration test for the updated Android app components."""
import sys

def test_service_module():
    """Test that service module can be imported and handles missing Android gracefully."""
    print("Testing service module...")
    try:
        from service import ForegroundService, acquire_wake_lock, release_wake_lock, ANDROID_AVAILABLE
        print("[OK] Service module imported successfully")
        print(f"  Android available: {ANDROID_AVAILABLE}")

        if not ANDROID_AVAILABLE:
            print("  Running in desktop mode - Android features will be skipped")
            # Test that methods don't crash
            service = ForegroundService()
            assert service.start() == False, "start() should return False on desktop"
            assert service.stop() == False, "stop() should return False on desktop"
            assert acquire_wake_lock() is None, "acquire_wake_lock() should return None on desktop"
            release_wake_lock(None)
            print("[OK] Desktop mode handling works correctly")
        else:
            print("  Android detected - foreground service available")
        return True
    except ImportError as e:
        print(f"[FAIL] Failed to import service module: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Service module test failed: {e}")
        return False

def test_deriv_module():
    """Test that deriv_synth_bot module can be imported and basic operations work."""
    print("\nTesting deriv_synth_bot module...")
    try:
        import deriv_synth_bot as bot
        print("[OK] deriv_synth_bot imported successfully")

        # Test Config
        cfg = bot.Config(pat="test_token", app_id="12345", account_id="CRW123456")
        cfg.validate()
        print("[OK] Config validation works")

        # Test account listing method exists
        assert hasattr(bot.Deriv, 'list_accounts'), "list_accounts method missing"
        assert hasattr(bot.Deriv, 'create_demo_account'), "create_demo_account method missing"
        print("[OK] New API methods present")

        # Test strategy
        strat = bot.Strategy(mode="trend")
        for i in range(100):
            strat.update(1000 + i * 0.1)
        print("[OK] Strategy initialization works")

        # Test backtest
        bot.backtest(cfg, n_ticks=1000)
        print("[OK] Backtest runs successfully")

        return True
    except Exception as e:
        print(f"[FAIL] deriv_synth_bot test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_main_module():
    """Test that main.py can be imported (Kivy may not be available)."""
    print("\nTesting main module...")
    try:
        # Just check syntax by compiling
        import py_compile
        py_compile.compile("main.py", doraise=True)
        print("[OK] main.py compiles successfully")
        return True
    except Exception as e:
        print(f"[FAIL] main.py test failed: {e}")
        return False

def main():
    print("=" * 60)
    print("INTEGRATION TEST FOR DERIV TRADER ANDROID APP")
    print("=" * 60)

    results = []
    results.append(("Service Module", test_service_module()))
    results.append(("Deriv Module", test_deriv_module()))
    results.append(("Main Module", test_main_module()))

    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"{name}: {status}")

    all_passed = all(r[1] for r in results)
    print("\n" + "=" * 60)
    if all_passed:
        print("ALL TESTS PASSED")
        print("The app is ready for Android build")
    else:
        print("SOME TESTS FAILED")
        print("Please fix the errors before building")
    print("=" * 60)

    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
