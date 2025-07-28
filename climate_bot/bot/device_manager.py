import requests
import threading
import time
import logging
from collections import defaultdict
from typing import Dict, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class DeviceManager:  
    def __init__(self, api_url: str, refresh_interval: int = 86400, max_retries: int = 3):
        self.api_url = api_url
        self.refresh_interval = refresh_interval
        self.max_retries = max_retries
        
        self._lock = threading.RLock()
        self._locations = defaultdict(list)
        self._device_ids = {}
        self._device_issues = {}
        self._devices_with_issues = set()
        
        self._update_thread = None
        self._stop_event = threading.Event()
        self._last_update = 0
        self._update_count = 0
        self._consecutive_failures = 0
        
        self._fetch_device_data()
        
    def start_auto_update(self) -> None:
        if self._update_thread and self._update_thread.is_alive():
            logger.warning("Auto-update is running")
            return
            
        self._stop_event.clear()
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()
        logger.info(f"Started auto-update with {self.refresh_interval}s interval")
        
    def stop_auto_update(self) -> None:
        if self._update_thread and self._update_thread.is_alive():
            self._stop_event.set()
            self._update_thread.join(timeout=5)
            logger.info("Stopped auto-update")
            
    def force_update(self) -> bool:
        return self._fetch_device_data()
        
    def get_locations(self) -> Dict[str, list]:
        with self._lock:
            return dict(self._locations)
            
    def get_device_ids(self) -> Dict[str, str]:
        with self._lock:
            return self._device_ids.copy()
            
    def get_device_issues(self) -> Dict[str, list]:
        with self._lock:
            return self._device_issues.copy()
            
    def get_devices_with_issues(self) -> Set[str]:
        with self._lock:
            return self._devices_with_issues.copy()
            
    def get_device_id(self, device_name: str) -> Optional[str]:
        with self._lock:
            return self._device_ids.get(device_name)
            
    def has_device_issues(self, device_name: str) -> bool:
        with self._lock:
            return device_name in self._devices_with_issues

    def _update_loop(self) -> None:
        #main update loop
        while not self._stop_event.is_set():
            try:
                if self._stop_event.wait(self.refresh_interval):
                    break  # Stop event was set
                    
                self._fetch_device_data()
                
            except Exception as e:
                logger.error(f"Unexpected error in update loop: {e}")
                
    def _fetch_device_data(self) -> bool:
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Fetching device data from {self.api_url} (attempt {attempt + 1})")
                
                response = requests.get(self.api_url, timeout=30)
                response.raise_for_status()
                devices = response.json()
                
                if not isinstance(devices, list):
                    logger.error("API response is not a list")
                    continue
                    
                new_locations = defaultdict(list)
                new_device_ids = {}
                new_device_issues = {}
                new_devices_with_issues = set()
                
                for device in devices:
                    if not isinstance(device, dict):
                        logger.warning(f"Invalid device data: {device}")
                        continue
                        
                    device_name = device.get("name")
                    if not device_name:
                        logger.warning("Device missing name field")
                        continue
                        
                    device_id = device.get("generated_id")
                    if device_id:
                        new_device_ids[device_name] = device_id
                    
                    parent_name = device.get("parent_name", "Unknown")
                    new_locations[parent_name].append(device_name)
                    
                    issues = device.get("issues", [])
                    if issues:
                        new_device_issues[device_name] = issues
                        new_devices_with_issues.add(device_name)
                
                with self._lock:
                    old_device_count = len(self._device_ids)
                    old_issues_count = len(self._devices_with_issues)
                    
                    self._locations = new_locations
                    self._device_ids = new_device_ids
                    self._device_issues = new_device_issues
                    self._devices_with_issues = new_devices_with_issues
                    self._last_update = time.time()
                    self._update_count += 1
                    self._consecutive_failures = 0
                    
                    new_device_count = len(self._device_ids)
                    new_issues_count = len(self._devices_with_issues)
                
                if old_device_count != new_device_count:
                    logger.info(f"Device count changed: {old_device_count} -> {new_device_count}")
                    
                if old_issues_count != new_issues_count:
                    logger.info(f"Devices with issues changed: {old_issues_count} -> {new_issues_count}")
                
                logger.debug(f"Successfully loaded {len(new_device_ids)} devices")
                return True
                
            except requests.RequestException as e:
                logger.warning(f"API request failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                
            except Exception as e:
                logger.error(f"Unexpected error fetching device data (attempt {attempt + 1}/{self.max_retries}): {e}")
                
            if attempt < self.max_retries - 1:
                wait_time = min(30, 2 ** attempt)  # Cap at 30 seconds
                time.sleep(wait_time)
        
        with self._lock:
            self._consecutive_failures += 1
            
        logger.error(f"Failed to fetch device data after {self.max_retries} attempts")
        return False
        
    def __del__(self):
        self.stop_auto_update()
