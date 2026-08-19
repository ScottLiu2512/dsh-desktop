"""模型 / API 设置对话框。"""

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import config_store


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(440)

        settings = QSettings("dsh-gui", "dsh-gui")

        # API Key
        self.api_key_edit = QLineEdit(config_store.get_api_key())
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("sk-...")
        self.show_key_check = QCheckBox("显示")
        self.show_key_check.toggled.connect(self._toggle_key_visibility)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit)
        key_row.addWidget(self.show_key_check)

        # 模型
        self.model_combo = QComboBox()
        self.model_combo.addItems(config_store.DEFAULT_MODELS)
        cfg = config_store.get_default_model_config()
        current_model = cfg.get("model", config_store.DEFAULT_MODELS[0])
        if current_model not in config_store.DEFAULT_MODELS:
            self.model_combo.addItem(current_model)
        self.model_combo.setCurrentText(current_model)

        # 推理强度
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(config_store.REASONING_EFFORTS)
        current_effort = cfg.get("reasoningEffort", "high")
        if current_effort in config_store.REASONING_EFFORTS:
            self.effort_combo.setCurrentText(current_effort)

        # 工作区目录
        self.workspace_edit = QLineEdit(settings.value("workspace", str(Path.home())))
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_workspace)
        ws_row = QHBoxLayout()
        ws_row.addWidget(self.workspace_edit)
        ws_row.addWidget(browse_btn)

        # 端口
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(settings.value("port", 3080)))

        form = QFormLayout()
        form.addRow("API Key（DEEPSEEK_API_KEY）", key_row)
        form.addRow("模型", self.model_combo)
        form.addRow("推理强度", self.effort_combo)
        form.addRow("工作区目录", ws_row)
        form.addRow("服务端口", self.port_spin)

        hint = QLabel(
            "API Key 会写入 DSH 的 .credentials.yaml；模型与推理强度写入 settings.yaml。\n"
            "保存后重启 dsh（停止再启动）即可生效。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        save_btn = QPushButton("保存")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(cancel_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(btn_row)

    def _toggle_key_visibility(self, visible: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)

    def _browse_workspace(self) -> None:
        start = self.workspace_edit.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "选择工作区目录", start)
        if chosen:
            self.workspace_edit.setText(chosen)

    def _save(self) -> None:
        config_store.set_api_key(self.api_key_edit.text())
        config_store.set_default_model_config(
            self.model_combo.currentText(), self.effort_combo.currentText()
        )
        settings = QSettings("dsh-gui", "dsh-gui")
        settings.setValue("workspace", self.workspace_edit.text())
        settings.setValue("port", self.port_spin.value())
        self.accept()

    def workspace(self) -> str:
        return self.workspace_edit.text()

    def port(self) -> int:
        return self.port_spin.value()
