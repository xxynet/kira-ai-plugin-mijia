import asyncio
import json
from typing import Optional

from core.agent.tool import ToolResult
from core.chat.message_elements import Image
from core.plugin import BasePlugin, logger, register

from .mijia_helper import DeviceIdentifier, MijiaController


class MijiaPlugin(BasePlugin):
    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)
        self._ctrl: Optional[MijiaController] = None
        self._enabled_sessions: list = []

    async def initialize(self):
        self._enabled_sessions = self.plugin_cfg.get("enabled_sessions", [])
        auth_path = self.plugin_cfg.get("auth_path") or ""
        default_home_id = self.plugin_cfg.get("default_home_id") or ""
        data_dir = self.ctx.get_plugin_data_dir()
        try:
            self._ctrl = MijiaController(
                auth_path=auth_path or None,
                default_home_id=default_home_id or None,
                data_dir=data_dir,
            )
            logger.info(f"Mijia plugin initialized, auth_path={self._ctrl.auth_path}")
        except Exception as e:
            logger.warning(f"Mijia plugin init failed: {e}")

    async def terminate(self):
        self._ctrl = None

    def _check_session(self, event) -> Optional[str]:
        """Return error string if session not allowed, None if OK."""
        if self._enabled_sessions and event.sid not in self._enabled_sessions:
            return "Permission denied: current session is not allowed to use Mijia devices"
        return None

    def _ensure_ctrl(self) -> MijiaController:
        if self._ctrl is None:
            raise RuntimeError(
                "Mijia plugin not initialized. Check plugin config and ensure mijiaAPI is installed."
            )
        return self._ctrl

    # --- Login ---

    @register.tool(
        "mijia_login",
        "Start Mijia QR code login. Returns a URL that the user should open and scan with Mijia app. "
        "Show the QR code to the user and wait for the user to confirm they have scanned it. "
        "Do not call mijia_login_check immediately after this tool.",
        {"type": "object", "properties": {}}
    )
    async def tool_login(self, _event) -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(ctrl.login_start)
            if result.get("status") == "already_logged_in":
                return "Already logged in, no need to scan QR code."
            qr_url = result.get('qr_url', '')
            return ToolResult(
                text=(
                    f"Please ask the user to scan the QR code with 米家 app:\n"
                    f"Wait for the user to confirm they have scanned it before calling mijia_login_check.\n"
                    f"Alternatively, you can send this QR Code image URL to the user if the user told that QR Code image did not appear:\n{qr_url}\n"
                ),
                attachments=[Image(qr_url)],
            )
        except Exception as e:
            return f"Login failed: {e}"

    @register.tool(
        "mijia_login_check",
        "Check whether the saved Mijia credentials are valid. Only call this after the user explicitly confirms "
        "they have scanned the QR code; never call it immediately after mijia_login. During an active QR login, "
        "it reports the current status.",
        {"type": "object", "properties": {}}
    )
    async def tool_login_check(self, _event) -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(ctrl.login_check)
            status = result.get("status")
            if status == "success":
                return "Login successful! You can now use other mijia tools."
            if status == "waiting_for_scan":
                return "Still waiting for QR code scan. Please scan the QR code first, then call this tool again."
            return f"Login status: {status}. {result.get('message', '')}"
        except Exception as e:
            return f"Error: {e}"

    # --- Tools ---

    @register.tool(
        "mijia_list_homes",
        "List all homes in the user's Mijia account",
        {"type": "object", "properties": {}}
    )
    async def tool_list_homes(self, _event) -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(ctrl.list_homes)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    @register.tool(
        "mijia_list_devices",
        "List Mijia smart home devices, optionally filtered by home_id",
        {
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "Home ID to filter devices. Omit to use default home or list all."},
                "include_shared": {"type": "boolean", "description": "Include shared devices", "default": False},
            },
        }
    )
    async def tool_list_devices(self, _event, home_id: str = "", include_shared: bool = False) -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(
                ctrl.list_devices,
                home_id=home_id or None,
                include_shared=include_shared,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    @register.tool(
        "mijia_device_status",
        "Get device properties and available actions. Use device_id or device_name.",
        {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device DID (preferred)"},
                "device_name": {"type": "string", "description": "Device name as shown in Mijia app"},
                "properties": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Property names to read, e.g. ['on', 'brightness']",
                },
                "include_metadata": {"type": "boolean", "description": "Include available properties/actions metadata", "default": True},
            },
        }
    )
    async def tool_device_status(
        self, _event, device_id: str = "", device_name: str = "",
        properties: list = None, include_metadata: bool = True,
    ) -> str:
        if err := self._check_session(_event):
            return err
        if not device_id and not device_name:
            return "Error: Provide device_id or device_name"
        try:
            ctrl = self._ensure_ctrl()
            ident = DeviceIdentifier(did=device_id or None, name=device_name or None)
            result = await asyncio.to_thread(
                ctrl.get_device_status,
                ident, properties=properties, include_metadata=include_metadata,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    @register.tool(
        "mijia_control_device",
        "Control a Mijia device: set a property or run an action.",
        {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device DID (preferred)"},
                "device_name": {"type": "string", "description": "Device name as shown in Mijia app"},
                "operation": {
                    "type": "string",
                    "enum": ["set_property", "run_action"],
                    "description": "Operation type",
                    "default": "set_property",
                },
                "prop_name": {"type": "string", "description": "Property name for set_property, e.g. 'on', 'brightness'"},
                "value": {"description": "Value to set, can be bool/int/float/string"},
                "action_name": {"type": "string", "description": "Action name for run_action"},
                "action_value": {"description": "Action parameter, e.g. [\"on\"]"},
                "action_kwargs": {"type": "object", "description": "Additional action parameters"},
            },
        }
    )
    async def tool_control_device(
        self, _event, device_id: str = "", device_name: str = "",
        operation: str = "set_property", prop_name: str = "", value=None,
        action_name: str = "", action_value=None, action_kwargs: dict = None,
    ) -> str:
        if err := self._check_session(_event):
            return err
        if not device_id and not device_name:
            return "Error: Provide device_id or device_name"
        try:
            ctrl = self._ensure_ctrl()
            ident = DeviceIdentifier(did=device_id or None, name=device_name or None)
            result = await asyncio.to_thread(
                ctrl.control_device,
                ident,
                operation=operation,
                prop_name=prop_name or None,
                value=value,
                action_name=action_name or None,
                action_value=action_value,
                action_kwargs=action_kwargs,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    @register.tool(
        "mijia_list_scenes",
        "List manual scenes for a Mijia home",
        {
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "Home ID. Omit to use default home."},
            },
        }
    )
    async def tool_list_scenes(self, _event, home_id: str = "") -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(ctrl.list_scenes, home_id=home_id or None)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    @register.tool(
        "mijia_run_scene",
        "Execute a Mijia scene by scene_id and home_id",
        {
            "type": "object",
            "properties": {
                "scene_id": {"type": "string", "description": "Scene ID from mijia_list_scenes"},
                "home_id": {"type": "string", "description": "Home ID where the scene belongs"},
            },
            "required": ["scene_id", "home_id"],
        }
    )
    async def tool_run_scene(self, _event, scene_id: str, home_id: str) -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(ctrl.run_scene, scene_id, home_id)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"

    @register.tool(
        "mijia_list_consumables",
        "Query consumable/filter status for Mijia devices",
        {
            "type": "object",
            "properties": {
                "home_id": {"type": "string", "description": "Home ID. Omit to use default home."},
            },
        }
    )
    async def tool_list_consumables(self, _event, home_id: str = "") -> str:
        if err := self._check_session(_event):
            return err
        try:
            ctrl = self._ensure_ctrl()
            result = await asyncio.to_thread(ctrl.list_consumables, home_id=home_id or None)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            return f"Error: {e}"
