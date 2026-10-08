"""主窗口：内嵌浏览器加载 dsh web，并提供启动/停止、设置、日志。"""

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDockWidget,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QToolBar,
)

from . import __version__
from .about_dialog import AboutDialog
from .config_dialog import ConfigDialog
from .dsh_manager import DEFAULT_PORT, DshManager
from .maintenance_dialog import MaintenanceDialog
from .session_cleanup_dialog import SessionCleanupDialog
from .update_dialog import UpdateDialog
from .updater import VersionCheck, version_gt
from .upgrade_worker import DshVersionCheck


class _ClickableLabel(QLabel):
    """带点击信号的 QLabel，用于状态栏的升级提示。"""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# featurePermissionRequested 收到的 Feature 枚举对应的中文名。各 Qt 版本新增的
# 枚举项并不一致（DesktopVideoCapture / LocalFontsAccess 是较新版本才有的），
# 所以逐个用 getattr 探测，取不到就退化成枚举自身的名字，不让窗口构造失败。
_PERMISSION_LABELS = (
    ("MediaAudioCapture", "麦克风"),
    ("MediaVideoCapture", "摄像头"),
    ("MediaAudioVideoCapture", "麦克风与摄像头"),
    ("DesktopVideoCapture", "屏幕画面共享"),
    ("DesktopAudioVideoCapture", "屏幕与系统声音共享"),
    ("Geolocation", "地理位置"),
    ("Notifications", "桌面通知"),
    ("MouseLock", "鼠标锁定"),
    ("ClipboardReadWrite", "剪贴板读写"),
    ("LocalFontsAccess", "本地字体"),
)

