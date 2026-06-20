"""Mijia API helper - adapted from MijiaAPI-MCP for KiraAI plugin."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import parse

import requests

from core.plugin import logger

from mijiaAPI import (
    mijiaAPI,
    mijiaDevice,
    DeviceNotFoundError,
    GetDeviceInfoError,
    LoginError,
    MultipleDevicesFoundError,
)


class MijiaAPIError(Exception):
    """Wrapper for Mijia API errors with user-friendly messages."""
    pass


def _resolve_auth_path(custom_path: Optional[str], data_dir: Optional[str] = None) -> str:
    if custom_path:
        path = Path(custom_path).expanduser().resolve()
    elif data_dir:
        path = Path(data_dir) / "auth.json"
    else:
        path = (Path.home() / ".config" / "mijia-api" / "auth.json").resolve()
    if path.is_dir():
        path = path / "auth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@dataclass
class DeviceIdentifier:
    did: Optional[str] = None
    name: Optional[str] = None


class MijiaController:
    def __init__(self, auth_path: Optional[str] = None, default_home_id: Optional[str] = None,
                 data_dir: Optional[str] = None) -> None:
        self.auth_path = _resolve_auth_path(auth_path, data_dir)
        self.default_home_id = default_home_id
        self._api: Optional[mijiaAPI] = None
        self._home_map: Optional[Dict[str, Dict[str, Any]]] = None
        self._login_state: Optional[str] = None
        self._login_api = None
        self._login_lp_url = None
        self._login_session = None
        self._login_headers = None

    def _ensure_api(self) -> mijiaAPI:
        if self._api is None:
            auth_file = Path(self.auth_path)
            if not auth_file.exists():
                raise RuntimeError(
                    f"Mijia auth file not found. "
                    "Please call mijia_login tool first."
                )
            api = mijiaAPI(auth_data_path=self.auth_path)
            try:
                api.login()  # refreshes session cookies, same as MCP
            except LoginError as e:
                raise RuntimeError(
                    "Mijia login failed or token expired. "
                    "Please call mijia_login tool to re-authenticate."
                ) from e
            self._patch_request(api)
            self._api = api
        return self._api

    @staticmethod
    def _patch_request(api: mijiaAPI):
        """Patch _request to log raw response on JSON parse failure."""
        import json as _json
        from mijiaAPI.miutils import decrypt, gen_nonce, get_signed_nonce, generate_enc_params

        def _request_with_logging(uri: str, data: dict, refresh_token: bool = True) -> dict:
            if refresh_token:
                api._refresh_token()
            url = api.api_base_url + uri
            params = {"data": _json.dumps(data, separators=(',', ':'))}
            nonce = gen_nonce()
            signed_nonce = get_signed_nonce(api.auth_data["ssecurity"], nonce)
            params = generate_enc_params(uri, "POST", signed_nonce, nonce, params, api.auth_data["ssecurity"])
            ret = api.session.post(url, data=params)
            try:
                ret_data = _json.loads(ret.text)
            except _json.JSONDecodeError:
                try:
                    dec_data = decrypt(api.auth_data["ssecurity"], nonce, ret.text)
                    ret_data = _json.loads(dec_data)
                except Exception:
                    logger.error(
                        f"Mijia API response parse failed.\n"
                        f"  URI: {uri}\n"
                        f"  HTTP Status: {ret.status_code}\n"
                        f"  Response headers: {dict(ret.headers)}\n"
                        f"  Raw response (first 500 chars): {ret.text[:500]}"
                    )
                    raise
            if ret_data.get("code", 0) != 0 or "result" not in ret_data:
                from mijiaAPI.errors import APIError
                raise APIError(ret_data["code"], ret_data.get("message", ret_data.get("desc", "unknown error")))
            return ret_data["result"]

        api._request = _request_with_logging

    def _ensure_home_map(self) -> Dict[str, Dict[str, Any]]:
        if self._home_map is None:
            api = self._ensure_api()
            homes = api.get_homes_list()
            self._home_map = {str(h["id"]): h for h in homes}
        return self._home_map

    @staticmethod
    def _wrap_error(e: Exception) -> MijiaAPIError:
        """Convert low-level errors to user-friendly messages."""
        if isinstance(e, GetDeviceInfoError):
            logger.error(f"Mijia GetDeviceInfoError: {e}")
            return MijiaAPIError(f"Failed to get device info: {e}")
        if isinstance(e, LoginError):
            logger.error(f"Mijia LoginError: {e}")
            return MijiaAPIError(
                "Mijia login failed or token expired. Please re-login with mijia_login."
            )
        if isinstance(e, (DeviceNotFoundError, MultipleDevicesFoundError)):
            logger.error(f"Mijia device error: {e}")
            return MijiaAPIError(str(e))
        if isinstance(e, RuntimeError):
            logger.error(f"Mijia runtime error: {e}")
            return MijiaAPIError(str(e))
        logger.error(f"Mijia API error: {e}", exc_info=True)
        return MijiaAPIError(f"Mijia API error: {e}")

    def login_start(self) -> Dict[str, Any]:
        """Start QR login and return the QR URL. Does NOT wait for scan."""
        self._login_api = mijiaAPI(auth_data_path=self.auth_path)
        location_data = self._login_api._get_location()
        if location_data.get("code", -1) == 0:
            self._login_api._save_auth_data()
            self._login_api._init_session()
            self._api = self._login_api
            self._login_state = "done"
            return {"status": "already_logged_in", "message": "Token is already valid"}

        location_data.update({
            "theme": "", "bizDeviceType": "", "_hasLogo": "false",
            "_qrsize": "240", "_dc": str(int(time.time() * 1000)),
        })
        url = self._login_api.login_url + "?" + parse.urlencode(location_data)
        headers = {
            "User-Agent": self._login_api.user_agent,
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "keep-alive",
        }
        login_ret = requests.get(url, headers=headers)
        login_data = self._login_api._handle_ret(login_ret)
        self._login_lp_url = login_data["lp"]
        self._login_session = requests.Session()
        self._login_headers = headers
        self._login_state = "waiting"
        return {
            "status": "waiting_for_scan",
            "qr_url": login_data["qr"],
            "message": "Please scan the QR code with Mijia app.",
        }

    def login_check(self) -> Dict[str, Any]:
        """Check if QR login has been completed (call after login_start)."""
        if self._login_state == "done":
            return {"status": "success", "message": "Login completed"}
        if self._login_state != "waiting":
            return {"status": "error", "message": "No login in progress, call mijia_login first"}
        try:
            lp_ret = self._login_session.get(
                self._login_lp_url, headers=self._login_headers, timeout=5,
            )
            lp_data = self._login_api._handle_ret(lp_ret)
            auth_keys = ["psecurity", "nonce", "ssecurity", "passToken", "userId", "cUserId"]
            for key in auth_keys:
                self._login_api.auth_data[key] = lp_data[key]
            callback_url = lp_data["location"]
            self._login_session.get(callback_url, headers=self._login_headers)
            cookies = self._login_session.cookies.get_dict()
            self._login_api.auth_data.update(cookies)
            from datetime import datetime, timedelta
            self._login_api.auth_data["expireTime"] = int(
                (datetime.now() + timedelta(days=30)).timestamp() * 1000
            )
            self._login_api._save_auth_data()
            self._login_api._init_session()
            self._api = self._login_api
            self._login_state = "done"
            return {"status": "success", "message": "Login successful"}
        except requests.exceptions.Timeout:
            return {"status": "waiting_for_scan", "message": "Still waiting for QR code scan..."}
        except Exception as e:
            self._login_state = None
            return {"status": "error", "message": str(e)}

    # --- Public API ---

    def list_homes(self) -> List[Dict[str, Any]]:
        try:
            api = self._ensure_api()
            return api.get_homes_list()
        except Exception as e:
            raise self._wrap_error(e) from e

    def list_devices(self, home_id: Optional[str] = None, include_shared: bool = False) -> List[Dict[str, Any]]:
        try:
            api = self._ensure_api()
            hid = home_id or self.default_home_id or None
            devices = api.get_devices_list(home_id=hid)
            if include_shared:
                devices.extend(api.get_shared_devices_list())
            home_map = self._ensure_home_map()
            for d in devices:
                h = home_map.get(str(d.get("home_id")))
                if h:
                    d["home_name"] = h.get("name")
            return devices
        except Exception as e:
            raise self._wrap_error(e) from e

    def get_device_status(
        self,
        identifier: DeviceIdentifier,
        properties: Optional[List[str]] = None,
        include_metadata: bool = True,
    ) -> Dict[str, Any]:
        try:
            api = self._ensure_api()
            device = mijiaDevice(api, did=identifier.did, dev_name=identifier.name)
            result: Dict[str, Any] = {
                "device": {"did": device.did, "name": device.name, "model": device.model},
            }
            if properties:
                result["properties"] = {p: device.get(p) for p in properties}
            if include_metadata:
                result["available_properties"] = {
                    name: {"desc": prop.desc, "rw": prop.rw, "type": prop.type, "range": prop.range, "value_list": prop.value_list}
                    for name, prop in device.prop_list.items()
                }
                result["available_actions"] = {
                    name: {"desc": action.desc}
                    for name, action in device.action_list.items()
                }
            return result
        except MijiaAPIError:
            raise
        except Exception as e:
            raise self._wrap_error(e) from e

    def control_device(
        self,
        identifier: DeviceIdentifier,
        operation: str,
        prop_name: Optional[str] = None,
        value: Any = None,
        action_name: Optional[str] = None,
        action_value: Optional[Any] = None,
        action_kwargs: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            api = self._ensure_api()
            device = mijiaDevice(api, did=identifier.did, dev_name=identifier.name)
            if operation == "set_property":
                if not prop_name:
                    raise ValueError("set_property requires prop_name")
                device.set(prop_name, value)
                return {"message": f"{device.name} set {prop_name} to {value}"}
            if operation == "run_action":
                if not action_name:
                    raise ValueError("run_action requires action_name")
                kwargs = action_kwargs or {}
                if action_value is not None:
                    device.run_action(action_name, value=action_value, **kwargs)
                else:
                    device.run_action(action_name, **kwargs)
                return {"message": f"{device.name} executed action {action_name}"}
            raise ValueError(f"Unsupported operation: {operation}")
        except MijiaAPIError:
            raise
        except Exception as e:
            raise self._wrap_error(e) from e

    def list_scenes(self, home_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            api = self._ensure_api()
            hid = home_id or self.default_home_id or None
            return api.get_scenes_list(home_id=hid)
        except Exception as e:
            raise self._wrap_error(e) from e

    def run_scene(self, scene_id: str, home_id: str) -> Dict[str, Any]:
        try:
            api = self._ensure_api()
            result = api.run_scene(scene_id, home_id)
            return {"result": result}
        except Exception as e:
            raise self._wrap_error(e) from e

    def list_consumables(self, home_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            api = self._ensure_api()
            hid = home_id or self.default_home_id or None
            return api.get_consumable_items(home_id=hid)
        except Exception as e:
            raise self._wrap_error(e) from e
