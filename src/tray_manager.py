import os
import logging
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item, Menu
from src.config import config

class TrayManager:
    def __init__(self, on_refresh_callback, on_exit_callback):
        self.on_refresh_callback = on_refresh_callback
        self.on_exit_callback = on_exit_callback
        self.icon = None
        self.current_snapshot = None
        self.active_processes = []
        
        # Load font
        self.font = self.load_system_font()
        
        # Create initial icon
        initial_image = self.create_numeric_image("..", (128, 128, 128))
        self.icon = pystray.Icon(
            "agy_quota_monitor",
            initial_image,
            title="Antigravity Quota Monitor",
            menu=self.build_default_menu()
        )

    def load_system_font(self):
        """Try to load a clean system font, fallback to default if not found."""
        font_choices = [
            ("Segoe UI Bold", "segoeuib.ttf", 20),
            ("Arial Bold", "arialbd.ttf", 20),
            ("Consolas Bold", "consolab.ttf", 20),
            ("Segoe UI", "Segoe UI.ttf", 20),
            ("Arial", "arial.ttf", 20),
        ]
        for name, filename, size in font_choices:
            try:
                # Pillow can search Windows Fonts directory by default
                return ImageFont.truetype(filename, size)
            except IOError:
                try:
                    # Explicit path search
                    path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", filename)
                    if os.path.exists(path):
                        return ImageFont.truetype(path, size)
                except Exception:
                    pass
        logging.warning("No system bold fonts found. Falling back to default font.")
        return ImageFont.load_default()

    def create_numeric_image(self, text, text_color):
        """Create a 32x32 image with the specified text and color."""
        size = 32
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0)) # transparent background
        draw = ImageDraw.Draw(img)
        
        # Determine text dimensions using bounding box (Pillow 10+ compatible)
        try:
            bbox = draw.textbbox((0, 0), text, font=self.font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            # Fallback for old Pillow versions
            text_w, text_h = draw.textsize(text, font=self.font) if hasattr(draw, "textsize") else (18, 18)
            
        # Draw centered text
        x = (size - text_w) // 2
        y = (size - text_h) // 2 - 2  # slight offset for visual alignment
        
        # Draw shadow/outline for readability on dark/light taskbars
        outline_color = (0, 0, 0, 180) if text_color != (0, 0, 0) else (255, 255, 255, 180)
        draw.text((x-1, y), text, fill=outline_color, font=self.font)
        draw.text((x+1, y), text, fill=outline_color, font=self.font)
        draw.text((x, y-1), text, fill=outline_color, font=self.font)
        draw.text((x, y+1), text, fill=outline_color, font=self.font)
        
        # Draw main text
        draw.text((x, y), text, fill=text_color, font=self.font)
        return img

    def update_icon(self, snapshot, active_processes):
        """
        Update the tray icon and its context menu based on the latest snapshot.
        """
        self.current_snapshot = snapshot
        self.active_processes = active_processes
        
        # Determine text and color to show on the icon
        text, color, tooltip = self.determine_icon_state()
        
        # Create and set image
        self.icon.icon = self.create_numeric_image(text, color)
        self.icon.title = tooltip
        
        # Rebuild dynamic menu
        self.icon.menu = self.build_dynamic_menu()

    def determine_icon_state(self):
        """Determine what number, color, and tooltip to show on the tray."""
        if not self.active_processes:
            return "!!", (220, 53, 69), "Antigravity: Not Running\n(Waiting for process...)"
            
        if not self.current_snapshot:
            return "..", (128, 128, 128), "Antigravity: Connecting..."
            
        models = self.current_snapshot.models
        if not models:
            return "?", (255, 193, 7), "Antigravity: No Quota Data\n(Check if logged in)"
            
        # Find the model to display based on user config
        target_model = None
        if config.selected_model == "AUTO":
            # Auto-select the model with the lowest percentage
            valid_models = [m for m in models if m.percentage is not None]
            if valid_models:
                target_model = min(valid_models, key=lambda m: m.percentage)
        else:
            # Match by label
            for m in models:
                if m.label == config.selected_model:
                    target_model = m
                    break
            # Fallback if preferred model is not in snapshot
            if not target_model:
                valid_models = [m for m in models if m.percentage is not None]
                if valid_models:
                    target_model = min(valid_models, key=lambda m: m.percentage)

        if not target_model or target_model.percentage is None:
            return "?", (255, 193, 7), "Antigravity: No active model quota found."
            
        pct = target_model.percentage
        text = f"{pct}"
        
        # Colors: Green (>50%), Yellow (20-50%), Red (<20%), Dark Red (0%)
        if pct > 50:
            color = (40, 167, 69)     # Green
        elif pct >= 20:
            color = (255, 193, 7)    # Yellow
        elif pct > 0:
            color = (220, 53, 69)    # Red
        else:
            color = (139, 0, 0)      # Dark Red / Exhausted
            text = "0!"
            
        # Build tooltip (limit to 127 chars for Windows API)
        tooltip_lines = ["【Antigravity Quota】"]
        for m in models[:3]:  # Top 3 models to fit inside tooltip character limit
            if m.percentage is not None:
                tooltip_lines.append(f"{m.label}: {m.percentage}% ({m.time_until_reset_formatted})")
            else:
                tooltip_lines.append(f"{m.label}: No Limit")
                
        if self.current_snapshot.credits:
            cre = self.current_snapshot.credits
            tooltip_lines.append(f"Credits: {cre.available}/{cre.monthly} ({cre.percentage}%)")
            
        tooltip = "\n".join(tooltip_lines)
        if len(tooltip) > 127:
            # Truncate to avoid Windows crash/exception
            tooltip = tooltip[:124] + "..."
            
        return text, color, tooltip

    def build_default_menu(self):
        """Create the default static menu shown before any process is found."""
        return Menu(
            item("今すぐ更新 (Refresh Now)", self.on_refresh_callback),
            item("アプリの終了 (Exit)", self.on_exit_callback)
        )

    def build_dynamic_menu(self):
        """Build a dynamic menu featuring models and instance selectors."""
        menu_items = []
        
        # 1. Show Details item (shows list in submenu or simple action)
        if self.current_snapshot and self.current_snapshot.models:
            detail_items = []
            for m in self.current_snapshot.models:
                pct_str = f"{m.percentage}%" if m.percentage is not None else "No Limit"
                detail_items.append(item(f"{m.label}: {pct_str} (Reset: {m.time_until_reset_formatted})", lambda: None, enabled=True))
            if self.current_snapshot.credits:
                cre = self.current_snapshot.credits
                detail_items.append(item(f"Monthly Credits: {cre.available}/{cre.monthly} ({cre.percentage}%)", lambda: None, enabled=True))
            menu_items.append(item("全モデル詳細表示 (Show Details)", Menu(*detail_items)))
        
        # 2. Refresh Now
        menu_items.append(item("今すぐ更新 (Refresh Now)", self.on_refresh_callback))
        
        # 3. Model Selector submenu
        if self.current_snapshot and self.current_snapshot.models:
            model_items = []
            # Option to Auto-select
            model_items.append(item(
                "最低残量モデルを自動選択",
                lambda: self.set_model_preference("AUTO"),
                checked=lambda _: config.selected_model == "AUTO"
            ))
            for m in self.current_snapshot.models:
                # Create a closure for selecting
                def make_setter(label):
                    return lambda: self.set_model_preference(label)
                
                model_items.append(item(
                    m.label,
                    make_setter(m.label),
                    checked=lambda _, lbl=m.label: config.selected_model == lbl
                ))
            menu_items.append(item("表示対象の切り替え (Display Model)", Menu(*model_items)))
            
        # 4. Instance selector submenu (only if multiple processes are running)
        if len(self.active_processes) > 1:
            instance_items = []
            for proc in self.active_processes:
                def make_setter(pid):
                    return lambda: self.set_instance_preference(pid)
                    
                label = f"{proc.name} (PID {proc.pid}) - Port {proc.active_port}"
                instance_items.append(item(
                    label,
                    make_setter(proc.pid),
                    checked=lambda _, p_id=proc.pid: config.selected_pid == p_id
                ))
            menu_items.append(item("接続先インスタンスの切り替え (Select Instance)", Menu(*instance_items)))
            
        # 5. Polling Interval submenu
        intervals = [
            ("1分", 60),
            ("3分", 180),
            ("5分", 300),
            ("10分", 600),
            ("15分", 900),
            ("30分", 1800),
        ]
        interval_items = []
        for label, seconds in intervals:
            def make_setter(sec):
                return lambda: self.set_interval_preference(sec)
                
            interval_items.append(item(
                label,
                make_setter(seconds),
                checked=lambda _, sec=seconds: config.polling_interval == sec
            ))
        menu_items.append(item("更新間隔の変更 (Polling Interval)", Menu(*interval_items)))
        
        # 6. Exit
        menu_items.append(item("アプリの終了 (Exit)", self.on_exit_callback))
        
        return Menu(*menu_items)

    def set_model_preference(self, label):
        logging.info(f"Model preference set to: {label}")
        config.set_selected_model(label)
        self.on_refresh_callback()

    def set_instance_preference(self, pid):
        logging.info(f"Instance preference set to PID: {pid}")
        config.set_selected_pid(pid)
        self.on_refresh_callback()

    def set_interval_preference(self, seconds):
        logging.info(f"Polling interval preference set to: {seconds}s")
        config.set_polling_interval(seconds)
        self.on_refresh_callback()

    def run(self):
        """Start the system tray icon main loop (blocks the thread)."""
        logging.info("Starting pystray system tray loop.")
        self.icon.run()

    def stop(self):
        """Stop the system tray icon loop."""
        logging.info("Stopping pystray system tray loop.")
        self.icon.stop()
