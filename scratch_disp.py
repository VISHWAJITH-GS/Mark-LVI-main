class MinimizedDisplaysOverlay(QWidget):
    dot_clicked = pyqtSignal(int)

    def __init__(self, parent_main_win=None):
        super().__init__(None)
        self._main_win = parent_main_win
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._vbox = QVBoxLayout(self)
        self._vbox.setContentsMargins(4, 4, 4, 4)
        self._vbox.setSpacing(6)
        self._vbox.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)

        self._buttons: dict[int, QPushButton] = {}

    def update_displays(self, display_items: list[tuple[int, str]]):
        while self._vbox.count():
            item = self._vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._buttons.clear()

        if not display_items:
            self.hide()
            return

        # Stack from top to bottom: highest ID at the top, lowest ID at the bottom (closest to JARVIS)
        # Display 1 sits immediately above the JARVIS window, Display 2 above Display 1, etc.
        for disp_id, disp_title in sorted(display_items, key=lambda x: x[0], reverse=True):
            btn = QPushButton(str(disp_id), self)
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"Display {disp_id}: {disp_title}\n(Click to restore)")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                        stop:0 #ffffff, stop:0.35 {C.PRI}, stop:0.75 #b30059, stop:1.0 rgba(14, 4, 12, 235));
                    color: #ffffff;
                    border: 1.5px solid {C.PRI};
                    border-radius: 13px;
                    font-family: 'Courier New';
                    font-size: 10px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                        stop:0 #ffffff, stop:0.4 #ffd700, stop:0.8 #ff8800, stop:1.0 rgba(20, 10, 0, 245));
                    border: 2px solid #ffd700;
                    color: #ffffff;
                }}
            """)
            btn.clicked.connect(lambda _, did=disp_id: self.dot_clicked.emit(did))
            self._vbox.addWidget(btn)
            self._buttons[disp_id] = btn

        dot_count = len(display_items)
        req_h = dot_count * 26 + (dot_count - 1) * 6 + 8
        self.resize(36, req_h)
        self.reposition()
        self.show()
        self.raise_()

    def reposition(self):
        if not self._main_win or not self.isVisible():
            return
        main_geom = self._main_win.geometry()
        center_x = main_geom.x() + (main_geom.width() - self.width()) // 2
        top_y = main_geom.y() - self.height() - 6
        self.move(center_x, top_y)


class JarvisDisplayWindow(QWidget):
    closed = pyqtSignal(int)
    minimized_requested = pyqtSignal(int)
    _img_data_sig = pyqtSignal(bytes)