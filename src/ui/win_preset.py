"""预设槽位与设置持久化: 读盘写盘、导入导出、参数收集与恢复默认。"""
from PySide6.QtWidgets import QMessageBox
from core import app as core
from .dialogs.base import _message, _open_file_dialog, _save_file_dialog
from .specs import _BACKEND_KEYS, migrate_preset
import json
import logging
import os
from .winconst import ILLEGAL_FN_CHARS, _NOT_PERSISTED, _PRESET_FORMAT, _PRESET_MACHINE_KEYS, _PRESET_SLOTS


class PresetMixin:

    def _load_params(self):
        p = dict(core.default_params())
        saved = self.state.settings or {}
        for k, v in saved.items():
            if k in p:                      # 只吃已知参数; 未知键留在 settings.json 里不动
                p[k] = v
        try:
            p["model_map"] = core.load_model_map(p.get("logo_dir") or core.DEFAULT_LOGO_DIR)
        except Exception:
            p["model_map"] = {}
        return p

    def _reload_presets(self, view):
        # 把 10 个槽位列进 view 的下拉(显示各自存着的方案名, 空槽显示「— 空 —」)
        # view 是 PresetDialog(控件在它那儿); 预设数据缓存在窗口的 self._preset_data
        # 键是槽位号(字符串), 与 core.preset_path(slot) 的槽位一致
        try:
            self._preset_data = self.state.load_presets() or {}
        except Exception as e:                      # noqa: BLE001
            logging.warning(f"[预设] 读取失败: {e}")
            self._preset_data = {}
        cur = view.current_slot()
        view.combo.clear()
        for i in range(1, _PRESET_SLOTS + 1):
            key = str(i)
            rec = self._preset_data.get(key) or {}
            name = rec.get("_preset_name") if isinstance(rec, dict) else None
            view.combo.addItem(self.tr("槽位 {slot}：{name}").format(
                slot=i, name=name or self.tr("— 空 —")), key)
        if cur:
            j = view.combo.findData(cur)
            if j >= 0:
                view.combo.setCurrentIndex(j)

    def _preset_load(self, view):
        key = view.current_slot()
        p = self._preset_data.get(key) if key else None
        if not isinstance(p, dict):
            self._notify(self.tr("该槽位为空，无法加载"))
            return
        name = p.get("_preset_name") or key
        if _message(self, QMessageBox.Icon.Question, self.tr("加载预设"),
                    self.tr("将把当前所有参数覆盖为预设「{name}」的值。继续？").format(name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._undo_step("__preset_load__", f"加载预设「{name}」",
                        lambda: self._apply_params(migrate_preset(p)))
        view.set_name(name)
        self._notify(self.tr("已加载预设：{name}").format(name=name))

    def _preset_payload(self):
        # 存进预设的参数(与 settings 的口径刻意不同)
        p = dict(self._params)
        p["canvas_ratio_mode"] = self._params.get("canvas_ratio_mode", "original")
        return p

    def _preset_save(self, view):
        # 把当前调参存进当前选中的槽位(方案名只是存在文件里的一个名字)
        key = view.current_slot()
        if not key:
            return
        name = view.name_text()
        if not name:
            self._notify(self.tr("请先输入方案名后再保存"))
            return
        cur = self._preset_data.get(key) or {}
        cur_name = cur.get("_preset_name") if isinstance(cur, dict) else None
        if cur_name and cur_name != name:
            if _message(self, QMessageBox.Icon.Question, self.tr("保存预设"),
                        self.tr("槽位 {slot} 已有方案「{name}」，保存会覆盖它。继续？")
                        .format(slot=key, name=cur_name),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        d = self.state.preset_save(key, self._preset_payload(), name)
        if not d.get("ok"):
            _message(self, QMessageBox.Icon.Warning, self.tr("保存预设"),
                     str(d.get("error") or ""))
            return
        view.reload()
        view.combo.setCurrentIndex(view.combo.findData(key))
        logging.info(f"[预设] 已保存: 槽位 {key}「{name}」-> {core.preset_path(key)}")
        self._notify(self.tr("已保存到槽位 {slot}：{name}").format(slot=key, name=name))

    def _export_preset(self, view):
        # 把当前选中的预设导出成一个 .json(给别人用)
        key = view.current_slot()
        rec = self._preset_data.get(key) if key else None
        if not isinstance(rec, dict):
            self._notify(self.tr("该槽位为空，没有可导出的方案"))
            return
        name = rec.get("_preset_name") or f"方案 {key}"
        # 文件名仅作对话框的建议值, 因此替换 Windows 不允许的字符(程序不依赖该名称识别内容)
        suggest = "".join("_" if c in ILLEGAL_FN_CHARS else c for c in name).strip() or f"preset{key}"
        path = _save_file_dialog(self, self.tr("导出预设"), "", f"{suggest}.json",
                                 self.tr("预设 (*.json)"))
        if not path:
            return
        data = {k: v for k, v in rec.items() if k not in _PRESET_MACHINE_KEYS}
        data["_format"] = _PRESET_FORMAT
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:                      # noqa: BLE001 - 写不出去就报一句, 不弹窗
            self._notify(self.tr("导出失败：{err}").format(err=e))
            logging.warning(f"[预设] 导出失败 {path}: {e}")
            return
        logging.info(f"[预设] 已导出槽位 {key}「{name}」-> {path} (剔除机器路径) 共 {len(data)} 键")
        self._notify(self.tr("已导出方案「{name}」").format(name=name))

    def _import_preset(self, view):
        # 把一个 .json 导入成预设(预设名取自文件里的 _preset_name, 没有再退回文件名)
        path = _open_file_dialog(self, self.tr("导入预设"), "",
                                 self.tr("预设 (*.json);;所有文件 (*)"))
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:                      # noqa: BLE001
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     self.tr("无法读取该文件：\n{err}").format(err=e))
            return
        if not isinstance(data, dict) or not data:
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"), self.tr("该文件中没有方案数据。"))
            return
        # 只吃"单个方案": 判据是有没有方案参数键(排除旧版整份预设文件)
        if not any(k in data for k in ("bg_color", "canvas_ratio_mode", "canvas_ratio",
                                      "font_path", "layout_mode")):
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     self.tr("该文件不是单个方案格式（可能是旧版 presets.json）。"
                             "请先用「导出预设」导出单个方案文件，再导入。"))
            return
        name = str(data.get("_preset_name") or os.path.splitext(os.path.basename(path))[0])
        payload = {k: v for k, v in data.items()
                   if k not in _PRESET_MACHINE_KEYS and k != "_format"}
        # 导入目标是当前选中的槽位: 想换目标先在下拉里选好槽位
        key = view.current_slot()
        if not key:
            return
        cur = self._preset_data.get(key) or {}
        cur_name = cur.get("_preset_name") if isinstance(cur, dict) else None
        if cur_name and _message(self, QMessageBox.Icon.Question, self.tr("导入预设"),
                                 self.tr("槽位 {slot} 已有方案「{name}」，导入会覆盖它。继续？")
                                 .format(slot=key, name=cur_name),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = self.state.preset_save(key, migrate_preset(payload), name)
        if not d.get("ok"):
            _message(self, QMessageBox.Icon.Warning, self.tr("导入预设"),
                     str(d.get("error") or ""))
            return
        view.reload()
        view.combo.setCurrentIndex(view.combo.findData(key))
        logging.info(f"[预设] 已导入 {path} -> 槽位 {key}「{name}」（{'覆盖' if cur_name else '空槽'}）")
        self._notify(self.tr("已导入方案「{name}」到槽位 {slot}").format(name=name, slot=key))

    def _preset_delete(self, view):
        key = view.current_slot()
        if not key or not isinstance(self._preset_data.get(key), dict):
            self._notify(self.tr("该槽位为空，无需删除"))
            return
        name = self._preset_data[key].get("_preset_name") or key
        if _message(self, QMessageBox.Icon.Question, self.tr("删除预设"),
                    self.tr("确定删除「{name}」？").format(name=name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = self.state.preset_delete(key)
        if not d.get("ok"):
            _message(self, QMessageBox.Icon.Warning, self.tr("删除预设"), str(d.get("error") or ""))
            return
        view.reload()
        view.set_name("")
        self._notify(self.tr("已删除槽位 {slot}：{name}").format(slot=key, name=name))

    def _apply_params(self, src):
        # 把整份参数铺回(加载预设 / 恢复默认 / 撤销重做), 仅接受已知键
        map_before = dict(self._params.get("model_map") or {})
        changed = set()
        for key, val in src.items():
            if key not in self._params:            # 未知键(如预设里的 _preset_name)忽略
                continue
            self._params[key] = val
            changed.add(key)
            row = self._rows.get(key)
            if row is not None:
                row.set_value(val)
        self._sync_mirror_rows(changed)
        self._sync_visibility()
        self._persist_model_map(map_before)
        self._update_effective_model()      # 「水印将显示：X」依赖映射表, 跟着刷一次
        self._touch()

    def _persist_model_map(self, before):
        # 整批铺参数改动过映射表时, 将内存中的表写回 logo 目录下的 model_map.json
        # 仅在内容确实变化时写入: _params 里的 model_map 只是内存副本
        cur = dict(self._params.get("model_map") or {})
        if cur == before:
            return
        try:
            core.save_model_map(self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR, cur)
        except Exception as e:                      # noqa: BLE001
            logging.warning(f"[映射] 撤销/铺参数后写盘失败: {e}")
            self._notify(self.tr("映射表没能写回文件：{err}").format(err=e))

    def _collect_settings(self):
        # 与 settings 同一口径: 全部参数(除路径类) + canvas_ratio_mode + mode
        s = {k: v for k, v in self._params.items()
             if k in _BACKEND_KEYS and k not in _NOT_PERSISTED}
        s["canvas_ratio_mode"] = self._params.get("canvas_ratio_mode", "original")
        s["mode"] = self._mode
        return s

    def _save_settings_now(self):
        try:
            # 与已有 settings 合并而不是整体替换: custom_colors 这类键不归参数面板管(由取色弹窗写入)
            s = dict(self.state.settings or {})
            s.update(self._collect_settings())
            self.state.settings = s
            core.save_settings(s)                       # 原子写, 见核心 _save_json
        except Exception as e:                          # noqa: BLE001 - 存不上不该影响使用
            self._notify(self.tr("自动保存失败：{err}").format(err=e))

    def _touch(self):
        # 参数变更的统一出口: 排一次保存 + 刷预览
        self._save_timer.start()
        self.request_preview()

    def _reset_settings(self):
        # 恢复默认设置(会覆盖当前全部调参, 因此先确认一次)
        # logo_dir 不在参数行中(无控件), 需单独从 default_params 取回
        if _message(self, QMessageBox.Icon.Question, self.tr("恢复默认设置"),
                    self.tr("将把当前所有参数恢复为出厂默认，现有调整会丢失。继续？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        d = core.default_params()

        def _do():
            self._apply_params(d)
            self._params["logo_dir"] = d.get("logo_dir") or self._params.get("logo_dir")
            self._reload_model_map()

        self._undo_step("__reset_settings__", "恢复默认设置", _do)
        self._notify(self.tr("已恢复默认设置"))

    def _reload_model_map(self):
        try:
            self._params["model_map"] = core.load_model_map(
                self._params.get("logo_dir") or core.DEFAULT_LOGO_DIR)
        except Exception:
            self._params["model_map"] = {}
        self._update_effective_model()
