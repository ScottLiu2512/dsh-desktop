"""DSH 维护面板：概览 / 汉化 / 插件 / 升级 / 清理。

把原先散落在 ``D:\\deepseek`` 里靠手工跑脚本维护的事情收进 GUI：dsh 装在哪、
装了什么插件、汉化有没有掉、有没有新版、升级留下的垃圾清没清。

风格与 ``config_dialog`` / ``session_cleanup_dialog`` 保持一致：顶部操作行、
中间表格或文本区、底部按钮右对齐、灰色小字提示。
"""

import shutil
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, cn_patches, dsh_env
from .upgrade_worker import DshVersionCheck, ShellTask, UpgradeWorker, compare_semver

STATE_TEXT = {
    cn_patches.STATE_APPLIED: "已汉化",
    cn_patches.STATE_PENDING: "待汉化",
    cn_patches.STATE_NOT_FOUND: "未找到",
}


def _gray(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


def _output_box() -> QPlainTextEdit:
    box = QPlainTextEdit()
    box.setReadOnly(True)
    box.setMaximumBlockCount(2000)
    return box


class MaintenanceDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DSH 维护")
        self.setMinimumSize(780, 580)

        self._worker: "UpgradeWorker | None" = None
        self._task: "ShellTask | None" = None
        self._target_version = ""
        self._checker = DshVersionCheck(self)
        self._checker.finished.connect(self._on_version_checked)
        self._checker.failed.connect(self._on_version_failed)
        self._snapshot: "dsh_env.EnvSnapshot | None" = None
        self._cn_entries: "list[cn_patches.PatchEntry]" = []
        self._checking = False

        tabs = QTabWidget(self)
        tabs.addTab(self._build_overview_tab(), "概览")
        tabs.addTab(self._build_cn_tab(), "汉化")
        tabs.addTab(self._build_plugin_tab(), "插件")
        tabs.addTab(self._build_upgrade_tab(), "升级")
        tabs.addTab(self._build_stash_tab(), "清理")

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addLayout(bottom)

        self._refresh_all()

    # ---- 通用 ----
    def _manager(self):
        """主窗口的 DshManager（升级前需要停服务）。"""
        return getattr(self.parent(), "manager", None)

    def _refresh_all(self) -> None:
        self._refresh_overview()
        self._check_cn()
        self._refresh_plugins()
        self._refresh_stash()

    # ================= 概览 =================
    def _build_overview_tab(self) -> QWidget:
        page = QWidget()
        self.overview_box = _output_box()

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_overview)
        copy_btn = QPushButton("复制报告")
        copy_btn.clicked.connect(self._copy_report)

        top = QHBoxLayout()
        top.addWidget(_gray("部署问题可以把这份报告直接贴出来，省去逐条问环境。"))
        top.addStretch(1)
        top.addWidget(refresh_btn)
        top.addWidget(copy_btn)

        layout = QVBoxLayout(page)
        layout.addLayout(top)
        layout.addWidget(self.overview_box)
        return page

    def _refresh_overview(self) -> None:
        self._snapshot = dsh_env.snapshot()
        cn_summary = cn_patches.summarize(cn_patches.check())
        self.overview_box.setPlainText(
            dsh_env.format_report(self._snapshot, __version__, cn_summary)
        )

    def _copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.overview_box.toPlainText())
        QMessageBox.information(self, "概览", "报告已复制到剪贴板。")

    # ================= 汉化 =================
    def _build_cn_tab(self) -> QWidget:
        page = QWidget()

        self.cn_table = QTableWidget(0, 3)
        self.cn_table.setHorizontalHeaderLabels(["命令包", "英文原文", "状态"])
        self.cn_table.horizontalHeader().setStretchLastSection(True)
        self.cn_table.verticalHeader().setVisible(False)
        self.cn_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cn_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        check_btn = QPushButton("检测")
        check_btn.clicked.connect(self._check_cn)
        self.cn_apply_btn = QPushButton("应用汉化")
        self.cn_apply_btn.clicked.connect(self._apply_cn)
        self.cn_revert_btn = QPushButton("还原")
        self.cn_revert_btn.clicked.connect(self._revert_cn)

        top = QHBoxLayout()
        top.addWidget(check_btn)
        top.addWidget(self.cn_apply_btn)
        top.addWidget(self.cn_revert_btn)
        top.addStretch(1)

        self.cn_output = _output_box()
        self.cn_output.setMaximumHeight(120)

        layout = QVBoxLayout(page)
        layout.addWidget(
            _gray(
                "dsh 官方界面已自带中文，这里只处理敲 / 后菜单里仍为英文的命令说明。"
                "修改前会自动备份，改完用 node --check 校验，不过会自动回滚。"
                "dsh 升级后这些描述会被覆盖回英文，重打一次即可。"
            )
        )
        layout.addLayout(top)
        layout.addWidget(self.cn_table)
        layout.addWidget(self.cn_output)
        return page

    def _check_cn(self) -> None:
        self._cn_entries = cn_patches.check()
        self.cn_table.setRowCount(len(self._cn_entries))
        for row, entry in enumerate(self._cn_entries):
            package_item = QTableWidgetItem(entry.package)
            self.cn_table.setItem(row, 0, package_item)

            if entry.error:
                self.cn_table.setItem(row, 1, QTableWidgetItem(entry.error))
                status_item = QTableWidgetItem("不可用")
                status_item.setForeground(QColor(Qt.gray))
                self.cn_table.setItem(row, 2, status_item)
                continue

            for item in entry.items:
                self.cn_table.setItem(row, 1, QTableWidgetItem(item.english))
                status_item = QTableWidgetItem(STATE_TEXT.get(item.state, item.state))
                if item.state == cn_patches.STATE_PENDING:
                    status_item.setForeground(QColor("#c0392b"))
                elif item.state == cn_patches.STATE_APPLIED:
                    status_item.setForeground(QColor("#1e8e3e"))
                else:
                    status_item.setForeground(QColor(Qt.gray))
                self.cn_table.setItem(row, 2, status_item)

        self.cn_table.resizeColumnsToContents()
        summary = cn_patches.summarize(self._cn_entries)
        self.cn_output.setPlainText(f"检测完成：{summary}")
        pending = cn_patches.needs_apply(self._cn_entries)
        self.cn_apply_btn.setEnabled(pending)

    def _apply_cn(self) -> None:
        outcomes = cn_patches.apply()
        lines = [
            f"{'成功' if o.action == 'applied' else '跳过' if o.action == 'skipped' else '失败'}"
            f"  {o.package}：{o.detail}"
            for o in outcomes
        ]
        self.cn_output.setPlainText("\n".join(lines))
        self._check_cn()
        applied = sum(1 for o in outcomes if o.action == "applied")
        self.cn_output.appendPlainText(
            f"\n本次修改 {applied} 个包。重启 dsh 后生效（这些串来自 host 侧）。"
        )

    def _revert_cn(self) -> None:
        outcomes = cn_patches.revert()
        lines = [f"{o.action}  {o.package}：{o.detail}" for o in outcomes]
        self.cn_output.setPlainText("\n".join(lines))
        self._check_cn()

    # ================= 插件 =================
    def _build_plugin_tab(self) -> QWidget:
        page = QWidget()

        self.plugin_table = QTableWidget(0, 4)
        self.plugin_table.setHorizontalHeaderLabels(["插件", "来源", "来源可用", "已解析"])
        self.plugin_table.horizontalHeader().setStretchLastSection(True)
        self.plugin_table.verticalHeader().setVisible(False)
        self.plugin_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.plugin_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_plugins)
        self.repair_btn = QPushButton("修复重装")
        self.repair_btn.clicked.connect(self._repair_plugin)

        top = QHBoxLayout()
        top.addWidget(refresh_btn)
        top.addWidget(self.repair_btn)
        top.addStretch(1)

        self.plugin_output = _output_box()
        self.plugin_output.setMaximumHeight(120)

        self.patch_label = _gray("")

        layout = QVBoxLayout(page)
        layout.addWidget(
            _gray(
                "这里只做诊断和修复，不是插件市场。"
                "「来源可用」为否说明插件原本的目录已经不在了（换机器、挪了位置），"
                "此时无法重装，需要重新获取插件本体。"
            )
        )
        layout.addLayout(top)
        layout.addWidget(self.plugin_table)
        layout.addWidget(self.patch_label)
        layout.addWidget(self.plugin_output)
        return page

    def _refresh_plugins(self) -> None:
        snap = dsh_env.snapshot()
        self._snapshot = snap
        plugins = snap.plugins

        self.plugin_table.setRowCount(len(plugins))
        for row, plugin in enumerate(plugins):
            self.plugin_table.setItem(row, 0, QTableWidgetItem(plugin.name))
            self.plugin_table.setItem(row, 1, QTableWidgetItem(plugin.source or "—"))

            exists_item = QTableWidgetItem("是" if plugin.source_exists else "否")
            if not plugin.source_exists:
                exists_item.setForeground(QColor("#c0392b"))
            self.plugin_table.setItem(row, 2, exists_item)

            installed_item = QTableWidgetItem("是" if plugin.installed else "否")
            if not plugin.installed:
                installed_item.setForeground(QColor("#c0392b"))
            self.plugin_table.setItem(row, 3, installed_item)

        self.plugin_table.resizeColumnsToContents()

        if snap.patch_entries:
            self.patch_label.setText(
                "补丁层（cordis.patch.yml）：" + "、".join(snap.patch_entries)
            )
        else:
            self.patch_label.setText("补丁层（cordis.patch.yml）：无")

        if not plugins:
            self.plugin_output.setPlainText("当前 profile 没有安装第三方插件。")
        elif snap.missing_bundles:
            self.plugin_output.setPlainText(
                "以下 bundle 已登记但解析不到，多半是装了没生效：\n  "
                + "\n  ".join(snap.missing_bundles)
            )
        else:
            self.plugin_output.setPlainText("插件状态正常。")

        self.repair_btn.setEnabled(any(p.source_exists for p in plugins))

    def _repair_plugin(self) -> None:
        row = self.plugin_table.currentRow()
        if row < 0 or self._snapshot is None or row >= len(self._snapshot.plugins):
            QMessageBox.information(self, "插件", "请先在表格里选中一个插件。")
            return
        plugin = self._snapshot.plugins[row]
        if not plugin.source_exists or plugin.path is None:
            QMessageBox.warning(
                self,
                "插件",
                f"{plugin.name} 的来源路径不存在：\n{plugin.source}\n\n"
                "需要重新获取插件本体后手动安装。",
            )
            return

        reply = QMessageBox.question(
            self,
            "修复重装",
            f"重新安装插件 {plugin.name}？\n来源：{plugin.path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        cmdline = f'dsh plugin --profile web add "{plugin.path}"'
        self.plugin_output.clear()
        self.repair_btn.setEnabled(False)
        self._task = ShellTask(cmdline, parent=self)
        self._task.log_line.connect(self.plugin_output.appendPlainText)
        self._task.done.connect(self._on_repair_done)
        self._task.start()

    def _on_repair_done(self, ok: bool, message: str) -> None:
        self.plugin_output.appendPlainText("")
        self.plugin_output.appendPlainText(("> 完成：" if ok else "> 失败：") + message)
        if not ok:
            self.plugin_output.appendPlainText(
                "提示：如果是 pnpm 报 EPERM / 锁文件错误，重试一次通常就好。"
            )
        self._refresh_plugins()

    # ================= 升级 =================
    def _build_upgrade_tab(self) -> QWidget:
        page = QWidget()

        self.current_label = QLabel("当前版本：—")
        self.latest_label = QLabel("最新版本：—（点「检查更新」）")
        self.latest_label.setWordWrap(True)

        self.reapply_check = QCheckBox("升级后自动重打汉化补丁")
        self.reapply_check.setChecked(True)

        self.check_btn = QPushButton("检查更新")
        self.check_btn.clicked.connect(self._check_dsh_update)
        self.upgrade_btn = QPushButton("升级 dsh")
        self.upgrade_btn.clicked.connect(self._start_upgrade)
        self.upgrade_btn.setEnabled(False)

        top = QHBoxLayout()
        top.addWidget(self.check_btn)
        top.addWidget(self.upgrade_btn)
        top.addStretch(1)

        self.upgrade_output = _output_box()

        layout = QVBoxLayout(page)
        layout.addWidget(self.current_label)
        layout.addWidget(self.latest_label)
        layout.addLayout(top)
        layout.addWidget(self.reapply_check)
        layout.addWidget(
            _gray(
                "注意：dsh 在 npm 上的 latest 标签可能比已装的版本还旧"
                "（例如 latest 指向 0.1.5-rc.2，而实际最新是 0.1.6-alpha.2）。"
                "直接跑 npm install 会降级，所以这里始终按语义化版本号挑真正最新的那个，"
                "安装时显式带上版本号。升级会先停止 dsh。"
            )
        )
        layout.addWidget(self.upgrade_output)
        return page

    def _check_dsh_update(self) -> None:
        if self._checking:
            return
        self._checking = True
        self.check_btn.setEnabled(False)
        self.latest_label.setText("最新版本：查询中…")
        self._checker.check()

    def _on_version_checked(self, info: dict) -> None:
        self._checking = False
        self.check_btn.setEnabled(True)
        self._target_version = info.get("latest") or ""
        current = info.get("current")
        self.current_label.setText(f"当前版本：{current or '（未检测到 dsh）'}")

        if not current:
            self.latest_label.setText(
                f"最新版本：{self._target_version}\n未检测到已安装的 dsh，无法比较。"
            )
            self.upgrade_btn.setEnabled(bool(self._target_version))
            return

        if info.get("has_update"):
            text = f"最新版本：{self._target_version}（共 {info.get('total', 0)} 个已发布版本）"
            tag = info.get("latest_tag") or ""
            if tag and compare_semver(tag, self._target_version) < 0:
                text += f"\n注意：npm 的 latest 标签指向 {tag}，比它旧——直接 npm install 会降级。"
            self.latest_label.setText(text)
            self.upgrade_btn.setEnabled(True)
        else:
            self.latest_label.setText(f"已是最新版本（{current}）。")
            self.upgrade_btn.setEnabled(False)

    def _on_version_failed(self, message: str) -> None:
        self._checking = False
        self.check_btn.setEnabled(True)
        self.latest_label.setText(f"检查失败：{message}")

    def _start_upgrade(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "升级 dsh", "升级正在进行中，请稍候。")
            return
        target = getattr(self, "_target_version", "")
        if not target:
            QMessageBox.information(self, "升级 dsh", "请先点「检查更新」。")
            return

        manager = self._manager()
        port = getattr(manager, "port", dsh_env.DEFAULT_PORT) if manager else dsh_env.DEFAULT_PORT
        running = bool(manager is not None and manager.is_running)

        prompt = f"将升级 dsh 到 {target}。"
        if running:
            prompt += "\n\n升级前需要先停止 dsh（运行中的进程会占用旧版原生模块，导致升级残留）。要继续吗？"
        reply = QMessageBox.question(
            self, "升级 dsh", prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.upgrade_output.clear()
        if running and manager is not None:
            self._append_upgrade("> 停止 dsh…")
            manager.stop()
            if not self._wait_port_free(port):
                self._append_upgrade(
                    f"停止 dsh 超时：端口 {port} 仍在监听。"
                    "请手动停止后再试，否则升级可能留下残留。"
                )
                return
            self._append_upgrade(f"> 端口 {port} 已释放")

        self.upgrade_btn.setEnabled(False)
        self._worker = UpgradeWorker(target, self.reapply_check.isChecked(), self)
        self._worker.log_line.connect(self._append_upgrade)
        self._worker.done.connect(self._on_upgrade_done)
        self._worker.start()

    def _wait_port_free(self, port: int, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not dsh_env.port_listening(port):
                return True
            time.sleep(0.3)
        return False

    def _append_upgrade(self, line: str) -> None:
        self.upgrade_output.appendPlainText(line)

    def _on_upgrade_done(self, ok: bool, message: str) -> None:
        self._append_upgrade("")
        self._append_upgrade(("> 完成：" if ok else "> 失败：") + message)
        self.upgrade_btn.setEnabled(ok)
        self._refresh_all()

        if ok:
            reply = QMessageBox.question(
                self,
                "升级完成",
                f"{message}\n\n现在重启 dsh 吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                manager = self._manager()
                if manager is not None:
                    self._append_upgrade("> 启动 dsh…")
                    manager.start()
        else:
            QMessageBox.warning(self, "升级 dsh", message)

    # ================= 清理 =================
    def _build_stash_tab(self) -> QWidget:
        page = QWidget()

        self.stash_table = QTableWidget(0, 2)
        self.stash_table.setHorizontalHeaderLabels(["残留目录", "大小"])
        self.stash_table.horizontalHeader().setStretchLastSection(True)
        self.stash_table.verticalHeader().setVisible(False)
        self.stash_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stash_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        scan_btn = QPushButton("扫描")
        scan_btn.clicked.connect(self._refresh_stash)
        self.clean_btn = QPushButton("删除全部")
        self.clean_btn.setStyleSheet("color: #c0392b;")
        self.clean_btn.clicked.connect(self._clean_stash)

        top = QHBoxLayout()
        top.addWidget(scan_btn)
        top.addWidget(self.clean_btn)
        top.addStretch(1)

        self.stash_hint = _gray("")
        self.stash_output = _output_box()
        self.stash_output.setMaximumHeight(120)

        layout = QVBoxLayout(page)
        layout.addWidget(
            _gray(
                "dsh 升级时如果服务还在运行，旧版原生模块（sharp / koffi）被占用，"
                "npm 删不掉就会留下这些 .dsh- 暂存目录，每个约 20 MB。"
                "清理前 dsh 必须已经停止，否则删不掉。"
            )
        )
        layout.addLayout(top)
        layout.addWidget(self.stash_hint)
        layout.addWidget(self.stash_table)
        layout.addWidget(self.stash_output)
        return page

    def _refresh_stash(self) -> None:
        entries = dsh_env.stash_dirs()
        self.stash_table.setRowCount(len(entries))
        total = 0
        for row, entry in enumerate(entries):
            total += entry["size"]
            self.stash_table.setItem(row, 0, QTableWidgetItem(entry["path"].name))
            self.stash_table.setItem(row, 1, QTableWidgetItem(dsh_env.format_size(entry["size"])))
        self.stash_table.resizeColumnsToContents()

        manager = self._manager()
        port = getattr(manager, "port", dsh_env.DEFAULT_PORT) if manager else dsh_env.DEFAULT_PORT
        listening = dsh_env.port_listening(port)

        if not entries:
            self.stash_hint.setText("没有残留目录。")
        else:
            self.stash_hint.setText(
                f"共 {len(entries)} 个，合计 {dsh_env.format_size(total)}。"
            )
        if listening:
            self.stash_hint.setText(
                self.stash_hint.text() + f"　⚠ dsh 正在运行（端口 {port}），请先停止再清理。"
            )
        self.clean_btn.setEnabled(bool(entries) and not listening)

    def _clean_stash(self) -> None:
        entries = dsh_env.stash_dirs()
        if not entries:
            return
        total = sum(entry["size"] for entry in entries)
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"将删除 {len(entries)} 个残留目录，释放约 {dsh_env.format_size(total)}。\n"
            "这些是升级失败留下的临时文件，删除不影响 dsh 本身。要继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        root = dsh_env.npm_global_root()
        scope = (root / "@deepseek-ai").resolve() if root else None
        lines = []
        for entry in entries:
            path = Path(entry["path"])
            try:
                resolved = path.resolve()
            except OSError as exc:
                lines.append(f"跳过 {path.name}：{exc}")
                continue
            # 双重校验，避免路径异常时误删别处。
            if scope is None or resolved.parent != scope or not resolved.name.startswith(
                dsh_env.STASH_PREFIX
            ):
                lines.append(f"跳过（路径校验不通过）：{resolved}")
                continue
            shutil.rmtree(resolved, ignore_errors=True)
            lines.append(f"{'已删除' if not resolved.exists() else '未能删除'} {resolved.name}")

        self.stash_output.setPlainText("\n".join(lines))
        self._refresh_stash()

    # ---- 关闭 ----
    def closeEvent(self, event) -> None:
        running = [
            name
            for name, task in (("升级", self._worker), ("插件安装", self._task))
            if task is not None and task.isRunning()
        ]
        if running:
            reply = QMessageBox.question(
                self,
                f"{running[0]}进行中",
                f"{'、'.join(running)}还在进行，现在关闭会中断它"
                "（npm / pnpm 可能停在半途）。确定关闭吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            for task in (self._worker, self._task):
                if task is not None and task.isRunning():
                    task.cancel()
        super().closeEvent(event)
