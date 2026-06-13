import sys
import threading
import logging
import time

from src.config import setup_logging, config
from src.process_finder import discover_active_processes
from src.quota_fetcher import fetch_quota
from src.tray_manager import TrayManager

class QuotaMonitorApp:
    def __init__(self):
        # Set up logging first
        setup_logging()
        logging.info("Starting Antigravity Quota Monitor application.")
        
        # Shutdown event and refresh event
        self.stop_event = threading.Event()
        self.refresh_event = threading.Event()
        
        # Initialize UI manager
        self.tray = TrayManager(
            on_refresh_callback=self.trigger_immediate_refresh,
            on_exit_callback=self.shutdown
        )
        
        # Background worker thread
        self.worker_thread = None

    def trigger_immediate_refresh(self):
        """Handle request to refresh immediately."""
        logging.info("Immediate refresh triggered.")
        self.refresh_event.set()

    def select_active_process(self, processes):
        """Select the preferred process from the list of verified processes."""
        if not processes:
            return None
            
        # If user preferred a PID, try to find it
        if config.selected_pid:
            for proc in processes:
                if proc.pid == config.selected_pid:
                    return proc
            # Preferred PID not found or dead, reset preference
            logging.info(f"Preferred PID {config.selected_pid} not found or dead. Falling back.")
            config.set_selected_pid(None)
            
        # Default: pick first one
        selected = processes[0]
        # If there are multiple, auto-pin to the first one's PID to lock selection until changed
        if len(processes) > 1:
            config.set_selected_pid(selected.pid)
            
        return selected

    def monitor_loop(self):
        """Background loop to fetch and update status."""
        logging.info("Starting background monitor loop.")
        
        # Re-try backoff timing in case of connection failure
        retry_delay = 30
        
        while not self.stop_event.is_set():
            # Reset refresh event flag
            self.refresh_event.clear()
            
            active_proc = None
            processes = []
            snapshot = None
            
            try:
                # 1. Discover processes
                processes = discover_active_processes()
                active_proc = self.select_active_process(processes)
                
                if active_proc:
                    logging.info(f"Target process selected: {active_proc}")
                    # 2. Fetch quota data
                    snapshot = fetch_quota(active_proc)
                    logging.info(f"Successfully fetched quota data for {len(snapshot.models)} models.")
                else:
                    logging.warning("No active Antigravity Language Server processes detected.")
                    
            except Exception as e:
                logging.error(f"Error in monitor loop: {e}", exc_info=True)
                # Keep snapshot None to trigger error state
                snapshot = None
                
            # 3. Update tray UI (always update, even if error occurred, to reflect state)
            try:
                self.tray.update_icon(snapshot, processes)
            except Exception as e:
                logging.critical(f"Failed to update tray icon: {e}", exc_info=True)
                
            # Determine sleep time (normal interval or retry backoff)
            if not active_proc or not snapshot:
                # Use shorter delay when in error/missing state to reconnect sooner
                sleep_time = retry_delay
                logging.info(f"Entering retry state. Will attempt next query in {sleep_time}s.")
            else:
                sleep_time = config.polling_interval
                logging.info(f"Next scheduled query in {sleep_time}s.")
                
            # Sleep until interval expires OR refresh is requested OR shutdown is triggered
            # We wait in a loop checking stop_event so we can exit immediately
            waited = 0
            check_interval = 1.0  # check shutdown state every second
            while waited < sleep_time:
                if self.stop_event.is_set():
                    break
                if self.refresh_event.is_set():
                    logging.info("Waking up monitor loop for immediate refresh.")
                    break
                time.sleep(check_interval)
                waited += check_interval

        logging.info("Background monitor loop terminated.")

    def start(self):
        """Start the monitor worker thread and the tray UI loop."""
        # 1. Start background thread
        self.worker_thread = threading.Thread(target=self.monitor_loop, name="MonitorThread", daemon=True)
        self.worker_thread.start()
        
        # 2. Run system tray (blocks this main thread)
        try:
            self.tray.run()
        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt received.")
            self.shutdown()

    def shutdown(self):
        """Gracefully stop background tasks and the UI."""
        logging.info("Initiating shutdown...")
        self.stop_event.set()
        self.refresh_event.set()  # break wait loop if sleeping
        
        # Stop tray loop
        if self.tray:
            self.tray.stop()
            
        logging.info("Shutdown completed. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    app = QuotaMonitorApp()
    app.start()
