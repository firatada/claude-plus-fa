"""The voice host module (agent-voice-host.py, in the repo's root, installed beside the panel) for the experiment
tools here: its name has hyphens, so it is loaded by path."""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "agent_voice_host", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent-voice-host.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
HOST_DIR, VoiceHost = _mod.HOST_DIR, _mod.VoiceHost
