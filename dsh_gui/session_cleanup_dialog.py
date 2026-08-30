"""会话清理对话框：列出当前工作区下的 dsh 历史会话，选择删除。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import session_cleanup

EMPTY_BADGE = "空"
HAS_CONTENT_BADGE = "有内容"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


class SessionCleanupDialog(QDialog):
    def __init__(self, workspace: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("会话清理")
        self.setMinimumSize(560, 420)

        workspace_label = QLabel("工作区：")
        self.workspace_combo = QComboBox()
        self.workspace_combo.setMinimumWidth(280)
        self.workspace_combo.currentIndexChanged.connect(self._on_workspace_changed)
        self._hint = QLabel()
        self._hint.setStyleSheet("color: gray;")
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(workspace_label)
        workspace_row.addWidget(self.workspace_combo)
        workspace_row.addWidget(self._hint)
        workspace_row.addStretch(1)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "创建时间", "大小", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        select_empty_btn = QPushButton("全选空会话")
        select_empty_btn.clicked.connect(self._select_empty)
        select_none_btn = QPushButton("取消全选")
        select_none_btn.clicked.connect(self._select_none)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._reload_workspaces)

        top_row = QHBoxLayout()
        top_row.addWidget(select_empty_btn)
        top_row.addWidget(select_none_btn)
        top_row.addStretch(1)
        top_row.addWidget(refresh_btn)

        self.delete_btn = QPushButton("删除选中")
        self.delete_btn.setStyleSheet("color: #c0392b;")
        self.delete_btn.clicked.connect(self._delete_selected)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        bottom_row.addWidget(self.delete_btn)
        bottom_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(workspace_row)
        layout.addLayout(top_row)
        layout.addWidget(self.table)
        layout.addLayout(bottom_row)

        self._all_sessions = []
        self._sessions = []
        self._reload_workspaces(select=workspace)

    def _reload_workspaces(self, select: str = "") -> None:
        """重新扫描全部会话、重建工作区下拉框；再据此刷新表格。

        ``select`` 只在首次打开（构造函数传入当前配置的工作区）或明确指定时
        使用；点「刷新」按钮触发时留空，保留用户当前选中的工作区不变
        ——即使它此刻一个会话都没有，也要保留在下拉框里，不能让用户选着选着
        选项就消失了。
        """
        target = select or self.workspace_combo.currentText()
        self._all_sessions = session_cleanup.list_all_sessions()
        workspaces = session_cleanup.list_workspaces(self._all_sessions)
        if target and target not in workspaces:
            workspaces.insert(0, target)

        self.workspace_combo.blockSignals(True)
        self.workspace_combo.clear()
        self.workspace_combo.addItems(workspaces)
        if target:
            index = self.workspace_combo.findText(target)
            if index >= 0:
                self.workspace_combo.setCurrentIndex(index)
        self.workspace_combo.blockSignals(False)

        self._reload_table()

    def _on_workspace_changed(self, _index: int) -> None:
        self._reload_table()

    def _reload_table(self) -> None:
        workspace = self.workspace_combo.currentText()
        self._sessions = session_cleanup.list_sessions_for_workspace(
            workspace, self._all_sessions
        )
        self.table.setRowCount(len(self._sessions))
        for row, info in enumerate(self._sessions):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check_item)
            self.table.setItem(
                row, 1, QTableWidgetItem(info.mtime.strftime("%Y-%m-%d %H:%M"))
            )
            self.table.setItem(row, 2, QTableWidgetItem(_format_size(info.size)))
            status_item = QTableWidgetItem(
                EMPTY_BADGE if info.is_empty else HAS_CONTENT_BADGE
            )
            if info.is_empty:
                status_item.setForeground(QColor(Qt.gray))
            self.table.setItem(row, 3, status_item)
        self.table.resizeColumnsToContents()
        if not self._sessions:
            self._hint.setText("没有找到会话")
        else:
            self._hint.setText(f"共 {len(self._sessions)} 个会话")

    def _select_empty(self) -> None:
        for row, info in enumerate(self._sessions):
            self.table.item(row, 0).setCheckState(
                Qt.Checked if info.is_empty else Qt.Unchecked
            )

    def _select_none(self) -> None:
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Unchecked)

    def _checked_sessions(self):
        return [
            info
            for row, info in enumerate(self._sessions)
            if self.table.item(row, 0).checkState() == Qt.Checked
        ]

    def _delete_selected(self) -> None:
        targets = self._checked_sessions()
        if not targets:
            QMessageBox.information(self, "会话清理", "没有勾选任何会话。")
            return
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要永久删除选中的 {len(targets)} 个会话吗？此操作无法撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        errors = []
        for info in targets:
            try:
                session_cleanup.delete_session(info)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{info.id[:20]}…：{exc}")
        self._reload_workspaces()
        if errors:
            QMessageBox.warning(self, "部分删除失败", "\n".join(errors))
