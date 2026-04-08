"""
桌面通知模块
跨平台桌面通知支持
"""
import sys
import subprocess
from typing import Optional
from dataclasses import dataclass


@dataclass
class NotificationData:
    """通知数据"""
    title: str
    message: str
    symbol: str
    full_code: str
    current_price: float
    diff_percent: float
    alert_level: str


class DesktopNotifier:
    """桌面通知器"""
    
    def __init__(self):
        self._platform = sys.platform
        self._notifier = None
        self._init_notifier()
    
    def _init_notifier(self):
        """初始化平台特定的通知器"""
        try:
            if self._platform == "darwin":  # macOS
                try:
                    import pync
                    self._notifier = "pync"
                except ImportError:
                    # 使用osascript作为备选
                    self._notifier = "osascript"
            
            elif self._platform == "win32":  # Windows
                try:
                    from win10toast import ToastNotifier
                    self._toast = ToastNotifier()
                    self._notifier = "win10toast"
                except ImportError:
                    self._notifier = None
            
            else:  # Linux
                try:
                    import notify2
                    notify2.init("股权激励监控面板")
                    self._notifier = "notify2"
                except ImportError:
                    self._notifier = None
        
        except Exception as e:
            print(f"桌面通知初始化失败: {e}")
            self._notifier = None
    
    def notify(self, data: NotificationData) -> bool:
        """
        发送桌面通知
        
        Args:
            data: 通知数据
        
        Returns:
            是否发送成功
        """
        if not self._notifier:
            print(f"[通知] {data.title}: {data.message}")
            return False
        
        try:
            if self._notifier == "pync":
                import pync
                pync.notify(
                    message=data.message,
                    title=data.title,
                    open=f"http://localhost:8000"  # 点击打开面板
                )
                return True
            
            elif self._notifier == "osascript":
                # 使用macOS的osascript发送通知（避免命令注入）
                # AppleScript字符串转义：先转义反斜杠，再转义双引号
                def escape_applescript(s: str) -> str:
                    return s.replace('\\', '\\\\').replace('"', '\\"')

                safe_message = escape_applescript(data.message)
                safe_title = escape_applescript(data.title)
                script = f'display notification "{safe_message}" with title "{safe_title}"'
                subprocess.run(
                    ["osascript", "-e", script],
                    check=False
                )
                return True
            
            elif self._notifier == "win10toast":
                self._toast.show_toast(
                    title=data.title,
                    msg=data.message,
                    duration=10,
                    threaded=True
                )
                return True
            
            elif self._notifier == "notify2":
                import notify2
                notification = notify2.Notification(
                    summary=data.title,
                    message=data.message
                )
                notification.show()
                return True
        
        except Exception as e:
            print(f"发送通知失败: {e}")
            print(f"[通知] {data.title}: {data.message}")
            return False
        
        return False
    
    def is_available(self) -> bool:
        """检查桌面通知是否可用"""
        return self._notifier is not None


# 全局通知器实例
desktop_notifier = DesktopNotifier()
