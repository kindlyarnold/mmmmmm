"""Android foreground service to keep trading running with screen off."""
try:
    from jnius import autoclass
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    NotificationBuilder = autoclass('android.app.Notification$Builder')
    Context = autoclass('android.content.Context')
    NotificationManager = autoclass('android.app.NotificationManager')
    Notification = autoclass('android.app.Notification')
    NotificationChannel = autoclass('android.app.NotificationChannel')
    ANDROID_AVAILABLE = True
except ImportError:
    ANDROID_AVAILABLE = False
    print("Android JNIUS not available - running in desktop mode")


class ForegroundService:
    """Manages foreground service with notification for Android."""

    def __init__(self):
        self.service = None
        self.notification_id = 1
        self.channel_created = False

    def _create_notification_channel(self):
        """Create notification channel for Android 8.0+."""
        if self.channel_created or not ANDROID_AVAILABLE:
            return

        try:
            current_activity = PythonActivity.mActivity
            notification_manager = current_activity.getSystemService(Context.NOTIFICATION_SERVICE)

            # Create channel
            channel = NotificationChannel(
                'deriv_trader_channel',
                'Deriv Trader',
                NotificationManager.IMPORTANCE_LOW
            )
            channel.setDescription('Background trading service')
            notification_manager.createNotificationChannel(channel)
            self.channel_created = True
        except Exception as e:
            print(f"Failed to create notification channel: {e}")

    def start(self, title="Deriv Trader", message="Trading engine running"):
        """Start foreground service with notification."""
        if not ANDROID_AVAILABLE:
            print("Android not available - foreground service skipped")
            return False

        try:
            self._create_notification_channel()
            current_activity = PythonActivity.mActivity
            notification_builder = NotificationBuilder(current_activity, 'deriv_trader_channel')
            notification_builder.setContentTitle(title)
            notification_builder.setContentText(message)
            notification_builder.setSmallIcon(current_activity.getApplicationInfo().icon)
            notification_builder.setAutoCancel(False)

            notification = notification_builder.build()

            # Start foreground service
            current_activity.startForeground(self.notification_id, notification)

            return True
        except Exception as e:
            print(f"Failed to start foreground service: {e}")
            return False

    def stop(self):
        """Stop foreground service."""
        if not ANDROID_AVAILABLE:
            return False

        try:
            current_activity = PythonActivity.mActivity
            current_activity.stopForeground(True)
            return True
        except Exception as e:
            print(f"Failed to stop foreground service: {e}")
            return False

    def update_notification(self, message):
        """Update notification message."""
        if not ANDROID_AVAILABLE:
            return

        try:
            current_activity = PythonActivity.mActivity
            notification_builder = NotificationBuilder(current_activity, 'deriv_trader_channel')
            notification_builder.setContentTitle("Deriv Trader")
            notification_builder.setContentText(message)
            notification_builder.setSmallIcon(current_activity.getApplicationInfo().icon)

            notification = notification_builder.build()
            notification_manager = current_activity.getSystemService(Context.NOTIFICATION_SERVICE)
            notification_manager.notify(self.notification_id, notification)
        except Exception as e:
            print(f"Failed to update notification: {e}")


# Keep wake lock to prevent CPU sleep
def acquire_wake_lock():
    """Acquire partial wake lock to keep CPU running."""
    if not ANDROID_AVAILABLE:
        return None

    try:
        PowerManager = autoclass('android.os.PowerManager')
        current_activity = PythonActivity.mActivity
        power_manager = current_activity.getSystemService(Context.POWER_SERVICE)
        wake_lock = power_manager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            'DerivTrader:TradingWakeLock'
        )
        wake_lock.acquire(10*60*1000)  # 10 minutes timeout
        return wake_lock
    except Exception as e:
        print(f"Failed to acquire wake lock: {e}")
        return None


def release_wake_lock(wake_lock):
    """Release wake lock."""
    if not ANDROID_AVAILABLE or not wake_lock:
        return

    try:
        if wake_lock.isHeld():
            wake_lock.release()
    except Exception as e:
        print(f"Failed to release wake lock: {e}")