# 允许申请媒体权限的来源：只有本机 dsh 服务。对话里的网页预览等外链一律拒绝。
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _permission_label(feature) -> str:
    """把 QWebEnginePage.Feature 翻译成中文名。"""
    for name, label in _PERMISSION_LABELS:
        value = getattr(QWebEnginePage.Feature, name, None)
        if value is not None and feature == value:
            return label
    return f"未知权限（{feature}）"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSH Desktop")
        self.resize(1200, 800)

        self._settings = QSettings("dsh-gui", "dsh-gui")

        self.manager = DshManager(self)
        self._eperm_hinted = False
        # 两个 setter 自己会做容错，注册表里存了脏值也不会让窗口构造失败。
        self.manager.set_workspace(self._settings.value("workspace", str(Path.home())))
        self.manager.set_port(self._settings.value("port", DEFAULT_PORT))

        # 内嵌浏览器
        self.web = QWebEngineView(self)
        self.setCentralWidget(self.web)
        # 页面里 target="_blank" 的链接（比如对话里的 PDF/预览页链接）默认会被
        # Qt 静默丢弃，因为我们没做内嵌多窗口/多标签；改成丢给系统默认浏览器打开。
        self.web.page().newWindowRequested.connect(self._on_new_window_requested)
        # 媒体权限：QtWebEngine 默认**一律拒绝**麦克风/摄像头等请求，而且不给任何
        # 提示。缺了这段，dsh 页面里的语音输入调 getUserMedia 会被直接拒掉，页面
        # 只会一直显示「请允许使用麦克风…」，用户翻遍界面也找不到可以点「允许」的
        # 地方。已经授予过的权限记在这里，本次运行不再重复询问。
        self._granted_permissions: set = set()
        self.web.page().featurePermissionRequested.connect(
            self._on_feature_permission_requested
        )
        self.web.setHtml(
            "<html><body style='background:#1e1e1e;color:#ccc;font-family:sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>"
            "<div style='text-align:center'>"
            "<h2>DSH Desktop</h2>"
            "<p>点击左上角「启动 dsh」开始。</p>"
            "</div></body></html>"
        )

        # 日志面板
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_dock = QDockWidget("日志", self)
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()

        self._build_toolbar()
        self._build_statusbar()

        self.manager.started.connect(self._on_started)
        self.manager.log_line.connect(self._on_log)
        self.manager.stopped.connect(self._on_stopped)

        # 启动后给一点时间等待 dsh 打印地址；超时则加载默认地址。
        self._url_timer = QTimer(self)
        self._url_timer.setSingleShot(True)
        self._url_timer.timeout.connect(self._on_url_timeout)

        # 更新检测：启动 3 秒后自动检查一次（失败静默），也可手动触发。
        self._last_update_info = None
        self._update_dialog = None
        self._update_checker = VersionCheck(self)
        self._update_checker.finished.connect(self._on_update_check)
        self._update_checker.failed.connect(self._on_update_check_failed)
        QTimer.singleShot(3000, self._check_updates)

        # dsh 本体的版本检查错峰做（晚 6 秒），免得两个请求同时打出去。
        # 失败静默——这是锦上添花的信息，不值得弹窗打扰。
        self._dsh_checker = DshVersionCheck(self)
        self._dsh_checker.finished.connect(self._on_dsh_check)
        QTimer.singleShot(6000, self._dsh_checker.check)

        self._restore_geometry()

    # ---- UI 构建 ----
    def _build_toolbar(self) -> None:
        toolbar = QToolBar("工具栏", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.start_action = QAction("启动 dsh", self)
        self.start_action.triggered.connect(self._start)
        toolbar.addAction(self.start_action)

        self.stop_action = QAction("停止 dsh", self)
        self.stop_action.triggered.connect(self._stop)
        self.stop_action.setEnabled(False)
        toolbar.addAction(self.stop_action)

        toolbar.addSeparator()

        self.config_action = QAction("设置", self)
        self.config_action.triggered.connect(self._open_config)
        toolbar.addAction(self.config_action)

        self.refresh_action = QAction("刷新", self)
        self.refresh_action.triggered.connect(self._refresh)
        toolbar.addAction(self.refresh_action)

        self.open_action = QAction("在浏览器打开", self)
        self.open_action.triggered.connect(self._open_external)
        self.open_action.setEnabled(False)
        toolbar.addAction(self.open_action)

        toolbar.addSeparator()

        self.log_action = QAction("日志", self)
        self.log_action.setCheckable(True)
        self.log_action.toggled.connect(self.log_dock.setVisible)
        toolbar.addAction(self.log_action)

        toolbar.addSeparator()

        self.maintenance_action = QAction("维护", self)
        self.maintenance_action.triggered.connect(self._open_maintenance)
        toolbar.addAction(self.maintenance_action)

        self.session_cleanup_action = QAction("会话清理", self)
        self.session_cleanup_action.triggered.connect(self._open_session_cleanup)
        toolbar.addAction(self.session_cleanup_action)

        toolbar.addSeparator()

        self.update_action = QAction("检查更新", self)
        self.update_action.triggered.connect(self._check_updates)
        toolbar.addAction(self.update_action)

        self.about_action = QAction("关于", self)
        self.about_action.triggered.connect(self._open_about)
        toolbar.addAction(self.about_action)

    def _build_statusbar(self) -> None:
        self.status_label = QLabel("已停止")
        self.statusBar().addWidget(self.status_label)

        # 发现新版本时的可点击提示（默认隐藏）
        self.update_label = _ClickableLabel("")
        self.update_label.setStyleSheet(
            "color:#2a6fd4; text-decoration:underline; padding:0 8px;"
        )
        self.update_label.setCursor(Qt.PointingHandCursor)
        self.update_label.hide()
        self.update_label.clicked.connect(self._open_update_dialog)
        self.statusBar().addPermanentWidget(self.update_label)

        # dsh 本体有新版本时的提示。特意和上面那个分开：Desktop 自己的升级走
        # update_dialog（下载安装包覆盖安装），dsh 的升级在维护面板里做，
        # 两者动作完全不同，混用一个入口会让用户搞不清点下去会发生什么。
        self.dsh_update_label = _ClickableLabel("")
        self.dsh_update_label.setStyleSheet(
            "color:#2a6fd4; text-decoration:underline; padding:0 8px;"
        )
        self.dsh_update_label.setCursor(Qt.PointingHandCursor)
        self.dsh_update_label.hide()
        self.dsh_update_label.clicked.connect(self._open_maintenance)
        self.statusBar().addPermanentWidget(self.dsh_update_label)

    # ---- 动作 ----
    def _start(self) -> None:
        self._set_status("正在启动 dsh…")
        self._eperm_hinted = False
        self._append_log(f"> 启动 dsh web（工作区 {self.manager.workspace}）")
        if not self.manager.start():
            self._set_status("启动失败，请查看日志")
            return
        self.start_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self._url_timer.start(20000)  # 20 秒兜底

    def _stop(self) -> None:
        self._append_log("> 停止 dsh")
        self.manager.stop()

    def _open_config(self) -> None:
        dialog = ConfigDialog(self)
        if dialog.exec():
            self.manager.set_workspace(dialog.workspace())
            self.manager.set_port(dialog.port())
            self._append_log("> 配置已保存（重启 dsh 后生效）")

    def _open_maintenance(self) -> None:
        """打开维护面板：概览 / 汉化 / 插件 / 升级 / 清理。"""
        MaintenanceDialog(self).exec()

    def _open_session_cleanup(self) -> None:
        SessionCleanupDialog(self.manager.workspace, self).exec()

    def _refresh(self) -> None:
        if self.manager.url:
            self.web.load(QUrl(self.manager.url))
        else:
            self.web.reload()

    def _open_external(self) -> None:
        if self.manager.url:
            QDesktopServices.openUrl(QUrl(self.manager.url))

    def _on_new_window_requested(self, request) -> None:
        QDesktopServices.openUrl(request.requestedUrl())

    def _on_feature_permission_requested(self, security_origin: QUrl, feature) -> None:
        """处理 dsh 页面的麦克风/摄像头等权限请求。

        必须对**每一个**请求都显式调用 setFeaturePermission，因为 QtWebEngine 的
        默认策略是拒绝；漏掉就等同于永久拒绝。这里只对本机 dsh 服务弹窗询问，
        对话里嵌的外部网页（预览页等）一律拒绝，不给媒体权限。
        """
        page = self.web.page()
        policy = QWebEnginePage.PermissionPolicy
        host = security_origin.host()
        label = _permission_label(feature)

        if host not in _LOCAL_HOSTS:
            page.setFeaturePermission(
                security_origin, feature, policy.PermissionDeniedByUser
            )
            return

        key = (security_origin.toString(), str(feature))
        if key in self._granted_permissions:
            page.setFeaturePermission(
                security_origin, feature, policy.PermissionGrantedByUser
            )
            return

        answer = QMessageBox.question(
            self,
            "DSH Desktop 权限请求",
            f"dsh 页面（{host}）请求使用「{label}」。\n\n是否允许？\n"
            "允许后本次运行期间不再重复询问。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        granted = answer == QMessageBox.Yes
        if granted:
            self._granted_permissions.add(key)
        page.setFeaturePermission(
            security_origin,
            feature,
            policy.PermissionGrantedByUser if granted else policy.PermissionDeniedByUser,
        )
        self._append_log(f"> dsh 请求「{label}」→ {'已允许' if granted else '已拒绝'}")

    def _open_about(self) -> None:
        AboutDialog(self).exec()

    # ---- 槽 ----
    def _on_started(self, url: str) -> None:
        self._url_timer.stop()
        self._set_status(f"运行中：{url}")
        self._append_log(f"> 服务地址：{url}")
        self.web.load(QUrl(url))
        self.open_action.setEnabled(True)

    def _on_log(self, line: str) -> None:
        self._append_log(line)
        self._check_permission_hint(line)

    def _check_permission_hint(self, line: str) -> None:
        low = line.lower()
        if self._eperm_hinted:
            return
        if "eperm" in low and (".dsh" in low or "cordis.yml" in low):
            self._eperm_hinted = True
            self._append_log(
                "[提示] dsh 因权限被拦截（EPERM，涉及 .dsh 配置文件）。"
                "这通常是被沙箱/安全软件拦截，而非文件权限本身的问题。"
                "请直接双击运行 exe，或在沙箱设置中放行 %USERPROFILE%\\.dsh 目录。"
            )

    def _on_stopped(self, code: int) -> None:
        self._url_timer.stop()
        self._set_status("已停止" if code == 0 else f"已停止（退出码 {code}）")
        self._append_log(f"> dsh 已退出（退出码 {code}）")
        self.start_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.open_action.setEnabled(False)

    def _on_url_timeout(self) -> None:
        if self.manager.is_running and self.manager.url is None:
            url = f"http://127.0.0.1:{self.manager.port}"
            self._append_log(f"> 未解析到地址，回退到 {url}")
            self.web.load(QUrl(url))
            self.open_action.setEnabled(True)
            self._set_status(f"运行中：{url}")

    # ---- 更新检测 ----
    def _check_updates(self, manual: bool = False) -> None:
        """检查更新；manual=True 时（用户点击按钮）失败/无更新会给反馈。"""
        self._manual_check = manual
        if manual:
            self._set_status("正在检查更新…")
        self._update_checker.check()

    def _on_update_check(self, info) -> None:
        latest = info["tag"]
        if not version_gt(latest, __version__):
            if self._manual_check:
                QMessageBox.information(self, "检查更新", f"已是最新版本（{__version__}）。")
                self._set_status("已停止")
            return
        self._last_update_info = info
        self.update_label.setText(f"发现新版本 {latest}，点击升级")
        self.update_label.show()
        self._open_update_dialog()

    def _on_update_check_failed(self, msg: str) -> None:
        # 自动检查失败保持静默，避免打扰；手动检查才提示。
        if self._manual_check:
            QMessageBox.warning(self, "检查更新", f"检查更新失败：{msg}")
            self._set_status("已停止")

    def _on_dsh_check(self, info) -> None:
        """dsh 本体有新版就在状态栏提示，点一下进维护面板处理。

        这个提示补的是个真实的盲区：Desktop 的「检查更新」只管它自己，
        dsh 装的是什么版本、该不该升，用户原本无从得知——而 dsh 在 npm 上的
        latest 标签还可能比已装的更旧，自己动手升反而会降级。
        """
        if not info.get("has_update"):
            return
        latest = info.get("latest")
        current = info.get("current")
        self.dsh_update_label.setText(f"dsh 可升级到 {latest}")
        self.dsh_update_label.show()
        self._append_log(
            f"> 检测到 dsh 新版本 {latest}（当前 {current}）。"
            "点状态栏提示，或打开「维护」→「升级」处理。"
        )

    def _open_update_dialog(self) -> None:
        if not self._last_update_info:
            return
        if self._update_dialog is not None and self._update_dialog.isVisible():
            self._update_dialog.raise_()
            self._update_dialog.activateWindow()
            return
        self._update_dialog = UpdateDialog(self, self._last_update_info)
        self._update_dialog.exec()

    # ---- 辅助 ----
    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    # ---- 窗口状态 ----
    def _restore_geometry(self) -> None:
        geo = self._settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.setValue("geometry", self.saveGeometry())
        if self.manager.is_running:
            self._append_log("> 关闭时停止 dsh")
            self.manager.stop()
        super().closeEvent(event)
