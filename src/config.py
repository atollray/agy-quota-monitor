import os
import logging
from logging.handlers import RotatingFileHandler

# Application metadata
APP_NAME = "quotachecker"
APP_DIR = os.path.join(os.path.expanduser("~"), f".{APP_NAME}")
LOG_FILE = os.path.join(APP_DIR, f"{APP_NAME}.log")

# Polling configuration
DEFAULT_POLLING_INTERVAL = 900  # 15 minutes in seconds
MIN_POLLING_INTERVAL = 60       # 1 minute in seconds

class Config:
    def __init__(self):
        self.polling_interval = DEFAULT_POLLING_INTERVAL
        self.selected_model = "AUTO"  # "AUTO" or specific model label
        self.selected_pid = None       # Chosen process ID (for multi-instance)
        
        # Ensure application directory exists
        os.makedirs(APP_DIR, exist_ok=True)
        
    def set_polling_interval(self, val_seconds):
        self.polling_interval = max(MIN_POLLING_INTERVAL, val_seconds)
        
    def set_selected_model(self, model_label):
        self.selected_model = model_label
        
    def set_selected_pid(self, pid):
        self.selected_pid = pid

# Global config instance
config = Config()

def setup_logging():
    """Configure rotating log file."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Clear existing handlers
    if logger.handlers:
        logger.handlers.clear()
        
    # Rotate at 1MB, keep 3 backup files
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Also log to console for debugging
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logging.info("Logging initialized.")
