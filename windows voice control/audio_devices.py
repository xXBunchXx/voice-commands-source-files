"""
Audio output switching.

Windows has no documented API to *set* the default playback device, so we use
the well-known undocumented ``IPolicyConfig`` COM interface (the same one the
Sound control panel uses).  Everything here is wrapped in try/except so a
failure can never take down the engine — worst case a switch just no-ops.

Public API
----------
list_output_devices()  -> [(device_id, friendly_name), ...]   active outputs
set_default_output(id) -> bool                                switch default
"""
import comtypes
from ctypes import HRESULT, POINTER, c_void_p, c_longlong
from ctypes.wintypes import LPCWSTR, DWORD, BOOL
from comtypes import GUID, COMMETHOD, IUnknown

# ── IPolicyConfig ───────────────────────────────────────────────────────────
# Only SetDefaultEndpoint (the 11th method) is actually called — the earlier
# entries exist purely so its vtable offset is correct.
_CLSID_PolicyConfigClient = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")


class _IPolicyConfig(IUnknown):
    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    _methods_ = (
        COMMETHOD([], HRESULT, "GetMixFormat",
                  (["in"], LPCWSTR, "name"), (["out"], POINTER(c_void_p), "fmt")),
        COMMETHOD([], HRESULT, "GetDeviceFormat",
                  (["in"], LPCWSTR, "name"), (["in"], BOOL, "default"),
                  (["out"], POINTER(c_void_p), "fmt")),
        COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], LPCWSTR, "name")),
        COMMETHOD([], HRESULT, "SetDeviceFormat",
                  (["in"], LPCWSTR, "name"), (["in"], c_void_p, "ep"),
                  (["in"], c_void_p, "mix")),
        COMMETHOD([], HRESULT, "GetProcessingPeriod",
                  (["in"], LPCWSTR, "name"), (["in"], BOOL, "default"),
                  (["out"], POINTER(c_longlong), "def_"),
                  (["out"], POINTER(c_longlong), "min_")),
        COMMETHOD([], HRESULT, "SetProcessingPeriod",
                  (["in"], LPCWSTR, "name"), (["in"], c_void_p, "period")),
        COMMETHOD([], HRESULT, "GetShareMode",
                  (["in"], LPCWSTR, "name"), (["in"], c_void_p, "mode")),
        COMMETHOD([], HRESULT, "SetShareMode",
                  (["in"], LPCWSTR, "name"), (["in"], c_void_p, "mode")),
        COMMETHOD([], HRESULT, "GetPropertyValue",
                  (["in"], LPCWSTR, "name"), (["in"], BOOL, "fx"),
                  (["in"], c_void_p, "key"), (["in"], c_void_p, "val")),
        COMMETHOD([], HRESULT, "SetPropertyValue",
                  (["in"], LPCWSTR, "name"), (["in"], BOOL, "fx"),
                  (["in"], c_void_p, "key"), (["in"], c_void_p, "val")),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint",
                  (["in"], LPCWSTR, "name"), (["in"], DWORD, "role")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility",
                  (["in"], LPCWSTR, "name"), (["in"], BOOL, "visible")),
    )


def list_output_devices():
    """Return [(device_id, friendly_name), ...] for active playback devices."""
    from pycaw.pycaw import AudioUtilities
    out = []
    # Preferred: enumerate only active render (output) endpoints.
    try:
        from pycaw.constants import (CLSID_MMDeviceEnumerator, EDataFlow,
                                     DEVICE_STATE)
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        enum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
        coll = enum.EnumAudioEndpoints(EDataFlow.eRender.value,
                                       DEVICE_STATE.ACTIVE.value)
        for i in range(coll.GetCount()):
            try:
                adev = AudioUtilities.CreateDevice(coll.Item(i))
                if adev and adev.id and adev.FriendlyName:
                    out.append((adev.id, adev.FriendlyName))
            except Exception:
                continue
        if out:
            return out
    except Exception:
        pass
    # Fallback: every device (may include microphones — user just won't pick them)
    try:
        for adev in AudioUtilities.GetAllDevices():
            try:
                if adev.id and adev.FriendlyName and "Active" in str(adev.state):
                    out.append((adev.id, adev.FriendlyName))
            except Exception:
                continue
    except Exception:
        pass
    return out


def set_default_output(device_id: str) -> bool:
    """Make *device_id* the default playback device for all roles."""
    try:
        pc = comtypes.CoCreateInstance(
            _CLSID_PolicyConfigClient, _IPolicyConfig, comtypes.CLSCTX_ALL)
        for role in (0, 1, 2):   # eConsole, eMultimedia, eCommunications
            pc.SetDefaultEndpoint(device_id, role)
        return True
    except Exception as e:
        print(f"  Audio switch failed: {e}")
        return False
