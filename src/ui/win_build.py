"""界面构建: 顶栏、菜单、预览区、底栏、页签与卡片栅格。"""
from PySide6.QtCore import QPoint, QT_TRANSLATE_NOOP, Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSpacerItem, QSplitter, QStackedWidget, QVBoxLayout, QWidget)
from core.config import DATA_DIR, DEFAULT_LOGO_DIR, LOG_DIR
from . import i18n, theme
from .specs import PARAM_SPECS
from .toast import Toast
from .widgets import (Card, ElidedLabel, ParamRow, PreviewBox, PreviewStateChip, _LABEL_W,
                      _fx_divider, _iconify, _reset_button)
from .winconst import FONT_STYLE_COLS, FONT_STYLE_GROUPS, TABS, _SB_W


class BuildMixin:

    def _build_ui(self):
        # 预览控件先建: 悬停心跳与 set_highlight 都指向它(见 MainWindow.__init__ 里的 _hover_timer)
        self._preview = PreviewBox()
        self._only_if = {s["key"]: s["only_if"] for s in PARAM_SPECS if s.get("only_if")}
        self._only_if_not = {s["key"]: s["only_if_not"] for s in PARAM_SPECS if s.get("only_if_not")}
        self._spec_by_key = {s["key"]: s for s in PARAM_SPECS if s.get("key")}
        self._controllers = {c[0] for c in list(self._only_if.values()) + list(self._only_if_not.values())}
        # 不在参数表里的行的显隐条件: [(控件, 判据)] —— 随的是状态而不是某个参数(见 _vis_rule)
        self._vis_rules = []

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(theme.INSET_ROOT, 10, theme.INSET_ROOT, 8)
        outer.setSpacing(8)

        outer.addWidget(self._build_topbar())

        self._split = QSplitter(Qt.Horizontal)
        split = self._split
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(theme.GAP_CARD)
        lv.addWidget(self._segbar_row())
        self._stack = QStackedWidget()
        for tab_id, _title in TABS:
            self._stack.addWidget(self._build_tab(tab_id))
        lv.addWidget(self._stack, 1)
        min_host = max(t.widget().minimumSizeHint().width()
                       for t in (self._stack.widget(i) for i in range(self._stack.count())))
        left.setMinimumWidth(min_host + _SB_W + 2 * theme.INSET_COL)
        split.addWidget(left)
        split.addWidget(self._build_preview_pane())
        split.setChildrenCollapsible(False)   # 左列变窄后防止被拖塌
        split.splitterMoved.connect(self._on_split_moved)
        outer.addWidget(split, 1)

        self._bottombar = self._build_bottombar()   # 留引用: 吐司要浮在它之上(见 Toast._place)
        outer.addWidget(self._bottombar)
        # 吐司: 挂在中央部件上的浮层, 最后建并 raise_() 使其盖在所有控件之上
        self._toast = Toast(root)

    def _build_topbar(self):
        top = QFrame()
        t = QHBoxLayout(top)
        # 左缩进对齐左列内容(与页签卡片、参数卡片同一条竖线); 右边缘仍由根布局决定
        t.setContentsMargins(theme.INSET_BAR, 0, 0, 0)
        t.setSpacing(8)
        # ☰ 菜单: 长尾功能收纳于此, 不另起菜单栏(避免在全平铺界面上多出一条空栏)
        self._menu_btn = QPushButton()
        _iconify(self._menu_btn, theme.MENU)
        self._menu_btn.setObjectName("mini")
        self._menu_btn.setFixedWidth(32)
        self._menu_btn.setCursor(Qt.PointingHandCursor)
        self._menu_btn.setToolTip(self.tr("菜单"))
        self._menu_btn.setAccessibleName(self.tr("菜单"))
        self._menu_btn.setMenu(self._build_menu())
        t.addWidget(self._menu_btn)
        self._open_btn = QPushButton(self.tr("打开图片"))
        # 「入口」按钮: 顶栏这一排都是中性小按钮, 白底的它不显眼(见 theme 里 #entry 那段注释)
        self._open_btn.setObjectName("entry")
        self._open_btn.clicked.connect(self._pick_image)
        t.addWidget(self._open_btn)
        self._input_label = QLabel(self.tr("未选择图片"))
        self._input_label.setObjectName("muted")
        self._input_label.setMinimumWidth(160)
        t.addWidget(self._input_label, 1)
        self._undo_btn = QPushButton(self.tr("撤销"))
        self._redo_btn = QPushButton(self.tr("重做"))
        for b, slot, asset in ((self._undo_btn, self.undo, theme.UNDO),
                               (self._redo_btn, self.redo, theme.REDO)):
            b.setObjectName("mini")
            _iconify(b, asset)
            b.setEnabled(False)                  # 栈空时置灰, 见 _sync_undo_btns
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            t.addWidget(b)
        self._sync_undo_btns()
        # 图片信息: 没图可看时置灰(见 _sync_info_btn)
        self._info_btn = QPushButton(self.tr("EXIF信息"))
        self._info_btn.setObjectName("mini")
        _iconify(self._info_btn, theme.INFO)
        self._info_btn.setCursor(Qt.PointingHandCursor)
        self._info_btn.setToolTip(self.tr("查看照片 EXIF 与文件信息"))
        self._info_btn.setAccessibleName(self.tr("EXIF信息"))
        self._info_btn.clicked.connect(self._show_image_info)
        t.addWidget(self._info_btn)
        return top

    def _build_menu(self):
        # 顶栏 ☰ 的菜单: 长尾功能收纳于此, 不再向顶栏添加按钮
        m = QMenu(self)
        m.addAction(self.tr("关于"), self._show_about)
        m.addSeparator()
        m.addAction(self.tr("预设方案…"), self._open_preset_dialog)
        m.addAction(self.tr("字体文件…"), self._open_font_dialog)
        a = m.addAction(self.tr("恢复默认设置"), self._reset_settings)
        a.setToolTip(self.tr("将所有参数恢复为出厂默认值"))
        m.addSeparator()
        m.setToolTipsVisible(True)  # 菜单项也带 tooltip, 每个入口指向哪个目录一目了然
        a = m.addAction(self.tr("打开日志目录"), self._open_log_dir)
        a.setToolTip(LOG_DIR)       # 日志就在程序目录下(LOG_DIR == BASE_DIR)
        a = m.addAction(self.tr("打开数据目录"), self._open_data_dir)
        a.setToolTip(DATA_DIR)      # 数据目录是它下面的 watermark_data 子目录
        a = m.addAction(self.tr("打开 Logo 目录"), self._open_logo_dir)
        a.setToolTip(self._params.get("logo_dir") or DEFAULT_LOGO_DIR)
        m.addSeparator()
        lang = m.addMenu(self.tr("语言"))
        # 「语言」入口的可选码跟在文字后面, 两个码都写出来(不是只显示当前那个)
        # 码本身不翻译; 子菜单两项各用母语写自己(「中文」/「English」, 也不拿去 tr())
        self._lang_menu = lang
        self._lang_acts = []
        for code, label, badge in i18n.LANGS:
            act = lang.addAction(label)
            act.setCheckable(True)
            act.setChecked(code == self._lang)
            act.triggered.connect(lambda _c, co=code: self._set_lang(co))
            self._lang_acts.append((code, badge, act))
        self._sync_lang_menu()
        return m

    def _build_preview_pane(self):
        # 右侧预览面板: 标题条 + 预览框
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(theme.INSET_COL, 0, 0, 0)
        rl.setSpacing(theme.GAP_CARD)       # 与左列"块与块之间"同一节奏
        rl.addWidget(self._build_preview_header())
        rl.addWidget(self._preview, 1)
        return right

    def _build_preview_header(self):
        # 预览标题条: 圆角小块(与左列卡片同一套底色/边框/圆角), 含「效果预览」标题与右侧「刷新预览效果」
        self._preview_header = QFrame()
        self._preview_header.setObjectName("card")
        h = QHBoxLayout(self._preview_header)
        h.setContentsMargins(10, 6, 10, 6)
        h.setSpacing(theme.GAP_CTRL)
        title = QLabel(self.tr("效果预览"))
        title.setObjectName("cardTitle")
        h.addWidget(title)
        # 状态标识: 紧随标题右侧, 描述屏上图像与当前参数的关系
        self._state_chip = PreviewStateChip(self._preview_header)
        h.addWidget(self._state_chip)
        h.addStretch(1)
        self._refresh_btn = QPushButton(self.tr("刷新预览效果"))
        _iconify(self._refresh_btn, theme.RESET, 14)
        self._refresh_btn.setObjectName("ghost")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._refresh_preview_clicked)
        # 上次渲染耗时: 等宽小字弱色, 置于刷新按钮左侧
        self._perf = QLabel("")
        self._perf.setStyleSheet("color:%s; font-family:Consolas,monospace; background:transparent;"
                                 % theme.MUTED)
        h.addWidget(self._perf)
        h.addWidget(self._refresh_btn)
        return self._preview_header

    def _build_bottombar(self):
        bottom = QFrame()
        b = QHBoxLayout(bottom)
        b.setContentsMargins(theme.INSET_BAR, 0, 0, 0)   # 与顶栏、页签行同一条左对齐线
        b.setSpacing(8)
        # 「导出到 …」这一组与预览框左缘对齐: 宽度随分栏走, 由 _sync_bottom_lead 算, 引用名 _bottombar
        self._bottom_lead = QSpacerItem(0, 0, QSizePolicy.Policy.Ignored,
                                        QSizePolicy.Policy.Minimum)
        b.addSpacerItem(self._bottom_lead)
        b.addWidget(QLabel(self.tr("导出到")))
        self._out_label = ElidedLabel(self.tr("（跟随原图目录）"))
        self._out_label.setObjectName("muted")
        b.addWidget(self._out_label, 1)
        pick_out = QPushButton(self.tr("选择输出目录"))
        pick_out.clicked.connect(self._pick_output)
        b.addWidget(pick_out)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(200)
        self._progress.setVisible(False)
        b.addWidget(self._progress)
        self._gen_btn = QPushButton(self.tr("开始生成"))
        self._gen_btn.setObjectName("primary")
        self._gen_btn.setMinimumWidth(130)
        self._gen_btn.clicked.connect(self.start_generate)
        b.addWidget(self._gen_btn)
        return bottom

    def _sync_bottom_lead(self):
        # 底栏留白 = 预览框左缘 − 底栏内容左缘, 使「导出到」对齐预览框左缘
        lead = getattr(self, "_bottom_lead", None)
        bar = getattr(self, "_bottombar", None)
        if lead is None or bar is None or getattr(self, "_preview", None) is None:
            return
        host = self.centralWidget()
        if host is None:
            return
        want = (self._preview.mapTo(host, QPoint(0, 0)).x()
                - bar.mapTo(host, QPoint(0, 0)).x() - theme.INSET_BAR)
        lead.changeSize(max(0, want), 0, QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
        if bar.layout() is not None:
            bar.layout().invalidate()           # changeSize 只是改了数据, 得让布局重算一次

    def _build_tab(self, tab_id):
        # s.get("tab"): 有些行不在任何页签里(它们收进了弹窗, 见 PARAM_SPECS 开头那几条)
        specs = [s for s in PARAM_SPECS if s.get("tab") == tab_id]
        area = QScrollArea()
        area.setWidgetResizable(True)
        # 滚动条常驻: 内容不满一屏时也保留占位, 卡片宽度不会跳(_segbar_row 按同一宽度对齐)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self._wire_scrollbar(area.verticalScrollBar())
        self._wire_scrollbar(area.horizontalScrollBar())
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(theme.INSET_COL, 0, theme.INSET_COL, theme.GAP_ROW + 4)
        v.setSpacing(theme.GAP_CARD)

        if tab_id == "files":
            # 预设不在本页签: 已移入 ☰ 菜单的「预设方案…」弹窗(见 PresetDialog)
            for builder in (self._build_mode_card, self._build_model_card,
                            self._build_manual_card):
                v.addWidget(builder())

        card_order = []
        for s in specs:
            if s["card"] not in card_order:
                card_order.append(s["card"])
        for name in card_order:
            special = next((s.get("special") for s in specs
                            if s["card"] == name and s.get("special")), None)
            if special:
                v.addWidget(getattr(self, special)())
                continue
            card = Card(name)
            for s in specs:
                if s["card"] != name:
                    continue
                if s.get("key2"):
                    init = (self._params.get(s["key"]), self._params.get(s["key2"]))
                else:
                    init = self._params.get(s["key"])
                    if init is None and s.get("mirror"):
                        init = self._params.get(s["mirror"][0])
                row = ParamRow(s, init)
                row.changed.connect(self._on_param)
                if s["kind"] == "color":
                    row.colorClicked.connect(lambda r=row: self._open_color_dialog(r))
                    row.pickColorClicked.connect(lambda r=row: self._pick_color(r))
                if s.get("label_action") == "invert":
                    row.labelClicked.connect(lambda r=row: self._invert_color(r))
                self._rows[s["key"]] = row
                row.resetClicked.connect(lambda r=row: self._reset_param_row(r))
                card.body.addWidget(row)
            self._cards.append(card)
            v.addWidget(card)

        v.addStretch(1)
        area.setWidget(host)
        return area

    def _wire_scrollbar(self, sb):
        # 不可滚动时把手柄画成透明: 按滚动范围切换 emptyRange 动态属性, QSS 据此绘制
        def _on_range(lo, hi, bar=sb):
            empty = hi <= lo
            if bar.property("emptyRange") == empty:
                return
            bar.setProperty("emptyRange", empty)
            bar.style().unpolish(bar)      # 改动态属性后必须重新 polish, 否则 QSS 不重算
            bar.style().polish(bar)
        sb.rangeChanged.connect(_on_range)
        _on_range(sb.minimum(), sb.maximum())   # 初始态: 首次布局前范围是 0, 先按"空"起

    def _segbar_row(self):
        # 页签行: 右侧留出竖向滚动条的宽度, 让页签卡片与下方卡片左右都对齐
        row = QWidget()
        h = QHBoxLayout(row)
        # 与 _build_tab 的卡片宿主同一个横向留白, 两者左边缘对齐
        h.setContentsMargins(theme.INSET_COL, 0, theme.INSET_COL, 0)
        h.setSpacing(0)
        h.addWidget(self._build_segbar(), 1)
        h.addSpacing(_SB_W)
        return row

    def _unify_buttons(self):
        # 统一所有按钮的高度(宽度仍按各自需要设置)
        for b in self.findChildren(QPushButton):
            b.setFixedHeight(theme.CTRL_H)

    def _unify_spins(self):
        # 统一所有数值框的宽度并将数值居中(按全表可能出现的最宽取值计算)
        rows = [r for r in self._rows.values() if hasattr(r, "spin")]
        if not rows:
            return
        fm = rows[0].spin.fontMetrics()
        widest = 0
        for r in rows:
            # 取该行可能出现的最大显示值(整数档是整数, 小数档是带 1 位小数的百分数), 全表按最宽者计算
            txt = f"{r._disp_hi / 10:.1f}" if getattr(r, "_decimals", 0) else str(r._disp_hi)
            widest = max(widest, fm.horizontalAdvance(txt))
        width = widest + 26
        for r in rows:
            r.spin.setFixedWidth(width)
            r.spin.setAlignment(Qt.AlignCenter)
        # 「选择字体」按钮: 左边缘对齐数值框、右边缘对齐右侧的 + 按钮
        step_w = rows[0]._step_w
        grp = 2 * (step_w + 3) + width      # 3 = 步进器组内部的 spacing
        for r in self._rows.values():
            if getattr(r, "kind", "") == "path" and hasattr(r, "_path_btn"):
                r._path_btn.setFixedWidth(grp)
            # 色块行的「颜色管理」入口占的正是数值行步进器组那一段: 与它同宽, 色块右缘才与滑块列右缘对齐
            if getattr(r, "kind", "") == "color" and hasattr(r, "color_btn"):
                r.color_btn.setFixedWidth(grp)

    def _sync_range_rows(self):
        # 双键行(字号范围)的成对回填: 通用回填只认单键, 重置/撤销/加载预设后在这里补一次
        for s in PARAM_SPECS:
            row = self._rows.get(s.get("key"))
            if s.get("key2") and row is not None:
                row.set_value((self._params.get(s["key"]), self._params.get(s["key2"])))

    def _build_segbar(self):
        # 页签实现为分段控件: 外层一张卡片, 内含三个胶囊
        wrap = QFrame()
        wrap.setObjectName("segbar")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(4)
        self._tab_btns = []
        for i, (_tab_id, title) in enumerate(TABS):
            b = QPushButton(self.tr(title))
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=i: self.set_tab(k))
            h.addWidget(b)
            self._tab_btns.append(b)
        h.addStretch(1)
        return wrap

    # 供外部(截图工具/测试)使用的页签接口
    def tab_titles(self):
        return [t for _id, t in TABS]

    def set_tab(self, index):
        if not 0 <= index < len(self._tab_btns):
            return
        for i, b in enumerate(self._tab_btns):
            b.setChecked(i == index)
        self._stack.setCurrentIndex(index)

    def _build_font_style_card(self):
        # 水印开关: 行=文字种类, 列=显示/粗体/斜体
        card = Card(QT_TRANSLATE_NOOP("Card", "水印开关"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        # 横向列间距给 0: 右侧三列等分, 间距由三列自己吸收(见下面 setColumnStretch)
        hsp = 0
        grid.setHorizontalSpacing(hsp)
        grid.setVerticalSpacing(5)
        # 第 0 列宽度按"让第一列控件落在其它卡片的控件列上"反推(自适应时该列会比其它卡片靠左)
        grid.setColumnMinimumWidth(0, _LABEL_W + theme.GAP_CTRL - hsp)
        # 右侧三列等分: 三列均设 stretch=1, 列头与下方开关对齐
        for c in range(1, len(FONT_STYLE_COLS) + 1):
            grid.setColumnStretch(c, 1)
        for col, head in enumerate(FONT_STYLE_COLS, start=1):
            h = QLabel(head)
            h.setObjectName("hint")
            h.setAlignment(Qt.AlignCenter)
            grid.addWidget(h, 0, col)
        for r, (name, keys) in enumerate(FONT_STYLE_GROUPS, start=1):
            lab = QLabel(name)
            lab.setObjectName("fieldLabel")
            grid.addWidget(lab, r, 0)
            for c, key in enumerate(keys, start=1):
                # 格位给空标签(不是空格): 配合 QSS 的 #cell 间距为 0, 居中后才与列头精确对齐
                # no_reset: 格位不给 ↺, 本行共用一个(见下), 与网页版"一行一个重置"的粒度一致
                row = ParamRow(dict(key=key, label="", kind="bool", no_reset=True), self._params.get(key))
                row.chk.setObjectName("cell")
                row.chk.setAccessibleName(f"{name}·{FONT_STYLE_COLS[c - 1]}")
                row.chk.setToolTip(f"{name} · {FONT_STYLE_COLS[c - 1]}")
                row.changed.connect(self._on_param)
                self._rows[key] = row
                grid.addWidget(row, r, c, Qt.AlignmentFlag.AlignCenter)
            # 本行共用一个 ↺(格位不各给一个): 与网页版"一行一个重置"的粒度一致
            btn = _reset_button(lambda _c, lb=name, ks=tuple(keys): self._reset_params(lb, ks))
            grid.addWidget(btn, r, len(FONT_STYLE_COLS) + 1)
        card.body.addLayout(grid)
        self._cards.append(card)
        return card

    def _build_fx_card(self):
        # 「描边与投影」: 整块可折叠, 展开后功能组用分割线相隔
        title = QT_TRANSLATE_NOOP("Card", "描边与投影")
        card = Card(title, collapsible=True)
        self._vis_rule(card.body_host, lambda c=card: not c.collapsed)
        self._cards.append(card)

        specs = [s for s in PARAM_SPECS if s.get("card") == title and s.get("key")]
        groups = []
        for s in specs:
            if s["group"] not in groups:
                groups.append(s["group"])
        for i, name in enumerate(groups):
            if i:                                   # 组与组之间才画分割线(首组之前不画)
                card.body.addWidget(_fx_divider())
            card.body.addWidget(self._fx_group_head(name))
            for s in specs:
                if s["group"] != name:
                    continue
                row = ParamRow(s, self._params.get(s["key"]))
                row.changed.connect(self._on_param)
                if s["kind"] == "color":
                    row.colorClicked.connect(lambda r=row: self._open_color_dialog(r))
                    row.pickColorClicked.connect(lambda r=row: self._pick_color(r))
                if s.get("label_action") == "invert":
                    row.labelClicked.connect(lambda r=row: self._invert_color(r))
                self._rows[s["key"]] = row
                row.resetClicked.connect(lambda r=row: self._reset_param_row(r))
                card.body.addWidget(row)
        return card

    def _fx_group_head(self, name):
        # 功能组的小标题行: 只有组名, 重置在组内每一行的行尾 ↺ 上
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 0)      # 组与上一组之间多留一点空隙
        h.setSpacing(theme.GAP_CTRL)
        lab = QLabel(self.tr(name))
        lab.setObjectName("groupTitle")
        h.addWidget(lab)
        h.addStretch(1)
        return w

    def _row(self, label, widget, label_w=_LABEL_W):
        # 一行 [定宽标签 + 控件], 返回容器; 卡片里的非参数行(路径/按钮)都用它对齐
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(theme.GAP_CTRL)
        lab = QLabel(self.tr(label))
        lab.setObjectName("fieldLabel")
        lab.setFixedWidth(label_w)
        lab.setWordWrap(True)
        h.addWidget(lab)
        if isinstance(widget, (list, tuple)):
            for i, sub in enumerate(widget):
                h.addWidget(sub, 1 if i == 0 else 0)
        else:
            h.addWidget(widget, 1)
        return w

    def _path_label(self, text=""):
        lab = ElidedLabel(text)
        lab.setObjectName("muted")
        return lab

    def _build_mode_card(self):
        card = Card(QT_TRANSLATE_NOOP("Card", "处理模式"))
        # 该行必须走 _row 的栅格(定宽标签 + 控制列), 不可自占一整行:
        # 胶囊文字需要横向内边距, 贴不到卡片内容左线
        seg = QWidget()
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(6)
        self._mode_btns = {}
        for key, text in (("single", QT_TRANSLATE_NOOP("MainWindow", "单张图片")),
                          ("batch", QT_TRANSLATE_NOOP("MainWindow", "批量目录"))):
            b = QPushButton(self.tr(text))
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, k=key: self._set_mode(k))
            self._mode_btns[key] = b
            sh.addWidget(b, 1)          # 各占一半: **不写死宽度** —— 窗口拉宽时两格跟着分
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "模式"), seg))

        # 此处不放「选择图片」按钮: 与顶栏「打开图片」重复, 仅保留顶栏入口
        self._input_val = self._path_label(self.tr("未选择图片"))
        self._row_single = self._row(QT_TRANSLATE_NOOP("MainWindow", "输入图片"), self._input_val)
        card.body.addWidget(self._row_single)

        self._batch_val = self._path_label(self.tr("未选择目录"))
        self._row_batch = self._row(QT_TRANSLATE_NOOP("MainWindow", "图片目录"), [
            self._batch_val, self._btn(self.tr("选择文件夹"), self._pick_batch_dir)])
        card.body.addWidget(self._row_batch)

        self._recursive_chk = QCheckBox(self.tr("包含子文件夹"))
        self._recursive_chk.setCursor(Qt.PointingHandCursor)
        self._recursive_chk.setChecked(self._batch_recursive)
        self._recursive_chk.toggled.connect(self._on_recursive)
        self._row_batch_rec = self._row("", self._recursive_chk)
        card.body.addWidget(self._row_batch_rec)

        self._batch_count = QLabel("")
        self._batch_count.setObjectName("hint")
        card.body.addWidget(self._batch_count)

        self._vis_rule(self._row_single, lambda: self._mode == "single")
        for w in (self._row_batch, self._row_batch_rec, self._batch_count):
            self._vis_rule(w, lambda: self._mode != "single")
        return card

    def _btn(self, text, slot, mini=False):
        b = QPushButton(text)
        if mini:
            b.setObjectName("mini")
        b.clicked.connect(slot)
        return b

    def _build_model_card(self):
        card = Card(QT_TRANSLATE_NOOP("Card", "型号美化"))
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText(self.tr("临时美化型号（仅本次有效）"))
        self._model_edit.setToolTip(self.tr("留空则使用映射表；填写后强制使用该名称"))
        self._model_edit.textChanged.connect(self._on_custom_model)
        self._map_btn = self._btn(self.tr("管理所有型号映射"), self._open_map_dialog)
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "美化型号"), [self._model_edit, self._map_btn]))
        # 「识别型号」显示照片中识别到的型号(与「美化型号/映射表」无关), 水印最终名称由该行 tooltip 给出
        self._model_lab = ElidedLabel("—")
        # 该行不用 muted 配色: 识别到的型号是判断水印内容的依据
        self._model_lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._copy_btn = self._btn(self.tr("复制原始型号"), self._copy_raw_model)
        card.body.addWidget(self._row(QT_TRANSLATE_NOOP("MainWindow", "识别型号"),
                                      [self._model_lab, self._copy_btn]))
        return card

    def _build_manual_card(self):
        card = Card(QT_TRANSLATE_NOOP("Card", "自定义水印信息"))
        self._manual_chk = QCheckBox(self.tr("启用（启用后强制使用下列信息，忽略照片 EXIF）"))
        self._manual_chk.setCursor(Qt.PointingHandCursor)
        self._manual_chk.toggled.connect(self._on_manual_toggle)
        card.body.addWidget(self._manual_chk)

        self._manual_edits = {}
        # 一项一行(竖排), 标签列与其它卡片对齐, 便于阅读
        specs = (("make", QT_TRANSLATE_NOOP("MainWindow", "品牌"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 Canon"),
                  QT_TRANSLATE_NOOP("MainWindow", "原样输出")),
                 ("model", QT_TRANSLATE_NOOP("MainWindow", "型号"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 EOS R5"),
                  QT_TRANSLATE_NOOP("MainWindow", "原样输出")),
                 ("exposure", QT_TRANSLATE_NOOP("MainWindow", "快门"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 1/125"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补充 s 后缀")),
                 ("fnumber", QT_TRANSLATE_NOOP("MainWindow", "光圈"),
                  QT_TRANSLATE_NOOP("MainWindow", "如 2.8"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补充 f/ 前缀")),
                 ("iso", "ISO", QT_TRANSLATE_NOOP("MainWindow", "如 100"),
                  QT_TRANSLATE_NOOP("MainWindow", "自动补充 ISO 前缀")))
        for key, label, ph, tip in specs:
            e = QLineEdit()
            e.setPlaceholderText(self.tr(ph))
            e.setToolTip(self.tr(tip))
            e.setEnabled(False)
            e.textChanged.connect(lambda t, k=key: self._on_manual_field(k, t))
            self._manual_edits[key] = e
            card.body.addWidget(self._row(label, e))
        return card
