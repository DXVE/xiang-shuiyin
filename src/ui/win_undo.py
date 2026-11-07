"""撤销 / 重做、显隐联动与镜像行、单参数恢复默认。"""
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from core.params import default_params
from .winconst import _UNDO_LIMIT


# 两个参数值是否等价(色号忽略大小写, 数值留一点容差): 供"已是默认值就别重渲"的判断
def _same_as_default(a, b, tol=1e-9):
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


class UndoVisibilityMixin:

    def _on_param(self, key, value):
        spec = self._spec_by_key.get(key) or {}
        self._begin_undo(key, spec.get("label") or key)     # 撤销: 按控件合并, 见 _begin_undo
        if spec.get("mirror"):
            # 镜像行: 一个滑块写两个底层参数(上下各一份), 保持底层行同步, 切到别的布局不残留
            for t in spec["mirror"]:
                self._params[t] = value
                if t in self._rows:
                    self._rows[t].set_value(value)
        else:
            self._params[key] = value
        if key in self._controllers:
            self._sync_visibility()
        self._touch()

    def _snapshot(self):
        # 取一份撤销用的快照(参数表 + model_map 副本)
        s = dict(self._params)
        s["model_map"] = dict(self._params.get("model_map") or {})
        return s

    def _begin_undo(self, key, label):
        # 开始/延续一次撤销事务(同一个 key 的连续改动算一步)
        txn = self._undo_txn
        if txn is not None and txn["key"] == key:
            txn["label"] = label
            self._undo_timer.start()            # 还在拖: 把"收尾"往后推(松开后 600ms 才落定)
            return
        self._commit_undo()                     # 换了控件: 先把上一笔落定
        self._undo_txn = {"key": key, "label": label, "before": self._snapshot()}
        self._undo_timer.start()
        self._sync_undo_btns()

    def _commit_undo(self):
        # 把进行中的事务落定入栈(超时 / 换控件 / 窗口级动作之后)
        self._undo_timer.stop()
        txn, self._undo_txn = self._undo_txn, None
        if txn is None:
            return
        after = self._snapshot()
        if after == txn["before"]:              # 值绕了一圈又回来了(滑块拖回原位): 不入栈
            self._sync_undo_btns()
            return
        self._undo_stack.append({"label": txn["label"], "before": txn["before"], "after": after})
        del self._undo_stack[:-_UNDO_LIMIT]     # 只留最近 N 步
        self._redo_stack.clear()                # 有了新改动, 重做链失效
        self._sync_undo_btns()

    def _undo_step(self, key, label, fn):
        # 把一个"整批动作"记成一步(单参数恢复默认 / 全局恢复默认 / 加载预设 / 映射表保存)
        self._begin_undo(key, label)
        try:
            fn()
        finally:
            self._commit_undo()

    def _restore(self, snap):
        # 把一份快照铺回去(_apply_params 会一并处理镜像行、显隐、映射表与保存预览)
        self._apply_params(snap)

    def undo(self):
        self._commit_undo()                     # 在途的那笔先落定, 再撤它
        if not self._undo_stack:
            return
        e = self._undo_stack.pop()
        self._restore(e["before"])
        self._redo_stack.append(e)
        self._notify(self.tr("已撤销：{label}").format(label=e["label"]))
        self._sync_undo_btns()

    def redo(self):
        self._commit_undo()
        if not self._redo_stack:
            return
        e = self._redo_stack.pop()
        self._restore(e["after"])
        self._undo_stack.append(e)
        self._notify(self.tr("已重做：{label}").format(label=e["label"]))
        self._sync_undo_btns()

    def _sync_undo_btns(self):
        # 按钮可用态与 tooltip 跟着栈走(栈空置灰)
        if not hasattr(self, "_undo_btn"):
            return
        nu = self._undo_stack[-1]["label"] if self._undo_stack else ""
        nr = self._redo_stack[-1]["label"] if self._redo_stack else ""
        can_undo = bool(self._undo_stack) or self._undo_txn is not None
        self._undo_btn.setEnabled(can_undo)
        self._redo_btn.setEnabled(bool(self._redo_stack))
        self._undo_btn.setToolTip(self.tr("撤销：{label}（Ctrl+Z）").format(label=nu) if nu
                                    else self.tr("撤销（Ctrl+Z）"))
        self._redo_btn.setToolTip(self.tr("重做：{label}（Ctrl+Shift+Z）").format(label=nr)
                                    if nr else self.tr("重做（Ctrl+Shift+Z）"))

    def _vis_rule(self, widget, predicate):
        # 给"不在参数表里的行"登记显隐条件(目前只有「单张 / 批量」那几行)
        self._vis_rules.append((widget, predicate))

    def _sync_visibility(self):
        # 唯一改显隐的地方: 参数行按 only_if / only_if_not, 手写行按 _vis_rules
        area = self._stack.currentWidget()
        host = area.widget() if area is not None else None
        if host is not None:
            host.setUpdatesEnabled(False)
        try:
            for key, row in self._rows.items():
                cond = self._only_if.get(key)
                ncond = self._only_if_not.get(key)
                show = (cond is None or self._params.get(cond[0]) in cond[1]) and                        (ncond is None or self._params.get(ncond[0]) not in ncond[1])
                row.setVisible(show)
            for widget, predicate in self._vis_rules:
                widget.setVisible(bool(predicate()))
        finally:
            if host is not None:
                lay = host.layout()
                if lay is not None:
                    lay.activate()          # 先算完终态布局, 再放开重绘, 避免中间态被画出来
                QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
                host.resize(host.width(), host.sizeHint().height())
                host.setUpdatesEnabled(True)

    def _sync_mirror_rows(self, changed):
        # 把镜像行的显示值刷新为底层参数的值
        # 镜像行(内侧/外侧间距)自身不存值, 写的是 gap_image_top/bottom 等底层参数
        for key, row in self._rows.items():
            m = (self._spec_by_key.get(key) or {}).get("mirror")
            if m and m[0] in changed:
                row.set_value(self._params.get(m[0]))

    def _reset_param_row(self, row):
        # 参数行行尾的 ↺: 镜像行回它代理的那一对底层键, 双键行(字号范围)两个键一起回
        spec = self._spec_by_key.get(row.key) or {}
        keys = tuple(spec.get("mirror") or ())
        if not keys:
            keys = (row.key, spec["key2"]) if spec.get("key2") else (row.key,)
        self._reset_params(row.spec_label() or row.key, keys)

    def _reset_params(self, label, keys):
        # 把一组参数恢复为出厂默认值(参数行的 ↺ 给单个键, 水印开关一行的 ↺ 给该行三个键), 整批算撤销一步
        # 已经是默认值就直接返回: 否则一次空点也要白渲一帧
        defaults = default_params()
        targets = [k for k in keys if k in defaults]
        if not targets or all(_same_as_default(self._params.get(k), defaults[k]) for k in targets):
            return

        def _do():
            changed = set()
            for key in targets:
                self._params[key] = defaults[key]
                if key in self._rows:
                    self._rows[key].set_value(defaults[key])
                changed.add(key)
            self._sync_mirror_rows(changed)
            self._sync_range_rows()
            self._sync_visibility()
            self._touch()

        self._undo_step(f"__reset__{label}", label, _do)
